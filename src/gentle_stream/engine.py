"""Real-time lo-fi synthesis engine.

A streaming port of the procedural `lofi_mel.py` bed used for the YouTube videos (felt piano / rhodes / nylon /
pad, sub bass, dusty swung drums, vinyl, room, tape wow). Instead of rendering a whole file, the engine renders
one bar at a time: notes are scheduled into an accumulation buffer (their tails ring into later bars) and the
finished bar goes through a master chain whose filters and delay lines keep state between bars, so consecutive
bars join seamlessly and the stream can run forever.

What plays in each bar (song form, harmony, melody, counter-melody, bass line) comes from `composer.Composer`;
this module turns that plan into sound. Style changes and "next" requests are queued from any thread and applied
at the next bar boundary.
"""
from __future__ import annotations

import threading
from functools import lru_cache

import numpy as np
from scipy.signal import butter, sosfilt

from .composer import Composer
from .style import DEFAULT, KEYS, Style

SR = 44100
KICK_PATTERNS = ((0, 2), (0, 1.5), (0, 2.5), (0, 1.5, 2))  # quarter-note beats, 0-based
MASTER_GAIN = 1.6


@lru_cache(maxsize=256)
def _sos(kind: str, f1: int, f2: int = 0, sr: int = SR):
    return butter(2, [f1, f2] if kind == "bandpass" else f1, kind, fs=sr, output="sos")


def lp(x, f, sr=SR):
    return sosfilt(_sos("lowpass", int(f), 0, sr), x)


def hp(x, f, sr=SR):
    return sosfilt(_sos("highpass", int(f), 0, sr), x)


def bp(x, a, b, sr=SR):
    return sosfilt(_sos("bandpass", int(a), int(b), sr), x)


