"""Rock / metal: power chords on double-tracked distorted guitars, a picked bass, driving drums and a lead guitar.

The target is well-produced MIDI rock rather than a recorded band. The rhythm guitar plays power chords (root,
fifth, octave) as palm-muted eighths with ringing open chords on section downbeats, recorded twice (two slightly
different takes, panned hard left and right). The bass guitar follows the composer's roots in eighths, the lead
guitar plays the melody (with vibrato in the B sections) and the drums keep a straight rock beat: kick on 1 and 3,
snare on 2 and 4, eighth hats or ride, a crash on every section start. The master is clean (no vinyl, no tape wow),
a tight room, a bright-mid tone and more compression and drive than lo-fi.

One genre name covers both flavors: rock by default; `Style.drums == "busy"` with `Style.energy >= 0.7` is metal
(16th-note chugs and gallops, double kick, china accents, more drive). This keeps the LLM schema and presets
unchanged (no separate "metal" value). Only originally composed material.

Procedural voices: saw-wave chords through `synth.distort` and a cabinet-like band-pass (palm mute = a short decay
and a low-pass before the distortion); synthesized drums. With samples: FluidR3_GM 30 Distortion Guitar,
29 Overdriven Guitar, 27 Clean Guitar, 34 Picked Bass, 48 Strings and the Power drum kit (16).
"""
from __future__ import annotations

import numpy as np

from ..composer import FIRST_FORM
from ..samples import Hit, Program
from ..synth import SR, adsr, bp, distort, hp, lp, mtof, osc
from .base import Genre, Master
from .lofi import Instruments as LofiInstruments

SECTION_BARS = dict(FIRST_FORM)
# rhythm guitar over a bar of 16ths: "m" palm-muted chug, "o" open accent (rings to the next hit), "." rest
ROCK_PATTERNS = ("m.m.m.m.m.m.m.m.", "m.m.m.o...m.m.m.", "m.m.m.m.o...m.m.", "m.m.o.m.m.m.o.m.")
METAL_PATTERNS = ("m.mmm.mmm.mmm.mm", "mmmmmmmmmmmmmmmm", "m.mmm.mmmmm.m.mm")
ROCK_KICKS = ((0, 2), (0, 2, 2.5), (0, 1.5, 2), (0, 0.5, 2))   # quarter-note beats
DRUMS = ("kick", "snare", "hat", "open_hat", "ride", "crash", "china", "tom")
# Style.chords / Style.lead -> (drive, cabinet top in Hz) for the procedural guitar; "nylon" is a clean guitar
TONES = {"piano": (16.0, 4800), "rhodes": (6.0, 5200), "nylon": (1.4, 6500), "pad": (16.0, 4800)}
VIBRATO_CENTS = 35
# drum levels: RMS of the first 0.5 s, shared by both backends (`samples.Hit.rms`)
DRUM_RMS = {"kick": 0.30, "snare": 0.20, "tom": 0.18, "hat": 0.05, "open_hat": 0.05, "ride": 0.05, "crash": 0.08,
            "china": 0.08}


