"""Electro: classic electro-funk with syncopated 808 breakbeats, robotic synth riffs and a funky synth bass.

What the listener was promised (S6): syncopated 808-style breakbeats (boomy pitched kick with a long decay on
syncopated 16ths, snappy snare and multi-burst clap on 2 and 4, crisp closed 16th hats with accents, cowbell or
rim accents off the beat), robotic square/saw riffs and 16th-note arpeggios whose filter sweeps in absolute time,
a funky syncopated synth bass with octave jumps, short chord stabs and a tight, dry master (no vinyl, no tape wow).
Not four-on-the-floor (that is electrohouse). Only originally composed patterns.

With the samples backend the voices are FluidR3_GM programs (81 Saw Lead, 80 Square Lead, 38 Synth Bass 1,
50 Synth Strings 1...) and the TR-808 drum kit (bank 128, preset 25).
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from ..samples import Hit, Program
from ..synth import SR, adsr, bp, distort, hp, lp, mtof, osc
from .base import Genre, Master

DUR_STEP = 0.05
ARP_ENERGY = 0.7           # sections at or above this energy get the 16th-note arpeggiator (the breakdown too)
FUNK_ENERGY = 0.4          # below it (and in the intro/breakdown) the bass holds the composer's line
BELL_ENERGY = 0.6          # at or above it the off-beat accents are cowbell, below it rimshots
SWEEP_STEPS = 64           # arpeggio filter LFO period in 16ths (4 bars), counted from the piece's first bar
CUT_STEP = 0.1             # the sweep is quantised so arpeggio notes stay cacheable
# kick patterns as (first bar, second bar) of a two-bar phrase, in 16th steps: syncopated, never on every quarter
KICKS = (((0, 6, 10), (0, 6, 9, 14)), ((0, 3, 6, 10), (0, 3, 10, 11)), ((0, 7, 10), (0, 7, 10, 13)),
         ((0, 6, 11), (0, 2, 6, 11, 14)))
BELLS = ((3, 10), (2, 7, 14), (6, 11, 15), (3, 9, 14))     # off-beat cowbell/rim accents, 16th steps
STABS = ((0, 3, 6), (2, 6, 10, 13), (3, 6, 11, 14), (0, 6, 10, 14))
# funky bass: (16th step, octave up, length in 16ths); the pitch follows the composer's bass line
BASSES = (((0, 0, 3), (3, 1, 1), (6, 0, 2), (8, 0, 1), (10, 1, 2), (13, 0, 1), (14, 1, 2)),
          ((0, 0, 2), (2, 0, 1), (4, 1, 1), (7, 0, 2), (10, 0, 1), (11, 1, 1), (12, 0, 2), (15, 1, 1)),
          ((0, 0, 3), (3, 0, 1), (5, 1, 1), (8, 0, 2), (11, 0, 1), (12, 1, 1), (14, 0, 2)))
ARP_SHAPES = ("up", "updown", "octaves")
HAT_ACCENTS = ((2, 6, 10, 14), (6, 14), (3, 7, 11, 15))    # 16th steps pushed above the plain 8th level
OPEN_HATS = ((), (14,), (6, 14))
HAT_FREQS = (205.3, 304.4, 369.6, 522.7, 540.0, 800.0)      # six square oscillators: the 808 metal


def _bucket(dur: float) -> float:
    return max(1, round(dur / DUR_STEP)) * DUR_STEP


def sweep(step: int) -> float:
    """Arpeggio filter position (0-1) at absolute 16th `step`: a sine LFO, quantised to `CUT_STEP`."""
    v = 0.5 - 0.5 * np.cos(2 * np.pi * step / SWEEP_STEPS)
    return round(round(v / CUT_STEP) * CUT_STEP, 2)


class Instruments:
    """Procedural electro voices: an 808-style kit drawn once from the piece's generator and cached synth notes."""

    def __init__(self, rng: np.random.Generator, sr: int = SR):
        self.rng, self.sr = rng, sr
        self._cache: dict[tuple, np.ndarray] = {}
        self._kick = self._make_kick()
        self._snares = [self._make_snare() for _ in range(2)]
        self._claps = [self._make_clap() for _ in range(2)]
        self._hats = [self._make_hat(False) for _ in range(3)]
        self._open = self._make_hat(True)
        self._bell = self._make_cowbell()
        self._rim = self._make_rim()

    def _t(self, n):
        return np.arange(n) / self.sr

    def _cached(self, key, make):
        x = self._cache.get(key)
        if x is None:
            x = self._cache[key] = make()
        return x

    # ------------------------------------------------------------ tonal voices
    def pad(self, m, dur, vel=1.0):
        """String synth: detuned saws, slow attack, a soft low-pass."""
        d = _bucket(dur)
        return self._cached(("pad", m, d), lambda: self._pad(m, d)) * vel

    def _pad(self, m, dur):
        n = int((dur + 0.5) * self.sr); f = mtof(m)
        s = sum(osc("saw", f * 2 ** (c / 1200), n, self.sr, phase=i / 3) for i, c in enumerate((-7, 0, 7))) / 3
        return lp(s, 2200, self.sr) * adsr(n, self.sr, 0.12, 0.3, 0.8, 0.45, hold=dur) * 0.4

    def stab(self, color, m, dur, vel=1.0):
        """The Style.chords colour: square riff stab (piano), FM electric piano (rhodes), pluck (nylon), string
        synth (pad)."""
        d = _bucket(dur)
        return self._cached(("stab", color, m, d), lambda: self._stab(color, m, d)) * vel

    def _stab(self, color, m, dur):
        f = mtof(m)
        if color == "pad":
            return self._pad(m, dur)
        if color == "rhodes":
            n = int((dur + 0.3) * self.sr); t = self._t(n)
            s = (np.sin(2 * np.pi * f * t + 1.4 * np.exp(-t * 5) * np.sin(2 * np.pi * f * t))
                 + 0.2 * np.sin(2 * np.pi * 14 * f * t) * np.exp(-t * 20))
            return s * adsr(n, self.sr, 0.003, 0.6, 0.3, 0.2, hold=dur) * 0.45
        if color == "nylon":
            n = int((dur + 0.15) * self.sr); t = self._t(n)
            s = osc("pulse", f, n, self.sr, duty=0.3)
            w = np.exp(-t * 18)
            s = lp(s, 800, self.sr) * (1 - w) + lp(s, 5000, self.sr) * w
            return s * np.exp(-t * 7) * np.minimum(1, t / 0.002) * np.clip((dur + 0.15 - t) / 0.03, 0, 1) * 0.45
        n = int((dur + 0.08) * self.sr); t = self._t(n)   # piano: two detuned squares, a snapping filter
        s = (osc("square", f * 2 ** (-5 / 1200), n, self.sr) + osc("square", f * 2 ** (5 / 1200), n, self.sr, .4)) / 2
        w = np.exp(-t * 22)
        s = lp(s, 1100, self.sr) * (1 - w) + lp(s, 6000, self.sr) * w
        return s * adsr(n, self.sr, 0.002, 0.08, 0.6, 0.06, hold=dur) * 0.35

    def lead(self, m, dur, vel=1.0, color="piano"):
        """Robotic lead: no vibrato, a hard gate and a tiny upward blip on the attack. Saw (piano), square (rhodes),
        thin pulse (nylon) or soft square-saw (pad)."""
        d = _bucket(dur)
        return self._cached(("lead", color, m, d), lambda: self._lead(color, m, d)) * vel

    def _lead(self, color, m, dur):
        n = int((dur + 0.06) * self.sr); t = self._t(n)
        f = mtof(m) * 2 ** (0.5 * np.exp(-t * 60) / 12)          # a half-semitone blip at the attack
        if color == "rhodes":
            s, cut = osc("square", f, n, self.sr), 4000
        elif color == "nylon":
            s, cut = osc("pulse", f, n, self.sr, duty=0.18), 5000
        elif color == "pad":
            s, cut = 0.5 * osc("square", f, n, self.sr) + 0.5 * osc("saw", f, n, self.sr), 2600
        else:
            s, cut = osc("saw", f, n, self.sr), 5500
        return lp(s, cut, self.sr) * adsr(n, self.sr, 0.004, 0.1, 0.75, 0.05, hold=dur) * 0.45

    def arp(self, m, dur, vel=1.0, cut=0.5):
        """Gated square/saw 16th through a resonant low-pass at `cut` (0 dark .. 1 open)."""
        d, c = _bucket(dur), round(cut, 2)
        return self._cached(("arp", m, d, c), lambda: self._arp(m, d, c)) * vel

    def _arp(self, m, dur, cut):
        n = int((dur + 0.05) * self.sr); t = self._t(n); f = mtof(m)
        s = 0.7 * osc("square", f, n, self.sr) + 0.3 * osc("saw", f * 1.004, n, self.sr, 0.3)
        fc = 450 * 2 ** (4 * cut)                                  # 450 Hz .. 7.2 kHz
        s = lp(s, fc, self.sr) + 0.6 * bp(s, fc * 0.8, fc * 1.25, self.sr)
        return s * np.exp(-t * 8) * np.minimum(1, t / 0.002) * np.clip((dur + 0.05 - t) / 0.02, 0, 1) * 0.4

    def bass(self, m, dur, vel=1.0):
        """Saw plus square with a fast, plucky filter envelope and a little drive."""
        d, v = _bucket(dur), round(vel, 1)
        return self._cached(("bass", m, d, v), lambda: self._bass(m, d, v))

    def _bass(self, m, dur, vel):
        n = int((dur + 0.03) * self.sr); t = self._t(n); f = mtof(m)
        s = 0.55 * osc("square", f, n, self.sr, 0.25) + 0.45 * osc("saw", f, n, self.sr)
        w = np.exp(-t * 28)
        tone = lp(s, 380, self.sr) * (1 - w) + (lp(s, 1800, self.sr) + 0.5 * bp(s, 900, 1500, self.sr)) * w
        env = adsr(n, self.sr, 0.002, 0.06, 0.65, 0.025, hold=dur)
        return distort(tone * env * 1.5, 1.4) * vel * 0.6

    # ------------------------------------------------------------ the 808 kit
    def _make_kick(self):
        """Pitched sine dropping onto ~50 Hz, ringing for most of a second (the 808 boom)."""
        n = int(0.9 * self.sr); t = self._t(n)
        f = 50 + 110 * np.exp(-t * 35)
        body = np.sin(2 * np.pi * np.cumsum(f) / self.sr) * np.exp(-t * 3.2)
        click = hp(self.rng.standard_normal(n), 3000, self.sr) * np.exp(-t * 400) * 0.15
        fade = np.clip((0.9 - t) / 0.05, 0, 1)
        return distort((body + click) * 1.1, 1.3) * fade * 0.85

    def _make_snare(self):
        n = int(0.28 * self.sr); t = self._t(n)
        tone = (np.sin(2 * np.pi * 180 * t) + 0.6 * np.sin(2 * np.pi * 330 * t)) * np.exp(-t * 22) * 0.5
        noise = hp(self.rng.standard_normal(n), 1800, self.sr) * np.exp(-t * 16) * 0.5
        return (tone + noise) * np.minimum(1, t / 0.0008) * 0.7

    def _make_clap(self):
        """Three quick noise bursts, then a short reverberant tail (the 808 hand clap)."""
        n = int(0.32 * self.sr); t = self._t(n)
        env = np.zeros(n)
        for at in (0.0, 0.011, 0.022):
            u = t - at
            env += np.where(u >= 0, np.exp(-np.maximum(u, 0) * 320), 0.0)
        u = t - 0.03
        env += np.where(u >= 0, 0.55 * np.exp(-np.maximum(u, 0) * 14), 0.0)
        noise = bp(self.rng.standard_normal(n), 900, 3200, self.sr)
        return noise * env * np.minimum(1, t / 0.0005) * 0.6

    def _make_hat(self, open_):
        n = int((0.4 if open_ else 0.07) * self.sr); t = self._t(n)
        metal = sum(osc("square", f * 1.7, n, self.sr, phase=i / 6) for i, f in enumerate(HAT_FREQS)) / 6
        s = hp(metal + 0.3 * self.rng.standard_normal(n), 7000, self.sr)
        return s * np.exp(-t * (9 if open_ else 70)) * np.minimum(1, t / 0.0005) * 0.5

    def _make_cowbell(self):
        n = int(0.35 * self.sr); t = self._t(n)
        s = bp(osc("square", 540, n, self.sr) + osc("square", 800, n, self.sr), 400, 2600, self.sr)
        env = 0.7 * np.exp(-t * 28) + 0.3 * np.exp(-t * 9)
        return s * env * np.minimum(1, t / 0.0005) * np.clip((0.35 - t) / 0.02, 0, 1) * 0.3

    def _make_rim(self):
        n = int(0.06 * self.sr); t = self._t(n)
        s = np.sin(2 * np.pi * 1700 * t) + 0.5 * np.sin(2 * np.pi * 455 * t)
        return s * np.exp(-t * 90) * 0.35

    def kick(self, g=1.0):
        return self._kick * g

    def snare(self, g=1.0):
        return self._snares[int(self.rng.integers(len(self._snares)))] * g

    def clap(self, g=1.0):
        return self._claps[int(self.rng.integers(len(self._claps)))] * g

    def hat(self, open=False, g=1.0):
        return (self._open if open else self._hats[int(self.rng.integers(len(self._hats)))]) * g

    def cowbell(self, g=1.0):
        return self._bell * g

    def rim(self, g=1.0):
        return self._rim * g


