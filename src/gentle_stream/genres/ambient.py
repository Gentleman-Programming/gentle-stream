"""Ambient (elegant): slow additive pads, an FM celesta, a sustained sub and starlight air; no drums, a lot of space.

The sound of the approved Lunar Pilot short bed (`elegant-short` music_ambient.py), streamed bar by bar: every
chord swells in on a detuned additive pad and rings into the next one, the sub holds one note per chord, the lead
is a sparse, high celesta (or glass, harp, soft piano) with a quiet echo, the counter-melody is a slow glass pad
line, and when nobody sings a few celesta notes twinkle over the chord. Chords get an added ninth (Lydian/major
colour). The master is clean (no vinyl, almost no tape wow), wide and soft. With the samples backend the tonal
voices are FluidR3_GM programs (Warm Pad, Space Voice, Celesta, Orchestral Harp, piano); the sub and the air stay
procedural.
"""
from __future__ import annotations

import numpy as np

from ..composer import Chord
from ..samples import Program
from ..synth import SR, bp, lp, mtof
from .base import Genre, Master
from .lofi import Instruments as LofiInstruments

TABLE = 4096
PAD_RELEASE = 2.2      # seconds a pad chord keeps ringing after its bar: the next chord swells in under it
GLASS_RELEASE = 1.8
SUB_RELEASE = 0.9
CELESTA_RING = 2.6
# broken-chord shapes for the chord colour: (beat, index into the voicing, from the top)
ARPS = (((0, 0), (1.5, 2), (3, 1)), ((0, 1), (1.5, 0), (2.5, 2)), ((0.5, 0), (2, 1), (3, 2)))
SPARKLE = (0, 4, 2, 6, 3, 7, 5, 9, 4, 8, 2, 6)   # the reference's walk over the upper chord tones


def _table(amps) -> np.ndarray:
    """One cycle of an additive waveform (with the first sample repeated at the end for interpolation)."""
    ph = np.arange(TABLE + 1) / TABLE
    w = sum(a * np.sin(2 * np.pi * (k + 1) * ph) for k, a in enumerate(amps))
    return w / np.max(np.abs(w))


PAD_WAVE = _table([p ** -1.6 for p in range(1, 9)])
GLASS_WAVE = _table([1.0, 0.3, 0.12, 0.05])


def _read(table: np.ndarray, freq: np.ndarray, sr: int, phase: float) -> np.ndarray:
    pos = (phase + np.concatenate([[0.0], np.cumsum(freq[:-1])]) / sr) % 1.0 * TABLE
    i = pos.astype(np.int64)
    frac = pos - i
    return table[i] + (table[i + 1] - table[i]) * frac


class Voices:
    """Procedural ambient voices. Notes are cached per piece (same pitch, length and velocity: same samples)."""

    def __init__(self, rng: np.random.Generator, sr: int = SR):
        self.rng, self.sr = rng, sr
        self.lofi = LofiInstruments(rng, sr)
        self._cache: dict[tuple, np.ndarray] = {}

    def _cached(self, key, make):
        x = self._cache.get(key)
        if x is None:
            if len(self._cache) > 1024:
                self._cache.clear()
            x = self._cache[key] = make()
        return x

    def _swell(self, n: int, dur: float, attack: float, release: float) -> np.ndarray:
        t = np.arange(n) / self.sr
        return np.clip(t / attack, 0, 1) ** 2 * np.clip((dur + release - t) / release, 0, 1) ** 1.5

    def _pad(self, m, dur, vel):
        f, n = mtof(m), int((dur + PAD_RELEASE) * self.sr)
        t = np.arange(n) / self.sr
        s = np.zeros(n)
        for j, det in enumerate((-0.0035, 0.0, 0.0035)):   # three slowly breathing, detuned voices
            freq = f * (1 + det + 0.0012 * np.sin(2 * np.pi * (0.07 + 0.013 * j) * t + m + j))
            s += _read(PAD_WAVE, freq, self.sr, j / 3)
        env = self._swell(n, dur, min(0.4 * dur, 2.0), PAD_RELEASE)
        return lp(s * env, 1500 + 1500 * vel) * vel * 0.095

    def pad(self, m, dur, vel=1.0):
        return self._cached(("pad", m, round(dur, 2), round(vel * 8)), lambda: self._pad(m, dur, vel))

    def _glass(self, m, dur, vel):
        f, n = mtof(m), int((dur + GLASS_RELEASE) * self.sr)
        t = np.arange(n) / self.sr
        s = sum(_read(GLASS_WAVE, f * (1 + d) * (1 + 0.001 * np.sin(2 * np.pi * 0.21 * t + k)), self.sr, k / 4)
                for k, d in enumerate((-0.002, 0.002)))
        return s * self._swell(n, dur, min(0.45 * dur, 1.5), GLASS_RELEASE) * vel * 0.3

    def glass(self, m, dur, vel=1.0):
        return self._cached(("glass", m, round(dur, 2), round(vel * 8)), lambda: self._glass(m, dur, vel))

    def _celesta(self, m, dur, vel):
        f, n = mtof(m), int((dur + CELESTA_RING) * self.sr)
        t = np.arange(n) / self.sr
        s = np.sin(2 * np.pi * f * t + 2.2 * np.exp(-t * 3.0) * np.sin(2 * np.pi * 3.5 * f * t))
        s += 0.25 * np.sin(2 * np.pi * 2 * f * t)
        env = np.exp(-t * 1.6) * np.minimum(1, t / 0.004) * np.clip((dur + CELESTA_RING - t) / 0.3, 0, 1)
        return lp(s * env, 7000) * vel

    def celesta(self, m, dur, vel=1.0):
        return self._cached(("celesta", m, round(dur, 1), round(vel * 8)), lambda: self._celesta(m, dur, vel))

    def piano(self, m, dur, vel=1.0):
        return lp(self.lofi.piano(m, dur, vel * 0.7), 2500)

    def harp(self, m, dur, vel=1.0):
        return self.lofi.nylon(m, dur + 0.8, vel)

    def sub(self, m, dur):
        def make():
            n = int((dur + SUB_RELEASE) * self.sr)
            t = np.arange(n) / self.sr
            return np.sin(2 * np.pi * mtof(m) * t) * self._swell(n, dur, 0.35, SUB_RELEASE)
        return self._cached(("sub", m, round(dur, 2)), make)

    def air(self, seconds):
        """Band-passed noise under a Hann window: windows started every `seconds / 2` add up to a steady hiss
        of starlight."""
        n = int(seconds * self.sr)
        return bp(self.rng.standard_normal(n), 4000, 9000) * np.hanning(n)


