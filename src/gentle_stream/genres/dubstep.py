"""Dubstep: 140 bpm half-time drums, a deep sine sub, LFO wobble basses and heavy drops.

What the listener was promised (S6): 140 bpm with a half-time feel (kick on beat 1, snare on beat 3, sparse ghost
kicks, 8th/16th/triplet hats), a deep sine sub following the bass line, wobble basses (detuned saws and a square
through a resonant low-pass whose cutoff an LFO opens and closes at a rate synced to 1/4, 1/8, 1/16 or triplets,
changing every half bar), a growl variant (distortion through vowel-like band-passes) answering the wobble, a
build-up (noise riser and an accelerating snare roll) in the bar before the strong sections and a heavy drop on
B/B' when their energy is above 0.7. The intro and the breakdown stay sparse: dark pads and plucks. Gritty but
controlled master: moderate drive, firm bus compression, no vinyl, no tape wow.

The wobble LFO lives in absolute time: every period it can take divides a half bar, so its phase is 0 (filter
closed) at every half-bar line and a rate change never jumps. The filter sweep is a crossfade across a bank of
fixed resonant low-passes, vectorized and smooth.

With the samples backend the drums come from FluidR3_GM's Power (16) and Standard (0) kits and the pads, plucks and
lead from GM synth programs; the sub, the wobble, the growl and the riser stay procedural on both backends (no GM
program can follow a synced filter LFO).
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
from scipy.signal import sosfilt

from ..composer import FIRST_FORM
from ..samples import Hit, Program
from ..synth import SR, adsr, bp, distort, hp, lp, mtof, osc
from .base import Genre, Master

DUR_STEP = 0.05
DROP_ENERGY = 0.7          # B/B' at or above this energy drop the full wobble
DROPS = ("B", "B'")
LENGTHS = dict(FIRST_FORM)
BUILD_BEFORE = {"A": "B", "breakdown": "B'"}   # sections whose last bar builds into a drop
# LFO periods in beats, all dividing a half bar: 1/4, 1/8, 1/16, 1/8 triplet, 1/4 triplet
QUARTER, EIGHTH, SIXTEENTH, TRIPLET8, TRIPLET4 = 1.0, 0.5, 0.25, 1 / 3, 2 / 3
DROP_RATES = (QUARTER, EIGHTH, EIGHTH, SIXTEENTH, TRIPLET8, TRIPLET4)
BANK = tuple(int(110 * 1.75 ** k) for k in range(8))   # low-pass cutoffs of the wobble filter (110 Hz .. 5.8 kHz)
CLOSED, OPEN = 120.0, 4200.0                           # wobble cutoff range at full depth
RESONANCE = 2.6                                        # Q of the wobble filter: the vocal "yoy" peak
GHOST_KICKS = ((), (2.75,), (1.5,), (3.5,), (0.75, 3.25), (1.75,))
PLUCK_PATTERNS = ((0, 1.5, 3), (0, 0.75, 2.5), (0.5, 2, 3.5), (0, 2.75))
ROLL = (0, 1, 2, 2.5, 3, 3.25, 3.5, 3.625, 3.75, 3.875)  # build-up snare roll (beats): it keeps accelerating
GROWL = (((400, 800), (300, 900)), ((750, 1250), (2400, 2900)))   # vowel formants ("o", "a"): (band) pairs


def _bucket(dur: float) -> float:
    return max(1, round(dur / DUR_STEP)) * DUR_STEP


@lru_cache(maxsize=64)
def _resonant(fc: int, q: float, sr: int) -> np.ndarray:
    """A resonant (peaking at `fc`, gain about `q`) second-order low-pass, as one sos section (RBJ cookbook)."""
    w = 2 * np.pi * fc / sr
    c, alpha = np.cos(w), np.sin(w) / (2 * q)
    b = np.array([(1 - c) / 2, 1 - c, (1 - c) / 2]) / (1 + alpha)
    a = np.array([1, -2 * c / (1 + alpha), (1 - alpha) / (1 + alpha)])
    return np.concatenate([b, a])[None, :]


def lfo(beats: np.ndarray, rates: tuple[float, ...]) -> np.ndarray:
    """Wobble LFO in [0, 1] at `beats` measured from a half-bar line; `rates[h]` is the period (in beats) during
    half bar h. Every period divides two beats, so the phase is 0 (closed) at each half-bar line: continuous."""
    beats = np.asarray(beats, dtype=float)
    h = np.clip((beats // 2).astype(int), 0, len(rates) - 1)
    period = np.asarray(rates, dtype=float)[h]
    phase = ((beats - 2 * h) / period) % 1.0
    return 0.5 - 0.5 * np.cos(2 * np.pi * phase)


class Instruments:
    """Procedural dubstep voices. Tonal notes are cached per piece; the drums are a few noise variants drawn once
    from the piece's generator."""

    def __init__(self, rng: np.random.Generator, sr: int = SR):
        self.rng, self.sr = rng, sr
        self._cache: dict[tuple, np.ndarray] = {}
        self._kick = self._make_kick()
        self._snares = [self._make_snare() for _ in range(2)]
        self._hats = [self._make_hat(False) for _ in range(3)]
        self._open = self._make_hat(True)
        self._crash = self._make_crash()

    def _t(self, n):
        return np.arange(n) / self.sr

    def _cached(self, key, make):
        x = self._cache.get(key)
        if x is None:
            if len(self._cache) > 1024:
                self._cache.clear()
            x = self._cache[key] = make()
        return x

    # ------------------------------------------------------------ bass
    def sub(self, m, dur, vel=1.0):
        """A pure sine with soft edges: the floor under everything."""
        d = _bucket(dur)
        return self._cached(("sub", m, d), lambda: self._sub(m, d)) * vel

    def _sub(self, m, dur):
        n = int((dur + 0.03) * self.sr)
        return osc("sine", mtof(m), n, self.sr) * adsr(n, self.sr, 0.006, 0.05, 0.9, 0.03, hold=dur) * 0.6

    def _saws(self, m, n):
        f = mtof(m)
        return (0.4 * osc("saw", f * 2 ** (-14 / 1200), n, self.sr) + 0.4 * osc("saw", f * 2 ** (14 / 1200), n, self.sr,
                0.37) + 0.35 * osc("square", f / 2, n, self.sr, 0.11))

    def _curve(self, n, beat, off, rates, depth):
        """Per-sample LFO value for a note starting `off` beats after a half-bar line."""
        return depth * lfo(off + self._t(n) / beat, rates)

    def wobble(self, m, dur, beat, off, rates, depth=1.0, vel=1.0):
        """Detuned saws and a sub-octave square through a resonant low-pass swept by the synced LFO (`rates`:
        period in beats per half bar from the note's half bar; `depth` 0-1 scales how far the filter opens)."""
        key = ("wobble", m, _bucket(dur), round(beat, 4), round(off, 3), tuple(round(r, 4) for r in rates),
               round(depth, 2))
        return self._cached(key, lambda: self._wobble(m, _bucket(dur), beat, off, rates, depth)) * vel

    def _wobble(self, m, dur, beat, off, rates, depth):
        n = int((dur + 0.02) * self.sr)
        s = self._saws(m, n)
        cut = CLOSED * (OPEN / CLOSED) ** self._curve(n, beat, off, rates, depth)
        # crossfade across the bank: weights are triangles in log-frequency, so the sweep is smooth
        u = np.log(cut / BANK[0]) / np.log(1.75)
        y = np.zeros(n)
        for k, fc in enumerate(BANK):
            w = np.clip(1 - np.abs(u - k), 0, 1)
            if not w.any():
                continue
            y += w * sosfilt(_resonant(fc, RESONANCE, self.sr), s)
        y = hp(y, 70, self.sr)
        env = adsr(n, self.sr, 0.004, 0.05, 0.95, 0.015, hold=dur)
        return distort(y * 0.8, 1.4) * env * 0.6

    def growl(self, m, dur, beat, off, rates, depth=1.0, vel=1.0):
        """The wobble's growling twin: driven hard, then through vowel formants the LFO morphs from "o" to "a"."""
        key = ("growl", m, _bucket(dur), round(beat, 4), round(off, 3), tuple(round(r, 4) for r in rates),
               round(depth, 2))
        return self._cached(key, lambda: self._growl(m, _bucket(dur), beat, off, rates, depth)) * vel

    def _growl(self, m, dur, beat, off, rates, depth):
        n = int((dur + 0.02) * self.sr)
        s = distort(self._saws(m, n) * 5, 1.0)
        w = self._curve(n, beat, off, rates, depth)
        vowels = [sum(bp(s, a, b, self.sr) for a, b in bands) for bands in GROWL]
        y = vowels[0] * (1 - w) + vowels[1] * w + 0.25 * lp(s, 250, self.sr)
        env = adsr(n, self.sr, 0.004, 0.05, 0.95, 0.015, hold=dur)
        return distort(y * 2.2, 1.2) * env * 0.42

    # ------------------------------------------------------------ tonal voices
    def pad(self, m, dur, vel=1.0):
        """Dark pad: detuned saws behind a low low-pass, slow attack."""
        d = _bucket(dur)
        return self._cached(("pad", m, d), lambda: self._pad(m, d)) * vel

    def _pad(self, m, dur):
        n = int((dur + 0.8) * self.sr); f = mtof(m)
        s = sum(osc("saw", f * 2 ** (c / 1200), n, self.sr, phase=i / 3) for i, c in enumerate((-10, 0, 9))) / 3
        return lp(s, 750, self.sr) * adsr(n, self.sr, 0.5, 0.5, 0.85, 0.8, hold=dur) * 0.6

    def pluck(self, color, m, dur, vel=1.0):
        """The Style.chords colour as a short pluck: saw (piano), FM bell (rhodes), pulse (nylon), soft saw (pad)."""
        d = _bucket(dur)
        return self._cached(("pluck", color, m, d), lambda: self._pluck(color, m, d)) * vel

    def _pluck(self, color, m, dur):
        n = int((dur + 0.3) * self.sr); t = self._t(n); f = mtof(m)
        if color == "rhodes":
            s = np.sin(2 * np.pi * f * t + 2.2 * np.exp(-t * 9) * np.sin(2 * np.pi * 3.5 * f * t))
            decay = 6
        else:
            s = (osc("pulse", f, n, self.sr, duty=0.3) if color == "nylon"
                 else osc("saw", f, n, self.sr))
            bright = 2500 if color == "pad" else 6000
            w = np.exp(-t * 16)
            s = lp(s, 700, self.sr) * (1 - w) + lp(s, bright, self.sr) * w
            decay = 7
        gate = np.clip((dur + 0.3 - t) / 0.03, 0, 1)
        return s * np.exp(-t * decay) * np.minimum(1, t / 0.002) * gate * 0.4

    def lead(self, color, m, dur, vel=1.0):
        """Saw (piano), square (rhodes), pulse pluck (nylon) or soft sine-saw (pad) with a late vibrato."""
        d = _bucket(dur)
        return self._cached(("lead", color, m, d), lambda: self._lead(color, m, d)) * vel

    def _lead(self, color, m, dur):
        n = int((dur + 0.15) * self.sr); t = self._t(n)
        f = mtof(m) * 2 ** (np.clip((t - 0.25) / 0.3, 0, 1) * 18 * np.sin(2 * np.pi * 5.2 * t) / 1200)
        if color == "rhodes":
            s, cut = osc("square", f, n, self.sr), 3500
        elif color == "nylon":
            s, cut = osc("pulse", f, n, self.sr, duty=0.25), 4500
        elif color == "pad":
            s, cut = 0.7 * osc("sine", f, n, self.sr) + 0.3 * osc("saw", f, n, self.sr), 2200
        else:
            s, cut = 0.5 * (osc("saw", f, n, self.sr) + osc("saw", f * 1.004, n, self.sr, 0.3)), 5000
        return lp(s, cut, self.sr) * adsr(n, self.sr, 0.01, 0.2, 0.8, 0.15, hold=dur) * 0.45

    def riser(self, seconds):
        """White noise swept up through band-passes, swelling over `seconds` and cut with a short fade."""
        d = _bucket(seconds)
        return self._cached(("riser", d), lambda: self._riser(d))

    def _riser(self, dur):
        n = int(dur * self.sr); t = self._t(n)
        noise = np.random.default_rng(140).standard_normal(n)      # fixed: the cache must not consume piece randomness
        x = t / dur
        bands = [bp(noise, int(f / 1.5), int(f * 1.5), self.sr) for f in (400, 1100, 3000, 7000)]
        u = x * (len(bands) - 1)
        y = sum(np.clip(1 - np.abs(u - k), 0, 1) * b for k, b in enumerate(bands))
        f = 200 * 8 ** x                                            # a rising saw underneath
        y = y + 0.3 * lp(osc("saw", f, n, self.sr), 3000, self.sr)
        return y * x ** 2 * np.clip((dur - t) / 0.01, 0, 1) * 0.5

    # ------------------------------------------------------------ drums
    def _make_kick(self):
        n = int(0.5 * self.sr); t = self._t(n)
        f = 45 + 140 * np.exp(-t * 28)
        body = np.sin(2 * np.pi * np.cumsum(f) / self.sr) * np.exp(-t * 6)
        click = hp(self.rng.standard_normal(n), 2500, self.sr) * np.exp(-t * 300) * 0.35
        return distort((body + click) * 1.5, 1.4) * 0.8

    def _make_snare(self):
        n = int(0.35 * self.sr); t = self._t(n)
        body = np.sin(2 * np.pi * 190 * np.cumsum(1 + 0.4 * np.exp(-t * 40)) / self.sr) * np.exp(-t * 22) * 0.7
        noise = bp(self.rng.standard_normal(n), 1200, 9000, self.sr) * np.exp(-t * 14)
        clap = sum(np.roll(bp(self.rng.standard_normal(n), 900, 4000, self.sr) * np.exp(-t * 60), int(d * self.sr))
                   for d in (0.0, 0.011, 0.022))
        return distort((body + noise + 0.4 * clap) * 1.2, 1.3) * 0.75

    def _make_hat(self, open_):
        n = int((0.28 if open_ else 0.05) * self.sr); t = self._t(n)
        return hp(self.rng.standard_normal(n), 8000, self.sr) * np.exp(-t * (12 if open_ else 90)) * 0.4

    def _make_crash(self):
        n = int(1.8 * self.sr); t = self._t(n)
        return hp(self.rng.standard_normal(n), 4500, self.sr) * np.exp(-t * 2.4) * np.minimum(1, t / 0.003) * 0.3

    def kick(self, g=1.0):
        return self._kick * g

    def snare(self, g=1.0):
        return self._snares[int(self.rng.integers(len(self._snares)))] * g

    def hat(self, open=False, g=1.0):
        return (self._open if open else self._hats[int(self.rng.integers(len(self._hats)))]) * g

    def crash(self, g=1.0):
        return self._crash * g