def _rms(x: np.ndarray, sr: int) -> float:
    return float(np.sqrt(np.mean(np.square(x[:int(0.5 * sr)])))) + 1e-12


@lru_cache(maxsize=4)
def _targets(sr: int) -> dict[str, float]:
    """RMS of the procedural voices' reference notes (middle C, 1 s, velocity 0.8): the sampled programs and kit are
    scaled to it, so both backends sit in the same mix."""
    p = Instruments(np.random.default_rng(0), sr)
    out = {"pad": _rms(p.pad(60, 1.0, 0.8), sr), "arp": _rms(p.arp(60, 1.0, 0.8, 0.6), sr),
           "bass": _rms(p.bass(60, 1.0, 0.8), sr), "kick": _rms(p.kick(), sr), "snare": _rms(p._snares[0], sr),
           "clap": _rms(p._claps[0], sr), "hat": _rms(p._hats[0], sr), "open": _rms(p._open, sr),
           "cowbell": _rms(p.cowbell(), sr), "rim": _rms(p.rim(), sr)}
    for c in ("piano", "rhodes", "nylon", "pad"):
        out["lead-" + c] = _rms(p.lead(60, 1.0, 0.8, c), sr)
        out["stab-" + c] = _rms(p.stab(c, 60, 1.0, 0.8), sr)
    return out


