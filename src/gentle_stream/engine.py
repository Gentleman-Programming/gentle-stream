"""Real-time synthesis engine.

Renders one bar at a time: notes are scheduled into an accumulation buffer (their tails ring into later bars) and
the finished bar goes through a master chain whose filters and delay lines keep state between bars, so consecutive
bars join seamlessly and the stream can run forever.

What plays in each bar (song form, harmony, melody, counter-melody, bass line) comes from `composer.Composer`; how
it sounds comes from the style's genre (`genres`, lo-fi by default): its voices (procedural, or sampled once
`gentle-stream setup-samples` has installed the FluidR3_GM soundfont, `samples.SampleInstruments`), its rhythmic
treatment and drum patterns, and its master-chain settings (convolution reverb, room, vinyl, tape wow, tone, bus
compressor from `mastering`, final tape saturation). Style changes and "next" requests are queued from any thread
and applied at the next bar boundary.
"""
from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import genres
from . import samples as _samples
from .composer import Composer
from .genres.base import Piece
from .genres.lofi import Instruments  # noqa: F401  (re-exported: the procedural lo-fi voices)
from .mastering import Compressor, ConvolutionReverb, procedural_ir, soft_clip
from .style import DEFAULT, KEYS, Style
from .synth import SR, StatefulFilter, _sos, bp, hp, lp, mtof  # noqa: F401  (re-exported)

IR_SEED = 1
RAMP = 0.05   # seconds over which a changed master gain glides to its new value


@lru_cache(maxsize=16)
def _ir(space_tenths: int, sr: int) -> np.ndarray:
    return procedural_ir(space_tenths / 10, sr, IR_SEED)


