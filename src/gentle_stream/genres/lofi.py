"""Lo-fi, the default genre: a streaming port of the procedural `lofi_mel.py` bed used for the YouTube videos.

Felt piano / rhodes / nylon / pad over soft strums, sub bass, dusty swung drums, vinyl crackle, a small room and
tape wow. With the samples backend the tonal voices and drums are FluidR3_GM (`samples.SampleInstruments`).
"""
from __future__ import annotations

import numpy as np

from ..synth import SR, bp, hp, lp, mtof
from .base import Genre, Master

KICK_PATTERNS = ((0, 2), (0, 1.5), (0, 2.5), (0, 1.5, 2))  # quarter-note beats, 0-based
MASTER_GAIN = 1.6


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


class LofiGenre(Genre):
    name = "lofi"
    description = "lo-fi hip hop: dusty swung drums, warm keys, sub bass, vinyl crackle and tape wow"
    bpm = (60, 92)
    modes = ("minor", "dorian", "major", "mixolydian", "lydian", "phrygian")
    voices = {"piano": "felt piano", "rhodes": "rhodes electric piano", "nylon": "nylon guitar",
              "pad": "warm pad"}

    def instruments(self, rng, sr, sampled):
        return sampled or Instruments(rng, sr)

    def start(self, piece):
        if piece.style.drums == "soft":
            piece.state["kick_beats"] = (0, 2)
        else:
            piece.state["kick_beats"] = KICK_PATTERNS[int(piece.rng.integers(len(KICK_PATTERNS)))]

    def schedule(self, piece, p):
        s, ins, bar = piece.style, piece.inst, p.bar
        B = 60 / s.bpm; BAR = 4 * B
        e, k = p.energy, p.key_offset
        add = piece.add
        chord_fn = getattr(ins, s.chords)
        # chords: soft strum; a passing chord takes beats 3-4, otherwise a quieter re-hit there every other bar
        vc = piece.voice([x + k for x in p.chord.tones]); piece.center = float(np.mean(vc))
        for i, m in enumerate(vc):
            add(chord_fn(m, (BAR * 0.98 if p.passing is None else 2 * B), 0.8), i * 0.022, (i - 1.5) * 0.18, 0.11)
        if p.passing is not None:
            vp = piece.voice([x + k for x in p.passing.tones]); piece.center = float(np.mean(vp))
            for i, m in enumerate(vp):
                add(chord_fn(m, 2 * B * 0.98, 0.7), 2 * B + i * 0.02, (i - 1.5) * 0.18, 0.09)
        elif bar % 2 == 1 and e > 0.3 and p.section != "breakdown":
            for i, m in enumerate(vc[1:]):
                add(chord_fn(m, BAR * 0.45, 0.5), 2 * B + i * 0.02, (i - 1) * 0.2, 0.04 + 0.04 * e)
        # bass: root, back-half note, approach into the next root
        for i, n in enumerate(p.bass):
            add(ins.sub(n.midi, n.length * B * (0.95 if i else 1.0)), n.beat * B, 0, 0.30 if i == 0 else 0.20)
        # drums: sparse at low energy, the style's full pattern from the middle up
        if "drums" in p.layers:
            dg = 0.7 + 0.6 * e
            full = e >= 0.45
            for kb in (piece.state["kick_beats"] if full else (0,)):
                add(ins.kick(), kb * B, 0, (0.42 if kb == int(kb) else 0.30) * dg)
            if s.drums == "busy" and e >= 0.6:
                add(ins.kick(), 3.75 * B, 0, 0.16 * dg)
            for b in ((1, 3) if full else (3,)):
                add(ins.brush(), b * B + 0.01, 0.08, 0.20 * dg)
            if full and s.drums != "soft" and bar % 2:
                add(ins.rim(), 3 * B + 0.01, -0.1, 0.10 * dg)
            steps = 4 if s.drums == "busy" and e >= 0.6 else 2
            hat_g = {"soft": 0.03, "lofi": 0.05, "busy": 0.045}[s.drums] * (1.0 if full else 0.6)
            for b in range(4):
                for h in range(steps):
                    off = h * B / steps + (B * s.swing * 2 / steps if h % 2 else 0)
                    add(ins.hat(0.6 if h % 2 else 1.0), b * B + off, -0.3,
                        hat_g * dg * (0.8 + 0.4 * piece.rng.random()))
        # lead and counter-melody (the counter on the chord instrument, quieter and slower)
        if "lead" in p.layers:
            lead_fn = getattr(ins, s.lead)
            for n in p.melody:
                add(lead_fn(n.midi, n.length * B * 0.95, 0.9), n.beat * B + 0.012 * piece.rng.random(), 0.15,
                    0.30 if s.lead == "pad" else 0.20)
        if "counter" in p.layers:
            for n in p.counter:
                add(chord_fn(n.midi, n.length * B * 0.95, 0.6), n.beat * B + 0.03, -0.25, 0.08)

    def master(self, s):
        return Master(space=s.space, reverb=0.08 + 0.22 * s.space, room=0.2 + 1.6 * s.space, vinyl=s.vinyl,
                      wow=1.0, tone=int(2800 * 5 ** s.brightness), drive=MASTER_GAIN)


GENRE = LofiGenre()