def _norm(x: np.ndarray, rms: float, sr: int = SR) -> np.ndarray:
    head = x[:sr // 2]
    return x * (rms * np.sqrt(sr / 2 / len(head)) / max(float(np.sqrt(np.mean(head ** 2))), 1e-9))


def _fade(x: np.ndarray, sr: int, ms: float = 8) -> np.ndarray:
    k = min(len(x), int(ms / 1000 * sr))
    x[len(x) - k:] *= np.linspace(1, 0, k)
    return x


def _vibrato(x: np.ndarray, depth: float, sr: int = SR, rate: float = 5.5) -> np.ndarray:
    """Delayed-onset pitch vibrato of `depth` x VIBRATO_CENTS, as a modulated fractional delay."""
    if depth <= 0:
        return x
    n = len(x); t = np.arange(n) / sr
    ratio = 2 ** (depth * VIBRATO_CENTS / 1200) - 1          # peak relative frequency deviation
    d = ratio / (2 * np.pi * rate) * sr * np.clip((t - 0.12) / 0.25, 0, 1) * np.sin(2 * np.pi * rate * t)
    return np.interp(np.clip(np.arange(n) - d, 0, n - 1), np.arange(n), x)


class Instruments:
    """Procedural rock voices. Every sound is rendered once per piece and cached (piece rng, so deterministic)."""

    def __init__(self, rng: np.random.Generator, sr: int = SR):
        self.rng, self.sr = rng, sr
        self._lofi = LofiInstruments(rng, sr)
        self._cache: dict[tuple, np.ndarray] = {}

    def _cached(self, key, make):
        x = self._cache.get(key)
        if x is None:
            x = self._cache[key] = make()
        return x

    def _t(self, n):
        return np.arange(n) / self.sr

    # ------------------------------------------------------------ tonal
    def guitar(self, notes, dur, vel=1.0, tone="piano", mute=False, take=0):
        """A chord on a distorted guitar; `take` 0/1 are two slightly different performances (double tracking)."""
        d = max(1, round(dur / 0.05)) * 0.05
        return self._cached(("gtr", tuple(notes), d, round(vel * 8), tone, mute, take),
                            lambda: self._guitar(notes, d, vel, tone, mute, take))

    def _guitar(self, notes, dur, vel, tone, mute, take):
        drive, cab = TONES[tone]
        rel = 0.03 if mute else 0.25
        n = int((dur + rel) * self.sr); t = self._t(n)
        cents = ((0, -5, 4), (-4, 3, -2))[take % 2]
        x = sum(osc("saw", mtof(m) * 2 ** (cents[i % 3] / 1200), n, self.sr, phase=self.rng.random())
                for i, m in enumerate(notes)) / len(notes)
        if mute:   # palm mute: dark and short before the amp, so it chugs instead of ringing
            x = lp(x, 500 + 500 * vel) * np.exp(-t * 26) * 2.2
        else:
            x = lp(x, 3200) * (0.55 + 0.45 * np.exp(-t * 1.2))
        x = x + lp(self.rng.standard_normal(n), 2500) * np.exp(-t * 300) * 0.15    # pick
        y = distort(x * vel, drive) / np.tanh(drive * 0.5)
        y = lp(bp(y, 80, cab * (1.0 if take % 2 == 0 else 0.86)), cab)          # cabinet
        env = adsr(n, self.sr, attack=0.002, decay=0.0, sustain=1.0, release=rel, hold=dur)
        if mute:
            env *= np.exp(-t * 12)
        return _fade(y * env * 0.35, self.sr)

    def lead(self, m, dur, vel=1.0, tone="piano", vibrato=0.0):
        vib = vibrato
        if tone == "pad":
            return _vibrato(self.strings(m, dur, vel) * 2.2, vib, self.sr)
        d = max(1, round(dur / 0.05)) * 0.05
        return self._cached(("lead", int(m), d, round(vel * 8), tone, round(vib, 2)),
                            lambda: self._lead(m, d, vel, tone, vib))

    def _lead(self, m, dur, vel, tone, vib):
        drive, cab = TONES[tone]
        n = int((dur + 0.2) * self.sr)
        x = (osc("saw", mtof(m), n, self.sr, phase=self.rng.random())
             + 0.4 * osc("square", mtof(m) * 1.003, n, self.sr, phase=self.rng.random()))
        y = distort(lp(x, 2800) * 0.7 * vel, drive) / np.tanh(drive * 0.5)
        y = lp(bp(y, 180, cab), cab)
        env = adsr(n, self.sr, attack=0.006, decay=0.15, sustain=0.85, release=0.2, hold=dur)
        return _fade(_vibrato(y * env * 0.32, vib, self.sr), self.sr)

    def strings(self, m, dur, vel=1.0):
        d = max(1, round(dur / 0.05)) * 0.05
        return self._cached(("str", int(m), d, round(vel * 8)), lambda: _fade(self._lofi.pad(m, d, vel), self.sr))

    def bass(self, m, dur, vel=1.0):
        d = max(1, round(dur / 0.05)) * 0.05
        return self._cached(("bass", int(m), d, round(vel * 8)), lambda: self._bass(m, d, vel))

    def _bass(self, m, dur, vel):
        n = int((dur + 0.05) * self.sr); t = self._t(n); f = mtof(m)
        x = lp(osc("saw", f, n, self.sr, phase=self.rng.random()), 700 + 500 * vel) + 0.8 * np.sin(2 * np.pi * f * t)
        x = distort(x * (0.6 + 0.4 * vel), 1.8)
        x = x + hp(self.rng.standard_normal(n), 1500) * np.exp(-t * 400) * 0.1    # pick click
        env = adsr(n, self.sr, attack=0.003, decay=0.2, sustain=0.75, release=0.05, hold=dur)
        return _fade(x * env * 0.45, self.sr)

    # ------------------------------------------------------------ drums
    def _drum(self, name, make):
        """`make()` at the drum's level in DRUM_RMS."""
        def norm():
            x = make()
            x *= np.minimum(1, np.arange(len(x)) / (0.0007 * self.sr))   # no click at the onset
            return _fade(_norm(x, DRUM_RMS[name], self.sr), self.sr)
        return self._cached(("drum", name), norm)

    def _noise(self, seconds):
        n = int(seconds * self.sr)
        return self.rng.standard_normal(n), self._t(n)

    def kick(self):
        def make():
            n = int(0.35 * self.sr); t = self._t(n); f = 50 + 120 * np.exp(-t * 40)
            body = np.sin(2 * np.pi * np.cumsum(f) / self.sr) * np.exp(-t * 9)
            click = hp(self.rng.standard_normal(n), 2500) * np.exp(-t * 500) * 0.4
            return distort(body * 1.3 + click, 1.5) * 0.75
        return self._drum("kick", make)

    def snare(self):
        def make():
            w, t = self._noise(0.35)
            x = (bp(w, 1500, 9000) * np.exp(-t * 11) * 0.9
                 + (np.sin(2 * np.pi * 185 * t) + 0.5 * np.sin(2 * np.pi * 330 * t)) * np.exp(-t * 22) * 0.9)
            return distort(x, 2.5)   # a compressed, cracking snare
        return self._drum("snare", make)

    def tom(self):
        def make():
            n = int(0.4 * self.sr); t = self._t(n); f = 95 + 50 * np.exp(-t * 18)
            return np.sin(2 * np.pi * np.cumsum(f) / self.sr) * np.exp(-t * 8) * 0.7
        return self._drum("tom", make)

    def hat(self):
        def make():
            w, t = self._noise(0.08)
            return lp(hp(w, 7500), 13000) * np.exp(-t * 70) * 0.55
        return self._drum("hat", make)

    def open_hat(self):
        def make():
            w, t = self._noise(0.45)
            return lp(hp(w, 6500), 13000) * np.exp(-t * 8) * 0.4
        return self._drum("open_hat", make)

    def ride(self):
        def make():
            w, t = self._noise(1.2)
            bell = sum(np.sin(2 * np.pi * f * t) * a for f, a in ((540, .3), (1263, .2), (2710, .15), (3930, .1)))
            return (hp(w, 5000) * 0.25 + bell * np.exp(-t * 6)) * np.exp(-t * 3) * 0.5
        return self._drum("ride", make)

    def crash(self):
        def make():
            w, t = self._noise(1.8)
            return (hp(w, 3500) + 0.4 * bp(w, 1200, 4000)) * np.minimum(1, t / 0.002) * np.exp(-t * 2.6) * 0.5
        return self._drum("crash", make)

    def china(self):
        def make():
            w, t = self._noise(1.0)
            return distort(bp(w, 1400, 7000) * 2, 2) * np.minimum(1, t / 0.002) * np.exp(-t * 5) * 0.35
        return self._drum("china", make)


class SampledInstruments(Instruments):
    """The same voices on FluidR3_GM programs and its Power drum kit (the shared `SampleInstruments`)."""

    GUITAR = {"piano": 30, "rhodes": 29, "nylon": 27, "pad": 30}
    DRUM_KEYS = {"kick": (36, 110, 0.4), "snare": (38, 110, 0.4), "hat": (42, 90, 0.15), "open_hat": (46, 90, 0.5),
                 "ride": (51, 95, 1.2), "crash": (49, 110, 1.8), "china": (52, 105, 1.2),
                 "tom": (45, 105, 0.5)}   # GM key, MIDI velocity, seconds

    def __init__(self, shared, rng: np.random.Generator, sr: int = SR):
        super().__init__(rng, sr)
        self.s = shared
        level = {k: 0.09 if k == "nylon" else 0.15 for k in self.GUITAR}   # the clean guitar adds up louder
        self._open = {k: Program(p, velocity=(70, 122), tail=0.35, rms=level[k]) for k, p in self.GUITAR.items()}
        self._mute = {k: Program(p, velocity=(60, 115), tail=0.04, rms=level[k]) for k, p in self.GUITAR.items()}
        self._lead = {**{k: Program(p, velocity=(70, 120), tail=0.3, rms=0.24) for k, p in self.GUITAR.items()},
                      "pad": Program(48, velocity=(70, 120), tail=0.6, rms=0.2)}
        self._strings = Program(48, velocity=(50, 100), tail=0.8, rms=0.1)
        self._bass = Program(34, velocity=(70, 120), tail=0.1, rms=0.25)

    def _guitar(self, notes, dur, vel, tone, mute, take):
        v = vel * (1.0 if take % 2 == 0 else 0.86)
        prog = (self._mute if mute else self._open)[tone]
        hold = 0.05 if mute else dur
        parts = [self.s.play(prog, m, hold, v) for m in notes]
        n = max(len(p) for p in parts)
        x = np.zeros(n)
        for p in parts:
            x[:len(p)] += p
        x = hp(x, 80)
        if mute:
            t = self._t(n)
            x = lp(x, 900 + 600 * vel) * np.exp(-t * 18) * 2.2
        elif take % 2:
            x = lp(x, 4200)
        return _fade(x, self.sr)

    def lead(self, m, dur, vel=1.0, tone="piano", vibrato=0.0):
        vib = vibrato
        d = max(1, round(dur / 0.05)) * 0.05
        return self._cached(("lead", int(m), d, round(vel * 8), tone, round(vib, 2)), lambda: _fade(
            _vibrato(np.array(self.s.play(self._lead[tone], m, d, vel)), vib, self.sr), self.sr))

    def strings(self, m, dur, vel=1.0):
        return self.s.play(self._strings, m, dur, vel)

    def bass(self, m, dur, vel=1.0):
        return self.s.play(self._bass, m, dur, vel)

    def _hit(self, name):
        key, v, secs = self.DRUM_KEYS[name]
        return self.s.hit(Hit(16, key, v, secs, DRUM_RMS[name]))

    def kick(self): return self._hit("kick")
    def snare(self): return self._hit("snare")
    def tom(self): return self._hit("tom")
    def hat(self): return self._hit("hat")
    def open_hat(self): return self._hit("open_hat")
    def ride(self): return self._hit("ride")
    def crash(self): return self._hit("crash")
    def china(self): return self._hit("china")


class RockGenre(Genre):
    name = "rock"
    description = ("rock or metal: distorted guitar power chords, palm mutes, driving drums "
                   "(busy drums with energy 0.7+ is metal, with double kick)")
    bpm = (90, 140)
    modes = ("minor", "phrygian", "dorian", "mixolydian")
    voices = {"piano": "distorted guitar", "rhodes": "overdriven guitar", "nylon": "clean electric guitar",
              "pad": "distorted guitar with a string section"}

    @staticmethod
    def flavor(style) -> str:
        return "metal" if style.drums == "busy" and style.energy >= 0.7 else "rock"

    def instruments(self, rng, sr, sampled):
        return Instruments(rng, sr) if sampled is None else SampledInstruments(sampled, rng, sr)

    def start(self, piece):
        metal = self.flavor(piece.style) == "metal"
        pats = METAL_PATTERNS if metal else ROCK_PATTERNS
        piece.state["pattern"] = pats[int(piece.rng.integers(len(pats)))]
        piece.state["kicks"] = ROCK_KICKS[int(piece.rng.integers(len(ROCK_KICKS)))]
        piece.state["lag"] = float(piece.rng.uniform(0.006, 0.012))   # the right take plays a hair late

    # ------------------------------------------------------------ per bar
    def schedule(self, piece, p):
        s, ins, add, rng = piece.style, piece.inst, piece.add, piece.rng
        B = 60 / s.bpm; e, k = p.energy, p.key_offset
        metal = self.flavor(s) == "metal"
        quiet = e < 0.45 or p.section in ("intro", "breakdown")

        def power(chord):
            r = (piece.ton + chord.tones[0] + k - 40) % 12 + 40
            return (r, r + 7, r + 12)

        def chord_at(beat):
            return p.passing if p.passing is not None and beat >= 2 else p.chord

        def strum(beat, dur, mute, vel):
            notes = power(chord_at(beat))
            t = beat * B
            g = (0.85 if mute else 0.6) * (0.8 + 0.3 * e)
            add(ins.guitar(notes, dur, vel, s.chords, mute, 0), t + 0.0015 * rng.random(), -0.75, g)
            add(ins.guitar(notes, dur, vel * 0.97, s.chords, mute, 1),
                t + piece.state["lag"] + 0.003 * rng.random(), 0.75, g * 0.92)

        # rhythm guitar
        if quiet:
            beats = (0, 2) if p.passing is not None else (0,)
            for b in beats:
                strum(b, (4 - b if len(beats) == 1 else 2) * B * 0.97, False, 0.75)
        else:
            pat = piece.state["pattern"]
            hits = [(i, c) for i, c in enumerate(pat) if c != "."]
            if p.section_bar == 0:   # section downbeat: an open chord rings for a beat first
                hits = [(0, "o")] + [(i, c) for i, c in hits if i >= 4]
            if p.passing is not None and 8 not in [i for i, _ in hits]:
                hits = sorted(hits + [(8, "m")])
            for j, (i, c) in enumerate(hits):
                nxt = hits[j + 1][0] if j + 1 < len(hits) else 16
                if c == "o":
                    strum(i / 4, (nxt - i) * B / 4 * 0.95, False, 0.95)
                else:
                    strum(i / 4, B / 4 * 0.9, True, 1.0 if i % 4 == 0 else 0.85)
        if s.chords == "pad":   # a string section holds the chord behind the guitars
            vc = piece.voice([x + k for x in p.chord.tones]); piece.center = float(np.mean(vc))
            for i, m in enumerate(vc):
                add(ins.strings(m, (2 if p.passing is not None else 4) * B * 0.98, 0.7), i * 0.02,
                    (i - 1.5) * 0.25, 0.10)

        # bass guitar: the composer's roots, as held notes when quiet, in driving eighths otherwise
        if quiet:
            for n in p.bass:
                add(ins.bass(n.midi, n.length * B * 0.95, 0.75), n.beat * B, 0, 0.5)
        else:
            for i in range(8):
                n = [x for x in p.bass if x.beat <= i / 2][-1]
                add(ins.bass(n.midi, B / 2 * 0.85, 1.0 if i % 2 == 0 else 0.85), i * B / 2, 0, 0.5)

        if "drums" in p.layers:
            self._drums(piece, p, metal, B)

        # lead guitar on the melody; vibrato in the B sections, wider on long notes
        if "lead" in p.layers and s.lead != "none":
            for n in p.melody:
                vib = (0.6 + 0.4 * (n.length >= 1)) if p.section.startswith("B") else 0.0
                add(ins.lead(n.midi, n.length * B * 0.95, 0.9, s.lead, vibrato=vib),
                    n.beat * B + 0.004 * rng.random(), 0.12, 0.55)
        if "counter" in p.layers:
            for n in p.counter:
                add(ins.strings(n.midi, n.length * B * 0.95, 0.6), n.beat * B + 0.02, -0.3, 0.12)

    def _drums(self, piece, p, metal, B):
        ins, add, rng, e = piece.inst, piece.add, piece.rng, p.energy
        dg = 0.75 + 0.35 * e
        last = p.section_bar == SECTION_BARS.get(p.section, 4) - 1 and e >= 0.5
        big = p.section.startswith("B")
        if p.section_bar == 0:
            add(ins.crash(), 0.0, 0.35, 0.5 * dg)
        # kick
        if metal:
            step = 4 if e >= 0.6 else 2
            kicks = [i / step for i in range(4 * step)]
        else:
            kicks = list(piece.state["kicks"] if e >= 0.5 else (0, 2))
        for b in kicks:   # the double kick sits lower: a rumble under the guitars, beats a little louder
            add(ins.kick(), b * B, 0, (0.75 if b == int(b) else 0.6) * dg * (0.65 if metal else 1.0))
        # snare on 2 and 4, a 16th-note fill closing each section
        for b in (1, 3):
            add(ins.snare(), b * B, 0.05, 0.6 * dg)
        if last:
            for i, b in enumerate((3.25, 3.5, 3.75)):
                add(ins.tom() if i < 2 and not metal else ins.snare(), b * B, -0.2 + 0.2 * i, 0.4 * dg)
        # cymbals
        if e < 0.5:
            for b in range(4):
                add(ins.hat(), b * B, -0.3, 0.35 * dg)
        elif big:
            for i in range(8):
                add(ins.ride(), i * B / 2, -0.3, (0.4 if i % 2 == 0 else 0.3) * dg)
            if metal:
                for b in (1, 3):
                    add(ins.china(), b * B, 0.4, 0.4 * dg)
        else:
            for i in range(8):
                if i == 7 and p.section_bar % 2 == 1:
                    add(ins.open_hat(), i * B / 2, -0.3, 0.3 * dg)
                else:
                    add(ins.hat(), i * B / 2, -0.3, (0.45 if i % 2 == 0 else 0.32) * dg * (0.9 + 0.2 * rng.random()))
            if metal and p.section_bar % 4 == 0 and p.section_bar:
                add(ins.crash(), 0.0, -0.35, 0.4 * dg)

    def master(self, s):
        metal = self.flavor(s) == "metal"
        return Master(space=0.3 * s.space, reverb=0.04 + 0.08 * s.space, room=0.1 + 0.5 * s.space, vinyl=0.0,
                      wow=0.0, tone=int(7000 * 2 ** s.brightness), drive=2.8 if metal else 2.2,
                      threshold=-24.0 if metal else -22.0, ratio=4.5 if metal else 3.5)


GENRE = RockGenre()