def _rms(x: np.ndarray, sr: int) -> float:
    return float(np.sqrt(np.mean(np.square(x[:int(0.5 * sr)])))) + 1e-12


@lru_cache(maxsize=4)
def _targets(sr: int) -> dict[str, float]:
    """RMS of the procedural voices' reference notes (middle C, 1 s): the sampled ones are scaled to it."""
    p = Instruments(np.random.default_rng(0), sr)
    out = {"pad": _rms(p.pad(60, 1.0), sr), "kick": _rms(p.kick(), sr), "snare": _rms(p._snares[0], sr),
           "hat": _rms(p._hats[0], sr), "open": _rms(p._open, sr), "crash": _rms(p.crash(), sr)}
    for c in ("piano", "rhodes", "nylon", "pad"):
        out["pluck-" + c] = _rms(p.pluck(c, 60, 1.0), sr)
        out["lead-" + c] = _rms(p.lead(c, 60, 1.0), sr)
    return out


class SampledVoices:
    """Pads, plucks and lead on FluidR3_GM synth programs, drums from the Power (16) and Standard (0) kits. The
    sub, wobble, growl and riser stay procedural: a GM program cannot follow the synced filter LFO."""

    PLUCK = {"piano": 81, "rhodes": 98, "nylon": 84, "pad": 90}   # Saw Lead, Crystal, Charang, Polysynth
    LEAD = {"piano": 81, "rhodes": 80, "nylon": 84, "pad": 82}    # Saw Lead, Square Lead, Charang, Calliope

    def __init__(self, sampled, rng: np.random.Generator, sr: int = SR):
        self.s, self.rng, self.sr = sampled, rng, sr
        self.proc = Instruments(rng, sr)
        r = _targets(sr)
        self.pad_p = Program(89, velocity=(45, 95), tail=1.0, rms=r["pad"])
        self.pluck_p = {c: Program(p, velocity=(55, 110), tail=0.3, rms=r["pluck-" + c]) for c, p in self.PLUCK.items()}
        self.lead_p = {c: Program(p, velocity=(60, 115), tail=0.3, rms=r["lead-" + c]) for c, p in self.LEAD.items()}
        self.drums = {"kick": Hit(16, 36, 120, 0.5, r["kick"]), "snare": Hit(16, 38, 115, 0.4, r["snare"]),
                      "hat": Hit(0, 42, 90, 0.12, r["hat"]), "open": Hit(0, 46, 90, 0.35, r["open"]),
                      "crash": Hit(0, 49, 100, 1.8, r["crash"])}

    def sub(self, m, dur, vel=1.0):
        return self.proc.sub(m, dur, vel)

    def wobble(self, *args, **kw):
        return self.proc.wobble(*args, **kw)

    def growl(self, *args, **kw):
        return self.proc.growl(*args, **kw)

    def riser(self, seconds):
        return self.proc.riser(seconds)

    def pad(self, m, dur, vel=1.0):
        return self.s.play(self.pad_p, m, dur, 0.8) * vel

    def pluck(self, color, m, dur, vel=1.0):
        return self.s.play(self.pluck_p[color], m, dur, vel)

    def lead(self, color, m, dur, vel=1.0):
        return self.s.play(self.lead_p[color], m, dur, vel)

    def kick(self, g=1.0):
        return self.s.hit(self.drums["kick"], g)

    def snare(self, g=1.0):
        return self.s.hit(self.drums["snare"], g)

    def hat(self, open=False, g=1.0):
        return self.s.hit(self.drums["open" if open else "hat"], g)

    def crash(self, g=1.0):
        return self.s.hit(self.drums["crash"], g)