class SampledVoices(Voices):
    """The same voices on FluidR3_GM programs (GM numbers 0-based); the sub and the air stay procedural."""

    PAD = Program(89, velocity=(45, 90), tail=PAD_RELEASE, rms=0.027)       # Warm Pad
    GLASS = Program(91, velocity=(40, 85), tail=GLASS_RELEASE, rms=0.2)    # Space Voice
    CELESTA = Program(8, velocity=(40, 100), tail=CELESTA_RING, rms=0.45)   # Celesta
    HARP = Program(46, velocity=(40, 100), tail=1.5, rms=0.2)               # Orchestral Harp
    PIANO = Program(0, velocity=(25, 70), tail=1.5, rms=0.4)               # Acoustic Grand, played softly

    def __init__(self, sampled, rng, sr=SR):
        super().__init__(rng, sr)
        self.s = sampled

    def pad(self, m, dur, vel=1.0):
        return self.s.play(self.PAD, m, dur, vel)

    def glass(self, m, dur, vel=1.0):
        return self.s.play(self.GLASS, m, dur, vel)

    def celesta(self, m, dur, vel=1.0):
        return self.s.play(self.CELESTA, m, dur, vel)

    def harp(self, m, dur, vel=1.0):
        return self.s.play(self.HARP, m, dur, vel)

    def piano(self, m, dur, vel=1.0):
        return self.s.play(self.PIANO, m, dur, vel)