# ---------------------------------------------------------------- engine
class Engine:
    HEADROOM = 24.0  # seconds of scheduling buffer: longest bar plus the longest note tail

    def __init__(self, style: Style = DEFAULT, seed: int | None = None, sr: int = SR,
                 samples: bool | str | Path = True):
        """`samples`: True uses the installed soundfont when FluidSynth is available, a path uses that soundfont,
        False keeps the procedural instruments. Anything unavailable falls back to procedural."""
        self.sr = sr
        sf2 = _samples.soundfont_path() if samples is True else (Path(samples) if samples else None)
        renderer = _samples.load_renderer(sf2, sr) if sf2 is not None else None
        self._sampled = _samples.SampleInstruments(renderer, np.random.default_rng(0), sr) if renderer else None
        self._lock = threading.Lock()
        self._pending_style: Style | None = None
        self._pending_next = False
        self._root_rng = np.random.default_rng(seed)
        self._acc = np.zeros((int(self.HEADROOM * sr), 2))
        self.samples_out = 0
        self.piece = 0
        # master chain state
        self._room_taps = ((.031, .28), (.057, .24), (.083, .2), (.127, .17), (.191, .13), (.263, .1), (.347, .07))
        self._room_hist = np.zeros((int(0.36 * sr), 2))
        self._room_lp = StatefulFilter(_sos("lowpass", 2600, 0, sr), 2)
        self._wow_hist = np.zeros((int(0.006 * sr), 2))
        self._crackle_hp = StatefulFilter(_sos("highpass", 1500, 0, sr), 2)
        self._hiss_lp = StatefulFilter(_sos("lowpass", 6000, 0, sr), 2)
        self._tone_cut = None
        self._tone_lp: StatefulFilter | None = None
        self._send_hp = StatefulFilter(_sos("highpass", 180, 0, sr), 2)
        self._ir_space: int | None = None
        self._reverb: ConvolutionReverb | None = None
        self._comp = Compressor(sr)
        self._last: dict[str, float] = {}   # master gains of the previous bar (see `_glide`)
        self._start_piece(style)

    # ------------------------------------------------------------ control (any thread)
    def queue_style(self, style: Style) -> None:
        """Play `style` from the next bar on (a new piece)."""
        with self._lock:
            self._pending_style, self._pending_next = style, False

    def queue_next(self) -> None:
        """Start a new piece with the same mood from the next bar on."""
        with self._lock:
            if self._pending_style is None:
                self._pending_next = True

    @property
    def backend(self) -> str:
        return "procedural" if self._sampled is None else "samples"

    @property
    def bar_seconds(self) -> float:
        return 240.0 / self.style.bpm

    # ------------------------------------------------------------ piece setup
    def _start_piece(self, style: Style) -> None:
        self.style = style
        self.genre = genres.get(style.genre)
        self.seed = int(self._root_rng.integers(2 ** 31))
        self.rng = np.random.default_rng(self.seed)
        self.inst = self.genre.instruments(self.rng, self.sr, self._sampled)
        self.piece += 1
        self.bar = 0
        ton = 48 + KEYS.index(style.key)
        self.ton = ton - 12 if ton > 54 else ton
        self.composer = Composer(style, self.seed, self.ton, self.genre.chord)
        self.section = self.composer.plan(0).section
        self._piece = Piece(style, self.rng, self.inst, self.ton, self.sr, self._acc)
        self.genre.start(self._piece)

    # ------------------------------------------------------------ rendering
    def render_bar(self) -> np.ndarray:
        """Render the next bar as float32 stereo samples of shape (n, 2)."""
        with self._lock:
            style, nxt = self._pending_style, self._pending_next
            self._pending_style, self._pending_next = None, False
        if style is not None:
            self._start_piece(style)
        elif nxt:
            self._start_piece(self.style.variation(self._root_rng))
        self._schedule_bar()
        n = int(round(self.bar_seconds * self.sr))
        out = self._acc[:n].copy()
        self._acc[:-n] = self._acc[n:]
        self._acc[-n:] = 0
        out = self._master(out)
        self.bar += 1
        self.samples_out += n
        return out.astype(np.float32)

    def _schedule_bar(self) -> None:
        p = self.composer.plan(self.bar)
        self.section = p.section
        self.genre.schedule(self._piece, p)

    def _glide(self, name: str, value: float, n: int, span: int) -> np.ndarray | float:
        """`value`, or an (n, 1) ramp to it over the first `span` samples when it changed since the last bar."""
        prev = self._last.get(name, value)
        self._last[name] = value
        if prev == value:
            return value
        k = min(n, span)
        return np.concatenate([np.linspace(prev, value, k, endpoint=False), np.full(n - k, value)])[:, None]

    def _master(self, x: np.ndarray) -> np.ndarray:
        m, n, sr = self.genre.master(self.style), len(x), self.sr
        span = int(RAMP * sr)
        # convolution reverb send (the music only; the tail rings on across bars and style changes)
        space = int(round(m.space * 10))
        if space != self._ir_space:
            if self._reverb is None:
                self._reverb = ConvolutionReverb(_ir(space, sr))
            else:
                self._reverb.set_ir(_ir(space, sr))
            self._ir_space = space
        verb = self._reverb(self._send_hp(x)) * self._glide("reverb", m.reverb, n, span)
        # vinyl crackle and hiss
        if m.vinyl > 0:
            cr = np.zeros((n, 2))
            hits = self.rng.integers(0, n, (int(n / sr * 28 * m.vinyl) + 1, 2))
            cr[hits[:, 0], 0] = self.rng.uniform(-1, 1, len(hits))
            cr[hits[:, 1], 1] = self.rng.uniform(-1, 1, len(hits))
            x = x + (self._crackle_hp(cr) * 0.22 + self._hiss_lp(self.rng.standard_normal((n, 2))) * 0.004) * m.vinyl
        # room: early-reflection taps over the dry history
        H = len(self._room_hist)
        ext = np.concatenate([self._room_hist, x])
        wet = np.zeros_like(x)
        for d, g in self._room_taps:
            k = int(d * sr)
            wet += ext[H - k:H - k + n] * g
        self._room_hist = ext[-H:]
        y = x + self._room_lp(wet) * self._glide("room", m.room, n, span) + verb
        # tape wow and flutter: a slowly modulated fractional delay, continuous in absolute time; a changed depth
        # glides over the whole bar so the delay never jumps
        W = len(self._wow_hist)
        ext = np.concatenate([self._wow_hist, y])
        t = (self.samples_out + np.arange(n)) / sr
        w = self._glide("wow", m.wow, n, n)
        w = w[:, 0] if isinstance(w, np.ndarray) else w
        d = (0.0025 + 0.0016 * w * np.sin(2 * np.pi * 0.5 * t) + 0.0005 * w * np.sin(2 * np.pi * 3.3 * t + 1)) * sr
        pos = W + np.arange(n) - d
        grid = np.arange(len(ext))
        y = np.stack([np.interp(pos, grid, ext[:, c]) for c in (0, 1)], 1)
        self._wow_hist = ext[-W:]
        # tone: darker or more open tape; a new cutoff crossfades from the old filter, whose output stays continuous
        cut = int(m.tone)
        old = None
        if cut != self._tone_cut:
            new = StatefulFilter(_sos("lowpass", cut, 0, sr), 2)
            if self._tone_lp is not None:
                new.zi = self._tone_lp.zi.copy()
                old = self._tone_lp
            self._tone_lp, self._tone_cut = new, cut
        if old is None:
            y = self._tone_lp(y)
        else:
            k = min(n, span)
            fade = np.linspace(0, 1, k, endpoint=False)[:, None]
            faded = old(y[:k])
            y = self._tone_lp(y)
            y[:k] = faded + (y[:k] - faded) * fade
        if self.samples_out < 0.6 * sr:  # fade in at the very start of the stream
            y *= np.clip((self.samples_out + np.arange(n)) / (0.6 * sr), 0, 1)[:, None]
        self._comp.threshold, self._comp.ratio = m.threshold, m.ratio
        drive = self._glide("drive", m.drive, n, span)
        return soft_clip(self._comp(y), drive, 0.9)