class SampledVoices:
    """The same voices on FluidR3_GM programs and the TR-808 kit (bank 128, preset 25)."""

    KIT = 25
    LEAD = {"piano": 81, "rhodes": 80, "nylon": 84, "pad": 87}     # Saw Lead, Square Lead, Charang, Bass+Lead
    STAB = {"piano": 80, "rhodes": 4, "nylon": 84, "pad": 50}      # Square Lead, E.Piano 1, Charang, Synth Strings

    def __init__(self, sampled, rng: np.random.Generator, sr: int = SR):
        self.s, self.rng, self.sr = sampled, rng, sr
        r = _targets(sr)
        self.pad_p = Program(50, velocity=(50, 100), tail=0.6, rms=r["pad"])
        self.arp_p = Program(80, velocity=(60, 110), tail=0.06, rms=r["arp"])
        self.bass_p = Program(38, velocity=(70, 120), tail=0.04, rms=r["bass"])
        self.lead_p = {c: Program(p, velocity=(60, 115), tail=0.08, rms=r["lead-" + c]) for c, p in self.LEAD.items()}
        self.stab_p = {c: Program(p, velocity=(60, 115), tail=0.15, rms=r["stab-" + c]) for c, p in self.STAB.items()}
        self.drums = {"kick": Hit(self.KIT, 36, 115, 0.9, r["kick"]), "snare": Hit(self.KIT, 38, 105, 0.3, r["snare"]),
                      "clap": Hit(self.KIT, 39, 105, 0.35, r["clap"]), "hat": Hit(self.KIT, 42, 90, 0.1, r["hat"]),
                      "open": Hit(self.KIT, 46, 90, 0.4, r["open"]), "cowbell": Hit(self.KIT, 56, 95, 0.35, r["cowbell"]),
                      "rim": Hit(self.KIT, 37, 95, 0.1, r["rim"])}
        self._arp_cache: dict[tuple, np.ndarray] = {}

    def pad(self, m, dur, vel=1.0):
        return self.s.play(self.pad_p, m, dur, vel)

    def stab(self, color, m, dur, vel=1.0):
        return self.s.play(self.stab_p[color], m, dur, vel)

    def lead(self, m, dur, vel=1.0, color="piano"):
        return self.s.play(self.lead_p[color], m, dur, vel)

    def arp(self, m, dur, vel=1.0, cut=0.5):
        """The square lead through the same low-pass sweep as the procedural arpeggio."""
        key = (int(round(m)), _bucket(dur), round(vel, 1), round(cut, 2))
        y = self._arp_cache.get(key)
        if y is None:
            if len(self._arp_cache) > 1024:
                self._arp_cache.clear()
            x = self.s.play(self.arp_p, m, dur, vel)
            fc = 450 * 2 ** (4 * cut)
            y = self._arp_cache[key] = (lp(x, fc, self.sr) + 0.4 * bp(x, fc * 0.8, fc * 1.25, self.sr)) * (
                1.0 + 0.8 * (1 - cut))
        return y

    def bass(self, m, dur, vel=1.0):
        return self.s.play(self.bass_p, m, dur, vel)

    def kick(self, g=1.0):
        return self.s.hit(self.drums["kick"], g)

    def snare(self, g=1.0):
        return self.s.hit(self.drums["snare"], g)

    def clap(self, g=1.0):
        return self.s.hit(self.drums["clap"], g)

    def hat(self, open=False, g=1.0):
        return self.s.hit(self.drums["open" if open else "hat"], g)

    def cowbell(self, g=1.0):
        return self.s.hit(self.drums["cowbell"], g)

    def rim(self, g=1.0):
        return self.s.hit(self.drums["rim"], g)