class AmbientGenre(Genre):
    name = "ambient"
    description = "elegant ambient: slow swelling pads, high celesta, sustained sub, no drums, a lot of space"
    bpm = (50, 80)
    modes = ("lydian", "major", "dorian", "minor")
    voices = {"piano": "soft distant piano", "rhodes": "celesta", "nylon": "harp", "pad": "slow evolving pad"}

    def instruments(self, rng, sr, sampled):
        return Voices(rng, sr) if sampled is None else SampledVoices(sampled, rng, sr)

    def start(self, piece):
        piece.state["arp"] = ARPS[int(piece.rng.integers(len(ARPS)))]
        piece.state["sparkle"] = int(piece.rng.integers(len(SPARKLE)))

    def chord(self, chord, scale):
        """Add the ninth to every chord that lacks it (not to diminished or raised-dominant chords)."""
        d = chord.degree - 1
        if d < 0 or len(chord.tones) != 4:
            return chord

        def deg(k):
            return scale[(d + k) % 7] + 12 * ((d + k) // 7)

        if chord.tones[1] != deg(2) or deg(4) - deg(0) == 6:
            return chord
        return Chord(chord.degree, chord.tones + (deg(8),))

    def schedule(self, piece, p):
        s, v, add, st = piece.style, piece.inst, piece.add, piece.state
        B = 60 / s.bpm; BAR = 4 * B
        e, k = p.energy, p.key_offset
        quiet = p.section in ("intro", "breakdown")
        lead = "lead" in p.layers and s.lead != "none"
        # air: overlapping Hann-windowed hiss, one window per bar, two bars long
        add(v.air(2 * BAR), 0, 0.0, 0.006 + 0.006 * e)
        # pad bed: each chord swells in over its span and rings into the next; a passing chord takes beats 3-4
        spans = [(0.0, BAR if p.passing is None else 2 * B, p.chord)]
        if p.passing is not None:
            spans.append((2 * B, 2 * B, p.passing))
        tops = []
        for t0, dur, ch in spans:
            vc = piece.voice([x + k for x in ch.tones]); piece.center = float(np.mean(vc))
            root = 43 + (piece.ton + ch.tones[0] + k - 43) % 12
            notes = [root] + vc
            for i, m in enumerate(notes):
                add(v.pad(m, dur, 0.55 + 0.3 * e), t0, (i / max(1, len(notes) - 1) - 0.5) * 0.9,
                    0.5 if i else 0.4)
            if s.chords == "pad" and not quiet:   # a halo an octave above the top voice
                add(v.pad(vc[-1] + 12, dur, 0.4), t0, 0.4, 0.22)
            tops.append((t0, dur, sorted(vc, reverse=True)))
        # chord colour: a slow broken chord, thinner under the lead and in the quiet sections
        if s.chords != "pad":
            fn = {"piano": v.piano, "rhodes": v.celesta, "nylon": v.harp}[s.chords]
            up = 12 if s.chords == "rhodes" else 0
            shape = st["arp"][:1] if lead or quiet or e < 0.4 else st["arp"]
            for beat, idx in shape:
                t0, _, vc = tops[-1] if len(tops) > 1 and beat >= 2 else tops[0]
                m = vc[min(idx, len(vc) - 1)] + up
                add(fn(m, 2 * B, 0.5), beat * B, (0.35, -0.35)[idx % 2], 0.16)
        # bass: one sustained sub note per chord
        subs = [p.bass[0]] + ([n for n in p.bass if n.beat == 2][:1] if p.passing is not None else [])
        for i, n in enumerate(subs):
            end = subs[i + 1].beat if i + 1 < len(subs) else 4
            add(v.sub(n.midi, (end - n.beat) * B), n.beat * B, 0, 0.07 if p.section == "breakdown" else 0.1)
        # lead: only the strong-beat (chord) tones of the planned melody, ringing, with a quiet dotted echo
        if lead:
            fn = {"piano": v.piano, "rhodes": v.celesta, "nylon": v.harp, "pad": v.glass}[s.lead]
            dense = s.lead_density >= 0.67
            for n in p.melody:
                if n.beat not in (0, 2) and not (dense and n.beat == int(n.beat) and n.length >= 1):
                    continue
                m = n.midi
                while s.lead == "rhodes" and m < 72:
                    m += 12
                sig = fn(m, max(n.length, 1.5) * B, 0.8)
                g = 0.3
                add(sig, n.beat * B, 0.15, g)
                if s.lead in ("rhodes", "nylon"):
                    add(sig, (n.beat + 1.5) * B, -0.3, g * 0.3)
        # twinkle: when nobody sings, a few high celesta notes walk over the chord tones
        else:
            pcs = sorted({(piece.ton + x + k) % 12 for x in p.chord.tones})
            pitches = [m for m in range(76, 92) if m % 12 in pcs]
            for beat in range(4):
                forced = quiet and p.section_bar % 2 == 0 and beat == 1
                if not forced and piece.rng.random() > 0.2 + 0.25 * e:
                    continue
                st["sparkle"] += 1
                m = pitches[SPARKLE[st["sparkle"] % len(SPARKLE)] % len(pitches)]
                vel = 0.55 + 0.4 * piece.rng.random()
                pan = 0.6 * np.sin(st["sparkle"] * 1.7)
                sig = v.celesta(m, B, vel)
                add(sig, beat * B + 0.5 * B * (st["sparkle"] % 2), pan, 0.12)
                add(sig, (beat + 1.5) * B, -pan, 0.035)
        # counter-melody: a slow glass pad line under the lead
        if "counter" in p.layers:
            for n in p.counter:
                add(v.glass(n.midi, n.length * B, 0.6), n.beat * B, -0.3, 0.24)

    def master(self, s):
        return Master(space=0.6 + 0.4 * s.space, reverb=0.32 + 0.2 * s.space, room=0.5 + 0.8 * s.space, vinyl=0.0,
                      wow=0.08, tone=int(5000 * 2.4 ** s.brightness), drive=1.2, threshold=-22.0, ratio=2.0)


GENRE = AmbientGenre()