def mtof(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


class StatefulFilter:
    """Second-order filter applied block by block along axis 0, carrying its state across blocks."""

    def __init__(self, sos, channels: int):
        self.sos = sos
        self.zi = np.zeros((sos.shape[0], 2, channels))

    def __call__(self, x):
        y, self.zi = sosfilt(self.sos, x, axis=0, zi=self.zi)
        return y


# ---------------------------------------------------------------- instruments
class Instruments:
    def __init__(self, rng: np.random.Generator, sr: int = SR):
        self.rng, self.sr = rng, sr

    def _t(self, n):
        return np.arange(n) / self.sr

    def piano(self, m, dur, vel=1.0):
        f = mtof(m); n = int((dur + 0.35) * self.sr); t = self._t(n); s = np.zeros(n)
        for k in range(1, 9):
            fk = k * f * np.sqrt(1 + 0.0004 * k * k)
            if fk > 9000:
                break
            s += np.sin(2 * np.pi * fk * t + self.rng.random()) * k ** -1.6 * np.exp(-t * (0.55 + 0.35 * k))
        s += lp(self.rng.standard_normal(n), 900) * np.exp(-t * 60) * 0.05        # soft felt hammer
        env = np.minimum(1, t / 0.006) * np.clip((dur + 0.35 - t) / 0.35, 0, 1)
        return lp(s * env, 100 * round(22 + 6 * vel)) * vel

    def rhodes(self, m, dur, vel=1.0):
        f = mtof(m); n = int((dur + 0.3) * self.sr); t = self._t(n)
        s = (np.sin(2 * np.pi * f * t + 0.45 * np.sin(2 * np.pi * f * t) * np.exp(-t * 3))
             + 0.18 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t * 2.2))
        env = np.minimum(1, t / 0.008) * np.exp(-t * 0.8) * np.clip((dur + 0.3 - t) / 0.3, 0, 1)
        return s * env * (1 + 0.12 * np.sin(2 * np.pi * 4.2 * t)) * vel

    def nylon(self, m, dur, vel=1.0):
        """Karplus-Strong vectorized one period per step: soft thumb pluck, dark body."""
        f = mtof(m); p = max(2, int(self.sr / f)); n = int((dur + 0.6) * self.sr)
        blk = lp(self.rng.standard_normal(p), 2600); out = np.empty(((n // p) + 2) * p)
        for k in range(len(out) // p):
            out[k * p:(k + 1) * p] = blk
            blk = 0.9955 * 0.5 * (blk + np.roll(blk, -1))
        out = out[:n]; t = self._t(n)
        return bp(out, 90, 4200) * np.exp(-t * 1.4) * np.minimum(1, t / 0.003) * vel * 1.4

    def pad(self, m, dur, vel=1.0):
        f = mtof(m); n = int((dur + 0.9) * self.sr); t = self._t(n); s = np.zeros(n)
        for det in (-0.12, 0.0, 0.11):
            for k in range(1, 9):
                s += np.sin(2 * np.pi * k * f * 2 ** (det / 12) * t + self.rng.random() * 6) / k
        env = np.clip(t / 0.7, 0, 1) * np.clip((dur + 0.9 - t) / 0.9, 0, 1)
        return lp(s * env, 1100) * vel * 0.22

    def sub(self, m, dur):
        n = int(dur * self.sr); t = self._t(n)
        return ((np.sin(2 * np.pi * mtof(m) * t) + 0.12 * np.sin(4 * np.pi * mtof(m) * t))
                * np.minimum(1, t / 0.02) * np.clip((dur - t) / 0.12, 0, 1))

    def kick(self):
        n = int(.45 * self.sr); t = self._t(n); f = 46 + 55 * np.exp(-t * 32)
        return np.sin(2 * np.pi * np.cumsum(f) / self.sr) * np.exp(-t * 7.5)

    def brush(self):
        n = int(.38 * self.sr); t = self._t(n)
        return (bp(self.rng.standard_normal(n), 1400, 6500) * np.minimum(1, t / 0.012) * np.exp(-t * 9)
                + np.sin(2 * np.pi * 190 * t) * np.exp(-t * 30) * 0.25)

    def rim(self):
        n = int(.08 * self.sr); t = self._t(n)
        return bp(self.rng.standard_normal(n), 1600, 3600) * np.exp(-t * 90) * 1.6

    def hat(self, g=1.0):
        n = int(.07 * self.sr); t = self._t(n)
        return lp(hp(self.rng.standard_normal(n), 7000), 10000) * np.exp(-t * 75) * g


# ---------------------------------------------------------------- engine
class Engine:
    HEADROOM = 24.0  # seconds of scheduling buffer: longest bar plus the longest note tail

    def __init__(self, style: Style = DEFAULT, seed: int | None = None, sr: int = SR):
        self.sr = sr
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
    def bar_seconds(self) -> float:
        return 240.0 / self.style.bpm

    # ------------------------------------------------------------ piece setup
    def _start_piece(self, style: Style) -> None:
        self.style = style
        self.seed = int(self._root_rng.integers(2 ** 31))
        self.rng = np.random.default_rng(self.seed)
        self.inst = Instruments(self.rng, self.sr)
        self.piece += 1
        self.bar = 0
        ton = 48 + KEYS.index(style.key)
        self.ton = ton - 12 if ton > 54 else ton
        self.composer = Composer(style, self.seed, self.ton)
        self.section = self.composer.plan(0).section
        self.kick_beats = (0, 2) if style.drums == "soft" else KICK_PATTERNS[int(self.rng.integers(len(KICK_PATTERNS)))]
        self.center = self.ton + 19

    def _voice(self, iv: list[int]) -> list[int]:
        """Upper chord tones (no root) placed closest to the previous voicing: smooth voice leading."""
        out = []
        for x in iv[1:]:
            m = self.ton + 12 + x
            while m - self.center > 6:
                m -= 12
            while self.center - m > 6:
                m += 12
            out.append(m)
        return sorted(set(out))

    # ------------------------------------------------------------ rendering
    def _add(self, sig, t: float, pan: float = 0.0, g: float = 1.0) -> None:
        i = int(t * self.sr)
        if i < 0 or i >= len(self._acc):
            return
        j = min(len(self._acc), i + len(sig)); s = sig[:j - i] * g
        self._acc[i:j, 0] += s * np.sqrt(0.5 * (1 - pan))
        self._acc[i:j, 1] += s * np.sqrt(0.5 * (1 + pan))

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
        s, ins, bar = self.style, self.inst, self.bar
        p = self.composer.plan(bar)
        self.section = p.section
        B = 60 / s.bpm; BAR = 4 * B
        e, k = p.energy, p.key_offset
        chord_fn = getattr(ins, s.chords)
        # chords: soft strum; a passing chord takes beats 3-4, otherwise a quieter re-hit there every other bar
        vc = self._voice([x + k for x in p.chord.tones]); self.center = float(np.mean(vc))
        for i, m in enumerate(vc):
            self._add(chord_fn(m, (BAR * 0.98 if p.passing is None else 2 * B), 0.8), i * 0.022, (i - 1.5) * 0.18, 0.11)
        if p.passing is not None:
            vp = self._voice([x + k for x in p.passing.tones]); self.center = float(np.mean(vp))
            for i, m in enumerate(vp):
                self._add(chord_fn(m, 2 * B * 0.98, 0.7), 2 * B + i * 0.02, (i - 1.5) * 0.18, 0.09)
        elif bar % 2 == 1 and e > 0.3 and p.section != "breakdown":
            for i, m in enumerate(vc[1:]):
                self._add(chord_fn(m, BAR * 0.45, 0.5), 2 * B + i * 0.02, (i - 1) * 0.2, 0.04 + 0.04 * e)
        # bass: root, back-half note, approach into the next root
        for i, n in enumerate(p.bass):
            self._add(ins.sub(n.midi, n.length * B * (0.95 if i else 1.0)), n.beat * B, 0, 0.30 if i == 0 else 0.20)
        # drums: sparse at low energy, the style's full pattern from the middle up
        if "drums" in p.layers:
            dg = 0.7 + 0.6 * e
            full = e >= 0.45
            for kb in (self.kick_beats if full else (0,)):
                self._add(ins.kick(), kb * B, 0, (0.42 if kb == int(kb) else 0.30) * dg)
            if s.drums == "busy" and e >= 0.6:
                self._add(ins.kick(), 3.75 * B, 0, 0.16 * dg)
            for b in ((1, 3) if full else (3,)):
                self._add(ins.brush(), b * B + 0.01, 0.08, 0.20 * dg)
            if full and s.drums != "soft" and bar % 2:
                self._add(ins.rim(), 3 * B + 0.01, -0.1, 0.10 * dg)
            steps = 4 if s.drums == "busy" and e >= 0.6 else 2
            hat_g = {"soft": 0.03, "lofi": 0.05, "busy": 0.045}[s.drums] * (1.0 if full else 0.6)
            for b in range(4):
                for h in range(steps):
                    off = h * B / steps + (B * s.swing * 2 / steps if h % 2 else 0)
                    self._add(ins.hat(0.6 if h % 2 else 1.0), b * B + off, -0.3,
                              hat_g * dg * (0.8 + 0.4 * self.rng.random()))
        # lead and counter-melody (the counter on the chord instrument, quieter and slower)
        if "lead" in p.layers:
            lead_fn = getattr(ins, s.lead)
            for n in p.melody:
                self._add(lead_fn(n.midi, n.length * B * 0.95, 0.9), n.beat * B + 0.012 * self.rng.random(), 0.15,
                          0.30 if s.lead == "pad" else 0.20)
        if "counter" in p.layers:
            for n in p.counter:
                self._add(chord_fn(n.midi, n.length * B * 0.95, 0.6), n.beat * B + 0.03, -0.25, 0.08)

    def _master(self, x: np.ndarray) -> np.ndarray:
        s, n, sr = self.style, len(x), self.sr
        # vinyl crackle and hiss
        if s.vinyl > 0:
            cr = np.zeros((n, 2))
            hits = self.rng.integers(0, n, (int(n / sr * 28 * s.vinyl) + 1, 2))
            cr[hits[:, 0], 0] = self.rng.uniform(-1, 1, len(hits))
            cr[hits[:, 1], 1] = self.rng.uniform(-1, 1, len(hits))
            x = x + (self._crackle_hp(cr) * 0.22 + self._hiss_lp(self.rng.standard_normal((n, 2))) * 0.004) * s.vinyl
        # room: early-reflection taps over the dry history
        H = len(self._room_hist)
        ext = np.concatenate([self._room_hist, x])
        wet = np.zeros_like(x)
        for d, g in self._room_taps:
            k = int(d * sr)
            wet += ext[H - k:H - k + n] * g
        self._room_hist = ext[-H:]
        y = x + self._room_lp(wet) * (0.2 + 1.6 * s.space)
        # tape wow and flutter: a slowly modulated fractional delay, continuous in absolute time
        W = len(self._wow_hist)
        ext = np.concatenate([self._wow_hist, y])
        t = (self.samples_out + np.arange(n)) / sr
        d = (0.0025 + 0.0016 * np.sin(2 * np.pi * 0.5 * t) + 0.0005 * np.sin(2 * np.pi * 3.3 * t + 1)) * sr
        pos = W + np.arange(n) - d
        grid = np.arange(len(ext))
        y = np.stack([np.interp(pos, grid, ext[:, c]) for c in (0, 1)], 1)
        self._wow_hist = ext[-W:]
        # tone: darker or more open tape
        cut = int(2800 * 5 ** s.brightness)
        if cut != self._tone_cut:
            new = StatefulFilter(_sos("lowpass", cut, 0, sr), 2)
            if self._tone_lp is not None:
                new.zi = self._tone_lp.zi
            self._tone_lp, self._tone_cut = new, cut
        y = self._tone_lp(y)
        if self.samples_out < 0.6 * sr:  # fade in at the very start of the stream
            y *= np.clip((self.samples_out + np.arange(n)) / (0.6 * sr), 0, 1)[:, None]
        return np.tanh(y * MASTER_GAIN) * 0.9