class DubstepGenre(Genre):
    name = "dubstep"
    description = "dubstep: 140 bpm half-time drums, deep sine sub, LFO wobble and growl basses, heavy drops"
    bpm = (136, 140)
    modes = ("minor", "phrygian")
    voices = {"piano": "wobble bass drops, saw plucks / saw lead", "rhodes": "growl bass drops, FM bell plucks",
              "nylon": "wobble bass drops, pulse plucks", "pad": "wobble bass drops, dark pad / soft sine lead"}

    def instruments(self, rng, sr, sampled):
        return SampledVoices(sampled, rng, sr) if sampled is not None else Instruments(rng, sr)

    def start(self, piece):
        r = piece.rng
        rates = [DROP_RATES[int(r.integers(len(DROP_RATES)))] for _ in range(8)]
        if len(set(rates)) < 3:                      # keep the drop moving: at least three different rates
            rates[3], rates[6] = SIXTEENTH, TRIPLET8
            rates[1] = QUARTER if rates[1] != QUARTER else EIGHTH
        piece.state["rates"] = tuple(rates)
        piece.state["ghosts"] = GHOST_KICKS[int(r.integers(len(GHOST_KICKS)))]
        piece.state["plucks"] = PLUCK_PATTERNS[int(r.integers(len(PLUCK_PATTERNS)))]
        piece.state["light"] = (QUARTER, TRIPLET4)[int(r.integers(2))]

    def schedule(self, piece, p):
        s, ins, add, st = piece.style, piece.inst, piece.add, piece.state
        B = 60 / s.bpm
        e, k = p.energy, p.key_offset
        drop = p.section in DROPS and e >= DROP_ENERGY
        quiet = p.section in ("intro", "breakdown")
        build = p.section in BUILD_BEFORE and p.section_bar == LENGTHS[p.section] - 1

        # dark pad: the chord as root plus voice-led upper tones (loud when sparse, a bed under the drop)
        segments = [(p.chord, 0.0, 4.0 if p.passing is None else 2.0)]
        if p.passing is not None:
            segments.append((p.passing, 2.0, 2.0))
        voicings = []
        for chord, at, beats in segments:
            vc = piece.voice([x + k for x in chord.tones]); piece.center = float(np.mean(vc))
            voicings.append((at, vc))
            for i, m in enumerate(vc):
                add(ins.pad(m, beats * B * 0.99, 0.8), at * B, (i - (len(vc) - 1) / 2) * 0.4,
                    0.07 if quiet else 0.04)

        def chord_at(beat):
            return voicings[-1][1] if beat >= 2 and len(voicings) > 1 else voicings[0][1]

        # plucks: sparse chord-tone figures, an octave up (not over the full drop)
        if not drop:
            for j, b in enumerate(st["plucks"]):
                vc = chord_at(b)
                add(ins.pluck(s.chords, vc[(j + p.bar) % len(vc)] + 12, 0.5 * B, 0.8), b * B,
                    0.35 if j % 2 else -0.35, 0.12 if quiet else 0.08)

        # sub under every bass note, and the wobble (light in A sections, full on the drop)
        for n in p.bass:
            m = n.midi if n.midi <= 40 else n.midi - 12
            add(ins.sub(m, n.length * B * 0.97, 1.0), n.beat * B, 0, 0.14 if quiet else 0.42 if drop else 0.3)
        if not quiet:
            growl_bar = (p.section_bar % 4 == 3) != (s.chords == "rhodes")
            for n in p.bass:
                h0 = int(n.beat // 2)
                off = n.beat - 2 * h0
                h1 = min(int((n.beat + n.length - 1e-6) // 2), 1)
                if drop:
                    rates = tuple(st["rates"][(2 * p.section_bar + h) % 8] for h in range(h0, h1 + 1))
                    depth, voice, g = 1.0, ins.growl if growl_bar else ins.wobble, 0.34
                else:
                    rates, depth, voice, g = (st["light"],) * (h1 - h0 + 1), 0.35 + 0.15 * min(1, e), ins.wobble, 0.13
                add(voice(n.midi + 12, n.length * B * 0.97, B, off, rates, depth), n.beat * B, 0, g)

        # build-up: a riser over the whole bar and an accelerating, swelling snare roll into the drop
        if build:
            add(ins.riser(4 * B), 0, 0, 0.25)
            if s.drums != "none":
                for i, b in enumerate(ROLL):
                    add(ins.snare(), b * B, 0.05, 0.08 + 0.22 * i / (len(ROLL) - 1))
                add(ins.kick(), 0, 0, 0.4)

        # half-time drums: kick on 1, snare on 3, sparse ghost kicks; hats in 8ths, 16ths on the drop, triplets
        # at phrase ends
        if "drums" in p.layers and not build:
            dg = (0.75 + 0.4 * e) * (0.7 if s.drums == "soft" else 1.0)
            add(ins.kick(), 0, 0, 0.5 * dg)
            ghosts = st["ghosts"] if (drop or s.drums == "busy") else st["ghosts"][:1]
            for b in ghosts:
                add(ins.kick(), b * B, 0, 0.3 * dg)
            add(ins.snare(), 2 * B, 0.05, 0.42 * dg)
            if p.section_bar == 0 and (drop or e >= 0.6):
                add(ins.crash(), 0, -0.35, 0.2 * dg)
            phrase_end = p.section_bar % 4 == 3 and s.drums != "soft"
            step = 0.25 if drop and s.drums != "soft" else 0.5
            hats = [j * step for j in range(int(round((2 if phrase_end else 4) / step)))]
            if phrase_end:
                hats += [2 + j / 3 for j in range(6)]
            for b in hats:
                accent = abs(b - round(b)) < 1e-6
                add(ins.hat(False), b * B, -0.25, (0.1 if accent else 0.065) * dg)
            if drop:
                add(ins.hat(True), 3.5 * B, -0.25, 0.08 * dg)

        # lead (the melody) and counter-melody plucks
        if "lead" in p.layers and s.lead != "none":
            for n in p.melody:
                add(ins.lead(s.lead, n.midi, n.length * B * 0.92, 0.9), n.beat * B, 0.12, 0.17 if drop else 0.2)
        if "counter" in p.layers:
            for n in p.counter:
                add(ins.pluck(s.chords, n.midi, n.length * B * 0.9, 0.6), n.beat * B + 0.01, -0.3, 0.07)

    def master(self, s):
        return Master(space=0.25 + 0.4 * s.space, reverb=0.06 + 0.12 * s.space, room=0.15 + 0.5 * s.space,
                      vinyl=0.0, wow=0.0, tone=min(20000, int(9000 * 2 ** s.brightness)), drive=2.3,
                      threshold=-20.0, ratio=3.5)


GENRE = DubstepGenre()
