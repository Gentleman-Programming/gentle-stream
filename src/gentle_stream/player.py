"""Real-time playback: a producer thread renders bars ahead of time and the audio callback only copies samples.

The callback never synthesizes. It pulls from a short queue of rendered bars, applies a smoothed gain (so play,
pause and volume never click) and records level/spectrum data for the TUI. "Now playing" follows the bar that is
actually audible, not the one being rendered.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import numpy as np

from .engine import SR, Engine, Track
from .style import Style

RAMP_SECONDS = 0.03
CROSSFADE_SECONDS = 0.08
SPECTRUM_SIZE = 8192                   # ~0.19 s of output: fine enough for narrow low bands
SPECTRUM_RANGE = (50.0, 10000.0)       # Hz covered by the bar; the master low-pass leaves little above
SPECTRUM_MIN_BINS = 3.0                # narrow low bands average at least this many FFT bins
SPECTRUM_TILT = 3.0                    # dB per octave around 1 kHz: pink noise (and most music) reads flat
SPECTRUM_FLOOR, SPECTRUM_DB = -96.0, 60.0   # dB shown as empty, and the dB span up to a full column
SPECTRUM_FALL = 0.12                   # how much a column may drop per call; rises are immediate


@dataclass(frozen=True)
class NowPlaying:
    style: Style
    piece: int
    bar: int
    playlist_name: str | None = None
    playlist_index: int | None = None
    playlist_revision: int | None = None  # local identity, not part of the wire state
    track: Track | None = field(default=None, repr=False, compare=False)


@dataclass
class _Chunk:
    audio: np.ndarray
    meta: NowPlaying


def settle(level: np.ndarray, shown: np.ndarray) -> np.ndarray:
    """Spectrum columns rise at once and fall by at most SPECTRUM_FALL from what was `shown` last time."""
    return np.maximum(level, shown - SPECTRUM_FALL) if shown.shape == level.shape else level


def _default_stream(**kw):
    import sounddevice as sd

    return sd.OutputStream(**kw)


class Player:
    def __init__(self, engine: Engine, stream_factory=_default_stream, prebuffer: float = 1.0,
                 blocksize: int = 1024, volume: float = 0.8):
        self.engine = engine
        self._factory = stream_factory
        self._prebuffer = int(prebuffer * engine.sr)
        self._blocksize = blocksize
        self._lock = threading.Lock()          # guards the chunk queue (held briefly by the callback)
        self._render_lock = threading.Lock()   # one bar render + enqueue at a time; next() waits for it
        self._chunks: deque[_Chunk] = deque()
        self._offset = 0                 # read position inside the first chunk
        self._buffered = 0               # samples queued and not yet played
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._producer = threading.Thread(target=self._produce, name="gentle-stream-producer", daemon=True)
        self._stream = None
        self._stream_ticket = None
        self._control_lock = threading.RLock()
        self._callback_lock = threading.Lock()
        self._restart_error = None
        self.playing = True
        self.volume = float(np.clip(volume, 0, 1))
        self._gain = 0.0
        self.position = 0                # samples played since start
        self.underruns = 0
        self.level = 0.0
        self._tail = np.zeros(SPECTRUM_SIZE, dtype=np.float32)
        self._window = np.hanning(SPECTRUM_SIZE).astype(np.float32)
        self._shown = np.zeros(0)        # last spectrum() result, for the gentle fall
        self.now = NowPlaying(engine.style, engine.piece, 0, track=engine.track)
        self._selection: tuple[int, int] | None = None  # explicit index and earliest matching audible piece

    # ------------------------------------------------------------ lifecycle
    def _activate_stream(self, stream, ticket) -> None:
        with self._callback_lock:
            self._stream, self._stream_ticket = stream, ticket

    def _retire_stream(self, *, cut: bool) -> None:
        stream = self._stream
        self._activate_stream(None, None)
        if stream is not None:
            try:
                abort = getattr(stream, "abort", None) if cut else None
                (abort or stream.stop)()
            finally:
                stream.close()

    def start(self) -> None:
        """Open the output stream and start rendering; calling it again is a no-op."""
        with self._control_lock:
            if self._stopping.is_set():
                raise ValueError("player is closed")
            if self._stream is not None:
                return
            ticket = object()
            stream = self._factory(samplerate=self.engine.sr, channels=2, dtype="float32",
                                   blocksize=self._blocksize, latency="high",
                                   callback=lambda *args: self._callback(*args, ticket=ticket))
            try:
                if self._producer.ident is None:
                    self._producer.start()
                deadline = time.monotonic() + 5
                while self._buffered == 0 and self._producer.is_alive() and time.monotonic() < deadline:
                    time.sleep(0.005)    # the first bar is ready before the stream starts pulling
                stream.start()
            except Exception:
                stream.close()
                raise
            self._activate_stream(stream, ticket)
            self._restart_error = None

    def close(self) -> None:
        with self._control_lock:
            self._stopping.set()
            self._wake.set()
            try:
                self._retire_stream(cut=False)
            finally:
                if self._producer.is_alive():
                    self._producer.join(timeout=5)

    transport_parity_supported = True

    # ------------------------------------------------------------ controls
    def panic(self) -> None:
        """Cut and restart the audible song without changing volume or pause state."""
        with self._control_lock:
            if self._stopping.is_set():
                raise ValueError("player is closed")
            try:
                self._retire_stream(cut=True)
                with self._render_lock, self._lock:
                    self.engine.restart(self.now.track)
                    self._chunks.clear()
                    self._offset = self._buffered = self.position = 0
                    self.level = self._gain = 0.0
                    self._tail[:] = 0
                    self._shown = np.zeros(0)
                    self.now = replace(self._metadata(), bar=0)
                    self._selection = None if self.playlist_index is None else (self.playlist_index, self.now.piece)
                self._wake.set()
                self.start()
            except Exception as error:
                message = f"Panic restart failed: {error}"
                if self._stream is None:
                    self.playing = False
                    self._restart_error = message
                raise ValueError(message) from error

    def set_continuous(self, enabled: bool) -> None:
        with self._render_lock:
            self.engine.set_continuous(enabled)

    def play(self) -> None:
        with self._control_lock:
            if self._restart_error:
                raise ValueError(self._restart_error + "; use Panic to retry")
            self.playing = True

    def pause(self) -> None:
        with self._control_lock:
            self.playing = False

    def toggle(self) -> None:
        with self._control_lock:
            self.pause() if self.playing else self.play()

    def set_volume(self, volume: float) -> None:
        self.volume = float(np.clip(volume, 0, 1))

    def set_style(self, style: Style) -> None:
        """The new style starts at the next rendered bar, after the audio already buffered."""
        with self._render_lock:
            self.engine.queue_style(style)
            self._selection = None

    def set_playlist(self, styles: Sequence[Style], start_index: int = 0, name: str = "",
                     continuous: bool = False) -> None:
        """Queue the selected song after buffered audio, preserving play/pause."""
        with self._render_lock:
            self.engine.set_playlist(styles, start_index, name, continuous)
            self._selection = (start_index, self.engine.piece + 1)
        self._wake.set()

    @property
    def playlist_name(self) -> str | None:
        return self.engine.playlist_name

    @property
    def playlist_index(self) -> int | None:
        """Selected/rendered cursor; now.playlist_index follows consumed audio."""
        return self.engine.playlist_index

    @property
    def continuous(self) -> bool:
        return self.engine.continuous

    def _metadata(self) -> NowPlaying:
        return NowPlaying(self.style, self.engine.piece, self.engine.bar - 1,
                          self.playlist_name, self.playlist_index, self.engine.playlist_revision, self.engine.track)

    def next(self) -> None:
        """Crossfade immediately to the next continuous-playlist entry or a same-mood variation."""
        self._navigate()

    def previous(self) -> None:
        """Crossfade immediately to the previous entry, or do nothing without a playlist."""
        self._navigate(previous=True)

    def _navigate(self, previous: bool = False) -> None:
        with self._render_lock:  # no old bar can be queued after this
            if previous and self.playlist_index is None:
                return
            navigating = previous or self.continuous
            if navigating:
                # Producer lookahead can already have crossed the form boundary. Use the
                # consumed song, unless a newer explicit selection is still unheard.
                with self._lock:
                    now = self.now
                index = None
                if self._selection is not None:
                    index, piece = self._selection
                    if now.playlist_revision == self.engine.playlist_revision and now.piece >= piece:
                        index = now.playlist_index
                if previous:
                    self.engine.queue_previous(from_index=index)
                else:
                    self.engine.queue_next(from_index=index)
            else:
                self.engine.queue_next()
            audio = self.engine.render_bar()
            meta = self._metadata()
            if navigating:
                self._selection = (meta.playlist_index, meta.piece)
            fade = min(int(CROSSFADE_SECONDS * self.engine.sr), len(audio))
            ramp = np.linspace(0, 1, fade, dtype=np.float32)[:, None]
            audio[:fade] *= ramp
            with self._lock:
                old = self._pending(fade)
                audio[:len(old)] += old * ramp[::-1][:len(old)]
                self._chunks.clear()
                self._chunks.append(_Chunk(audio, meta))
                self._offset = 0
                self._buffered = len(audio)
        self._wake.set()

    def _pending(self, n: int) -> np.ndarray:
        """The next `n` queued samples (fewer if less is queued), without consuming them. Caller holds _lock."""
        parts, need, offset = [], n, self._offset
        for c in self._chunks:
            part = c.audio[offset:offset + need]
            parts.append(part)
            need -= len(part)
            offset = 0
            if need <= 0:
                break
        return np.concatenate(parts) if parts else np.zeros((0, 2), dtype=np.float32)

    @property
    def buffered_seconds(self) -> float:
        return self._buffered / self.engine.sr

    @property
    def style(self) -> Style:
        """The style being rendered, which may not be audible yet: the base for the next mood."""
        return self.engine.style

    def playback_state(self) -> dict:
        """One snapshot of the audible and render-side metadata for a daemon response."""
        with self._render_lock:
            with self._lock:
                now = self.now
            return {
                "now": {"style": now.style.to_dict(), "piece": now.piece, "bar": now.bar,
                        "playlist_name": now.playlist_name, "playlist_index": now.playlist_index},
                "style": self.style.to_dict(),
                "playlist_name": self.playlist_name,
                "playlist_index": self.playlist_index,
                "continuous": self.continuous,
            }

    def detach(self) -> None:
        """Leave the music playing without this app; an in-process player cannot outlive it, so it closes."""
        self.close()

    def spectrum(self, bands: int = 32) -> np.ndarray:
        """`spectrum_levels`, with columns that rise at once and fall gently between calls. Runs on the UI thread."""
        self._shown = settle(self.spectrum_levels(bands), self._shown)
        return self._shown

    def spectrum_levels(self, bands: int = 32) -> np.ndarray:
        """Log-spaced band levels of the latest output, scaled to 0..1 for display.

        Each band is the mean power over its frequency range, integrated from the FFT with fractional bins, so a
        band narrower than one bin still gets a value and wide high bands are averaged instead of merged. A tilt
        of SPECTRUM_TILT dB/octave makes pink noise flat. Stateless, so any thread may call it.
        """
        df = self.engine.sr / SPECTRUM_SIZE
        x = self._tail * self._window
        power = np.abs(np.fft.rfft(x)) ** 2 * (2 / self._window.sum()) ** 2
        # bin k covers [(k - 0.5) df, (k + 0.5) df]; interpolating the running sum integrates fractional bins
        cum = np.concatenate([[0.0], np.cumsum(power)])
        cum_freqs = (np.arange(len(cum)) - 0.5) * df
        edges = np.geomspace(*SPECTRUM_RANGE, bands + 1)
        centers = np.sqrt(edges[:-1] * edges[1:])
        width = np.maximum(np.diff(edges), SPECTRUM_MIN_BINS * df)
        lo = (edges[:-1] + edges[1:] - width) / 2
        hi = lo + width
        mean = (np.interp(hi, cum_freqs, cum) - np.interp(lo, cum_freqs, cum)) / ((hi - lo) / df)
        db = 10 * np.log10(mean + 1e-12) + SPECTRUM_TILT * np.log2(centers / 1000)
        return np.clip((db - SPECTRUM_FLOOR) / SPECTRUM_DB, 0, 1)

    # ------------------------------------------------------------ threads
    def _produce(self) -> None:
        while not self._stopping.is_set():
            if self._buffered < self._prebuffer:
                with self._render_lock:
                    audio = self.engine.render_bar()
                    meta = self._metadata()
                    with self._lock:
                        self._chunks.append(_Chunk(audio, meta))
                        self._buffered += len(audio)
            else:
                self._wake.wait(0.05)
                self._wake.clear()

    def _pull(self, frames: int) -> np.ndarray:
        out = np.zeros((frames, 2), dtype=np.float32)
        got = 0
        with self._lock:
            while got < frames and self._chunks:
                c = self._chunks[0]
                take = min(frames - got, len(c.audio) - self._offset)
                out[got:got + take] = c.audio[self._offset:self._offset + take]
                self.now = c.meta
                got += take
                self._offset += take
                if self._offset >= len(c.audio):
                    self._chunks.popleft()
                    self._offset = 0
            self._buffered -= got
        if got < frames:
            self.underruns += 1
        self.position += frames
        return out

    def _callback(self, outdata, frames, time_info, status, *, ticket=None) -> None:
        with self._callback_lock:
            if ticket is not None and ticket is not self._stream_ticket:
                outdata[:] = 0
                return
            self._render_callback(outdata, frames)

    def _render_callback(self, outdata, frames) -> None:
        target = self.volume if self.playing else 0.0
        if self._gain == 0.0 and target == 0.0 and not self.playing:
            outdata[:] = 0
            self.level = 0.0
            self._tail[:] = 0
            return
        audio = self._pull(frames)
        self._wake.set()
        rate = 1.0 / (RAMP_SECONDS * self.engine.sr)
        diff = target - self._gain
        steps = np.minimum(np.arange(1, frames + 1) * rate, abs(diff))
        gain = (self._gain + np.sign(diff) * steps).astype(np.float32)
        self._gain = float(gain[-1]) if abs(gain[-1] - target) > 1e-6 else target
        outdata[:] = audio * gain[:, None]
        mono = outdata.mean(axis=1)
        self.level = float(np.sqrt(np.mean(mono ** 2)))
        n = min(frames, SPECTRUM_SIZE)
        self._tail = np.concatenate([self._tail[n:], mono[-n:]])
