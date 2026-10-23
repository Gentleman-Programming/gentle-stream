"""Real-time playback: a producer thread renders bars ahead of time and the audio callback only copies samples.

The callback never synthesizes. It pulls from a short queue of rendered bars, applies a smoothed gain (so play,
pause and volume never click) and records level/spectrum data for the TUI. "Now playing" follows the bar that is
actually audible, not the one being rendered.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from .engine import SR, Engine
from .style import Style

RAMP_SECONDS = 0.03
CROSSFADE_SECONDS = 0.08
SPECTRUM_SIZE = 2048


@dataclass(frozen=True)
class NowPlaying:
    style: Style
    piece: int
    bar: int


@dataclass
class _Chunk:
    audio: np.ndarray
    meta: NowPlaying


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
        self.playing = True
        self.volume = float(np.clip(volume, 0, 1))
        self._gain = 0.0
        self.position = 0                # samples played since start
        self.underruns = 0
        self.level = 0.0
        self._tail = np.zeros(SPECTRUM_SIZE, dtype=np.float32)
        self.now = NowPlaying(engine.style, engine.piece, 0)

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        """Open the output stream and start rendering; calling it again is a no-op."""
        if self._stream is not None:
            return
        self._stream = self._factory(samplerate=self.engine.sr, channels=2, dtype="float32",
                                     blocksize=self._blocksize, latency="high", callback=self._callback)
        self._producer.start()
        deadline = time.monotonic() + 5
        while self._buffered == 0 and self._producer.is_alive() and time.monotonic() < deadline:
            time.sleep(0.005)    # the first bar is ready before the device starts pulling
        self._stream.start()

    def close(self) -> None:
        self._stopping.set()
        self._wake.set()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
        if self._producer.is_alive():
            self._producer.join(timeout=5)

    # ------------------------------------------------------------ controls
    def play(self) -> None:
        self.playing = True

    def pause(self) -> None:
        self.playing = False

    def toggle(self) -> None:
        self.playing = not self.playing

    def set_volume(self, volume: float) -> None:
        self.volume = float(np.clip(volume, 0, 1))

    def set_style(self, style: Style) -> None:
        """The new style starts at the next rendered bar, after the audio already buffered."""
        self.engine.queue_style(style)

    def next(self) -> None:
        """A new piece with the same mood, right away: its first bar is rendered now and crossfaded over the
        pending audio of the old piece, which is dropped. No gap, no jump."""
        with self._render_lock:  # waits for a bar being rendered, so no old bar can be queued after this
            self.engine.queue_next()
            audio = self.engine.render_bar()
            meta = NowPlaying(self.engine.style, self.engine.piece, self.engine.bar - 1)
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

    def spectrum(self, bands: int = 32) -> np.ndarray:
        """Log-spaced band magnitudes of the latest output, scaled to 0..1 for display."""
        x = self._tail.copy() * np.hanning(SPECTRUM_SIZE)
        mag = np.abs(np.fft.rfft(x))
        freqs = np.fft.rfftfreq(SPECTRUM_SIZE, 1 / self.engine.sr)
        edges = np.geomspace(40, 12000, bands + 1)
        out = np.zeros(bands)
        for i in range(bands):
            sel = mag[(freqs >= edges[i]) & (freqs < edges[i + 1])]
            if sel.size:
                out[i] = sel.max()
        db = 20 * np.log10(out + 1e-9)
        return np.clip((db + 20) / 60, 0, 1)

    # ------------------------------------------------------------ threads
    def _produce(self) -> None:
        while not self._stopping.is_set():
            if self._buffered < self._prebuffer:
                with self._render_lock:
                    audio = self.engine.render_bar()
                    meta = NowPlaying(self.engine.style, self.engine.piece, self.engine.bar - 1)
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

    def _callback(self, outdata, frames, time_info, status) -> None:
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