class ElectroGenre(Genre):
    name = "electro"
    description = "808 breakbeats, robotic synth riffs and arpeggios, funky synth bass (not four-on-the-floor)"
    bpm = (100, 130)
    modes = ("minor", "dorian", "major")
    voices = {"piano": "square synth riff stabs / saw lead", "rhodes": "FM electric piano / square lead",
              "nylon": "pluck synth", "pad": "string synth pad"}

    def instruments(self, rng, sr, sampled):
        return SampledVoices(sampled, rng, sr) if sampled is not None else Instruments(rng, sr)

    def start(self, piece):
        r = piece.rng
        pick = lambda options: options[int(r.integers(len(options)))]
        piece.state.update(kicks=pick(KICKS), bells=pick(BELLS), stabs=pick(STABS), bass=pick(BASSES),
                           arp=pick(ARP_SHAPES), accents=pick(HAT_ACCENTS), open_hats=pick(OPEN_HATS))

    def schedule(self, piece, p):
        s, ins, add, st = piece.style, piece.inst, piece.add, piece.state
        B = 60 / s.bpm; S = B / 4
        e, k = p.energy, p.key_offset
        quiet = p.section in ("intro", "breakdown") or e < FUNK_ENERGY
        # harmony: the chord (and the passing chord on beats 3-4) voice-led above a root
        segments = [(p.chord, 0, 16 if p.passing is None else 8)]
        if p.passing is not None:
            segments.append((p.passing, 8, 8))
        voicings = []
        for chord, at, steps in segments:
            vc = piece.voice([x + k for x in chord.tones]); piece.center = float(np.mean(vc))
            voicings.append((at, vc))
            if s.chords == "pad" or quiet:
                for i, m in enumerate(vc):
                    add(ins.pad(m, steps * S * 0.98, 0.8), at * S, (i - (len(vc) - 1) / 2) * 0.35, 0.09)

        def chord_at(step):
            return voicings[-1][1] if step >= 8 and len(voicings) > 1 else voicings[0][1]

        # short syncopated stabs on the chord colour
        if s.chords != "pad" and not quiet:
            for j in st["stabs"]:
                for i, m in enumerate(chord_at(j)):
                    add(ins.stab(s.chords, m + 12, 1.4 * S, 0.85), j * S + i * 0.003, (i - 1) * 0.3,
                        0.06 + 0.04 * e)
        # arpeggiator: 16ths over the chord, its low-pass swept by an LFO in absolute time (seamless across bars)
        if (e >= ARP_ENERGY and p.section != "release") or p.section == "breakdown":
            for j in range(16):
                vc = [m + 12 for m in chord_at(j)]
                if st["arp"] == "updown":
                    seq = vc + vc[-2:0:-1]
                elif st["arp"] == "octaves":
                    seq = [m for x in vc for m in (x, x + 12)]
                else:
                    seq = vc
                add(ins.arp(seq[j % len(seq)], 0.8 * S, 1.0 if j % 4 == 0 else 0.75, sweep(p.bar * 16 + j)),
                    j * S, 0.35 if j % 2 else -0.35, 0.07 + 0.04 * e)
        # bass: the funky 16th pattern on the composer's line, or the line itself when it is quiet
        if quiet:
            for n in p.bass:
                add(ins.bass(n.midi, n.length * B * 0.95, 0.6), n.beat * B, 0, 0.24)
        else:
            for j, up, length in st["bass"]:
                n = max((x for x in p.bass if x.beat <= j / 4), key=lambda x: x.beat, default=p.bass[0])
                add(ins.bass(n.midi + 12 * up, length * S * 0.8, 0.9 if j % 4 == 0 else 0.75), j * S, 0, 0.30)
        if "drums" in p.layers:
            self._drums(piece, p, S)
        # robotic lead on the melody, counter-melody on the chord colour
        if "lead" in p.layers and s.lead != "none":
            for n in p.melody:
                add(ins.lead(n.midi, n.length * B * 0.85, 0.9, s.lead), n.beat * B, 0.1, 0.2)
        if "counter" in p.layers:
            for n in p.counter:
                if s.chords == "pad":
                    add(ins.pad(n.midi, n.length * B * 0.9, 0.6), n.beat * B, -0.3, 0.07)
                else:
                    add(ins.stab(s.chords, n.midi, min(n.length * B * 0.6, 2 * S), 0.6), n.beat * B + 0.01, -0.3,
                        0.08)

    def _drums(self, piece, p, S):
        s, ins, add, st = piece.style, piece.inst, piece.add, piece.state
        e = p.energy
        dg = (0.75 + 0.5 * e) * (0.7 if s.drums == "soft" else 1.0)
        kicks = st["kicks"][p.bar % 2]
        if s.drums == "busy" and p.bar % 2 and 15 not in kicks:
            kicks = kicks + (15,)
        for j in kicks:
            add(ins.kick(), j * S, 0, (0.42 if j == 0 else 0.36) * dg)
        for j in (4, 12):
            add(ins.snare(), j * S, 0.05, 0.22 * dg)
            add(ins.clap(), j * S, -0.05, 0.26 * dg)
        if s.drums == "busy" and e >= 0.6 and p.section_bar % 8 == 7:
            for j in (14, 15):
                add(ins.clap(), j * S, -0.05, 0.14 * dg)
        if s.drums != "soft":
            accent = ins.cowbell if e >= BELL_ENERGY else ins.rim
            for j in st["bells"]:
                add(accent(), j * S, 0.3, (0.12 if e >= BELL_ENERGY else 0.1) * dg)
        per_beat = 4 if s.drums != "soft" and e >= 0.45 else 2
        for j in range(0, 16, 4 // per_beat):
            is_open = j in st["open_hats"] and per_beat == 4
            g = 0.085 if j % 4 == 0 else 0.07 if j % 2 == 0 else 0.04
            if j in st["accents"]:
                g = max(g, 0.08)
            add(ins.hat(is_open), j * S, -0.25, g * dg * (1.2 if is_open else 1.0))

    def master(self, s):
        return Master(space=0.25 * s.space, reverb=0.02 + 0.05 * s.space, room=0.08 + 0.4 * s.space, vinyl=0.0,
                      wow=0.0, tone=min(20000, int(8000 * 2 ** s.brightness)), drive=2.0, threshold=-22.0,
                      ratio=3.5)


GENRE = ElectroGenre()
