"""Tango: the orquesta tipica's marcato and 3-3-2 sincopa, bandoneon and violin melodies, no drum kit (S5).

Time: `Style.bpm` counts the half note of the tango's 4/4 (60-70, a quarter of about 120-140), so an engine bar
holds two tango bars of two engine beats each. Every tango bar is a figure: marcato in 4 (four staccato chords, the
accents on 1 and 3), the 3-3-2 sincopa (strokes on the 1st, 4th and 7th eighth, the first two accented) or, in the
breakdown, legato chords held by the strings. The A sections are mostly marcato, the B sections mostly sincopa. The
double bass marks 1 and 3 on the chord's root (the composer's tonic pedal in a moment's tension), takes the
composer's approach note into the next chord and, closing the A sections, a chromatic line; with the piano colour the
piano left hand doubles the accents in octaves. The percussion is the ensemble's: string arrastres (a low string note
swelling into the tango downbeat and stopping on it) and chicharra chops (the bow scraped behind the bridge) on the
off-beats of the marcato. The melody sings on the bandoneon or the violin (doubled by the other an octave apart in
B'), the counter-melody on the other one. Harmony: plain triads, the dominant and diminished sevenths kept (the
composer already raises the dominant in minor). The level swells across every section and with the form's energy;
the master is a warm room with light compression, so the swells survive.

Procedural voices: the lo-fi piano and nylon, the cumbia accordion's tango registration (bandoneon), the orchestral
bowed strings and violins and a plucked double bass, all scaled to the reference levels of `PARTS`; the chop is
synthesized on both backends. With samples: GM 0 piano, 23 tango accordion (bandoneon), 24 nylon guitar, 32 acoustic
bass, 40 violin and 48 strings; the recorded library replaces any of them it covers. Moments: the roll is a string
tremolo and piano octaves growing into an arrastre, the hit a sforzando of the whole ensemble, the stinger the tonic
held by everyone. Only originally composed material.
"""
from __future__ import annotations

from collections import OrderedDict
from functools import lru_cache

import numpy as np

from ..composer import FIRST_FORM, Chord
from ..samples import Program
from ..synth import SR, bp, lp, mtof
from .base import Genre, Master
from .cumbia import Instruments as Reeds
from .lofi import Instruments as LofiVoices
from .orchestral import Voices as Orchestra

SECTION_BARS = dict(FIRST_FORM)
# part: (GM program, MIDI velocity range, release tail in seconds, RMS of a reference note: middle C, 1 s, vel 0.8)
PARTS = {
    "piano": (0, (40, 112), 0.5, 0.20), "bandoneon": (23, (55, 118), 0.25, 0.13),
    "guitar": (24, (45, 110), 0.5, 0.15), "bass": (32, (60, 122), 0.15, 0.20),
    "violin": (40, (55, 115), 0.4, 0.12), "strings": (48, (50, 110), 0.6, 0.10),
    "staccato": (48, (65, 122), 0.12, 0.12),
}
CHORD_PART = {"piano": "piano", "rhodes": "bandoneon", "nylon": "guitar", "pad": "staccato"}
LEAD_PART = {"piano": "bandoneon", "rhodes": "bandoneon", "nylon": "violin", "pad": "violin"}
OTHER = {"bandoneon": "violin", "violin": "bandoneon"}
# figures of a tango bar: (beat inside it, velocity, length in beats)
FIGURES = {"marcato": ((0.0, 1.0, 0.28), (0.5, 0.62, 0.22), (1.0, 0.9, 0.28), (1.5, 0.62, 0.22)),
           "sincopa": ((0.0, 1.0, 0.28), (0.75, 0.95, 0.55), (1.5, 0.66, 0.22)),
           "legato": ((0.0, 0.7, 1.96),)}
BASS_RANGE = (31, 52)
MIX = 1.7                  # overall level against the other genres
CHOP_RMS = 0.09
DUR_STEP = 0.05


def _rms(x: np.ndarray, sr: int) -> float:
    return float(np.sqrt(np.mean(x[:sr // 2] ** 2))) + 1e-9


def _fade(x: np.ndarray, sr: int, ms: float = 4) -> np.ndarray:
    k = min(len(x), int(ms / 1000 * sr))
    x[len(x) - k:] *= np.linspace(1, 0, k)
    return x


def figures(p) -> tuple[str, str]:
    """The figure of each tango bar (the two halves of the engine's bar)."""
    odd = p.section_bar % 2 == 1
    if p.section == "breakdown":
        return "legato", "legato"
    if p.section in ("A", "A'"):
        return "marcato", "sincopa" if odd else "marcato"
    if p.section in ("B", "B'"):
        return "sincopa", "sincopa" if odd else "marcato"
    return "marcato", "marcato"


class _Raw:
    """The unscaled procedural sources of every part."""

    def __init__(self, rng: np.random.Generator, sr: int):
        self.rng, self.sr = rng, sr
        self.lofi, self.reeds, self.orch = LofiVoices(rng, sr), Reeds(rng, sr), Orchestra(rng, sr)

    def render(self, part, m, dur, vel):
        if part == "piano":
            return self.lofi.piano(m, dur, vel)
        if part == "guitar":
            return self.lofi.nylon(m, dur, vel)
        if part == "bandoneon":   # the accordion's tango registration: two reeds and the low bandoneon reed
            return self.reeds.accordion(m, dur, vel, "rhodes")
        if part == "bass":
            return self._bass(m, dur, vel)
        return self.orch.note({"violin": "violins", "strings": "strings", "staccato": "spiccato"}[part], m, dur, vel)

    def _bass(self, m, dur, vel):
        """A plucked double bass: decaying partials, the finger on the string, a short release."""
        f = mtof(m); n = int((dur + 0.12) * self.sr); t = np.arange(n) / self.sr
        s = sum(np.sin(2 * np.pi * k * f * t + self.rng.random() * 0.3) * k ** -1.3 * np.exp(-t * (1.5 + 1.2 * k))
                for k in range(1, 7))
        s += lp(self.rng.standard_normal(n), 450, self.sr) * np.exp(-t * 50) * 0.4
        env = np.minimum(1, t / 0.004) * np.clip((dur + 0.12 - t) / 0.12, 0, 1)
        return lp(s * env, 900 + 900 * vel, self.sr) * vel


@lru_cache(maxsize=4)
def _scales(sr: int) -> dict[str, float]:
    """Gain that gives each procedural part's reference note the RMS of `PARTS` (the sampled programs use it too)."""
    raw = _Raw(np.random.default_rng(0), sr)
    return {part: PARTS[part][3] / _rms(np.asarray(raw.render(part, 60, 1.0, 0.8)), sr) for part in PARTS}


class Voices:
    """Procedural tango ensemble. Notes are bucketed (0.05 s, eighths of velocity) and cached per piece."""

    def __init__(self, rng: np.random.Generator, sr: int = SR):
        self.rng, self.sr = rng, sr
        self._cache: OrderedDict[tuple, np.ndarray] = OrderedDict()
        self._raw: _Raw | None = None
        self._chops = [self._make_chop() for _ in range(3)]

    def _cached(self, key, make):
        x = self._cache.get(key)
        if x is None:
            x = make(); x.flags.writeable = False
            self._cache[key] = x
            if len(self._cache) > 1024:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(key)
        return x

    def note(self, part: str, m, dur: float, vel: float = 1.0) -> np.ndarray:
        """MIDI note `m` on `part` (a PARTS name) held `dur` seconds at velocity `vel` (0-1)."""
        d = max(1, round(dur / DUR_STEP)) * DUR_STEP
        vb = int(np.clip(round(vel * 8), 1, 8))
        return self._cached((part, int(m), d, vb), lambda: self._note(part, int(m), d, vb / 8))

    def _note(self, part, m, dur, vel):
        if self._raw is None:
            self._raw = _Raw(self.rng, self.sr)
        x = np.array(self._raw.render(part, m, dur, vel), dtype=float) * _scales(self.sr)[part]
        return _fade(x, self.sr, 10)

    def drag(self, m, dur: float, vel: float = 1.0) -> np.ndarray:
        """Arrastre: a low string note swelling over `dur` seconds, with the bow's noise, cut on the downbeat."""
        d = round(dur, 2)
        vb = int(np.clip(round(vel * 8), 1, 8))

        def make():
            n = int(d * self.sr); t = np.arange(n) / self.sr
            x = np.array(self.note("strings", m, d + 0.1, vb / 8), dtype=float)[:n]
            x = np.pad(x, (0, n - len(x)))
            bow = bp(self.rng.standard_normal(n), 900, 5000, self.sr)
            bow *= 0.6 * float(np.sqrt(np.mean(x ** 2))) / (float(np.sqrt(np.mean(bow ** 2))) + 1e-9)
            return _fade(x * (0.05 + 0.95 * (t / d) ** 1.5) + bow * (t / d) ** 3, self.sr)
        return self._cached(("drag", int(m), d, vb), make)

    def chop(self, vel: float = 1.0) -> np.ndarray:
        """Chicharra: the bow scraped behind the bridge, a short ridged noise with a knock of the body."""
        return self._chops[int(self.rng.integers(len(self._chops)))] * vel

    def _make_chop(self):
        n = int(0.14 * self.sr); t = np.arange(n) / self.sr
        ridges = (0.5 + 0.5 * np.sin(2 * np.pi * (85 + 20 * self.rng.random()) * t)) ** 2
        scrape = bp(self.rng.standard_normal(n), 1800, 7000, self.sr) * ridges * np.exp(-t * 22)
        knock = np.sin(2 * np.pi * 180 * t) * np.exp(-t * 45) * 0.5
        x = (scrape + knock) * np.minimum(1, t / 0.002)
        return _fade(x * CHOP_RMS / float(np.sqrt(np.mean(x ** 2))), self.sr)


class SampledVoices(Voices):
    """The same parts on GM programs (the shared `SampleInstruments`, recorded library first); the arrastre is
    shaped from the sampled strings and the chop stays synthesized."""

    def __init__(self, sampled, rng: np.random.Generator, sr: int = SR):
        super().__init__(rng, sr)
        self.s = sampled
        self._programs = {k: Program(p, velocity=v, tail=tail, rms=rms) for k, (p, v, tail, rms) in PARTS.items()}

    def note(self, part, m, dur, vel=1.0):
        return self.s.play(self._programs[part], m, dur, vel)


class TangoGenre(Genre):
    name = "tango"
    description = ("Argentine tango: marcato in 4 and the 3-3-2 sincopa on piano, double bass and strings, string "
                   "arrastres and chops instead of drums, bandoneon and violin melodies, minor-key drama with swells")
    bpm = (60, 70)
    modes = ("minor", "phrygian", "major")
    voices = {"piano": "piano marcato with bass octaves / bandoneon melody",
              "rhodes": "bandoneon section marcato / bandoneon melody",
              "nylon": "guitar marcato / violin melody",
              "pad": "staccato string section with arrastres / violin melody"}

    def instruments(self, rng, sr, sampled):
        return Voices(rng, sr) if sampled is None else SampledVoices(sampled, rng, sr)

    def chord(self, chord, scale):
        """Plain triads; dominant sevenths, diminished sevenths and the chromatic dominants keep their seventh."""
        if chord.degree == 0 or len(chord.tones) < 4:
            return chord
        iv = [t - chord.tones[0] for t in chord.tones[:4]]
        if (iv[1] == 4 and iv[3] == 10) or iv[2] == 6:
            return Chord(chord.degree, chord.tones[:4])
        return Chord(chord.degree, chord.tones[:3])

    # ------------------------------------------------------------ per bar
    @staticmethod
    def _level(p) -> float:
        """The form's energy times a swell across the section."""
        n = SECTION_BARS.get(p.section, 4)
        swell = 0.75 + 0.45 * min(1.0, p.section_bar / max(1, n - 1))
        return MIX * (0.35 + 0.65 * p.energy) * swell

    @staticmethod
    def _place(pc, lo=BASS_RANGE[0], hi=BASS_RANGE[1]):
        return lo + (pc - lo) % 12 if lo + (pc - lo) % 12 <= hi else lo + (pc - lo) % 12 - 12

    def _voicing(self, piece, chord, k):
        vc = piece.voice([x + k for x in chord.tones]); piece.center = float(np.mean(vc))
        return [m + 12 if m < 55 else m for m in vc]

    def schedule(self, piece, p):
        s, ins, add = piece.style, piece.inst, piece.add
        B, k, e = 60 / s.bpm, p.key_offset, p.energy
        lvl = self._level(p)
        perc = "drums" in p.layers
        figs = figures(p)
        halves = [(0, p.chord), (2, p.passing if p.passing is not None else p.chord)]
        roots = [(piece.ton + c.tones[0] + k) % 12 for _, c in halves]
        held = len(p.bass) == 1 and p.bass[0].length >= 4          # a moment's tonic pedal or final root
        approach = next((n.midi for n in p.bass if n.beat == 3.5), None)
        part = CHORD_PART[s.chords]
        last = p.section_bar == SECTION_BARS.get(p.section, 4) - 1

        for (h, chord), fig, root in zip(halves, figs, roots):
            vc = self._voicing(piece, chord, k)
            if fig == "legato":
                held_part = "bandoneon" if s.chords == "rhodes" else "strings"
                for i, m in enumerate(vc):
                    add(ins.note(held_part, m, 1.96 * B, 0.5 + 0.3 * e), h * B + 0.02 * i, (i - 1) * 0.3,
                        0.16 * lvl)
            else:
                for beat, vel, ln in FIGURES[fig]:
                    v = vel * (0.7 + 0.3 * e)
                    for i, m in enumerate(vc):
                        add(ins.note(part, m, ln * B, v), (h + beat) * B + 0.004 * i, (i - 1) * 0.25,
                            0.12 * lvl * vel)
            # bass on 1 and 3 (the sincopa's three strokes); the piano left hand doubles the accents in octaves
            r = self._place(roots[0] if held else root)
            if held:
                r = self._place(p.bass[0].midi % 12)
            fifth = r + 7 if r + 7 <= BASS_RANGE[1] else r - 5
            hits = {"marcato": ((0.0, r, 1.0, 0.45), (1.0, r if held else fifth, 0.85, 0.45)),
                    "sincopa": ((0.0, r, 1.0, 0.6), (0.75, r, 0.9, 0.6), (1.5, r if held else fifth, 0.75, 0.4)),
                    "legato": ((0.0, r, 0.75, 1.9),)}[fig]
            if h == 2 and not held and approach is not None:
                a = self._place(approach % 12)
                if last and p.section in ("A", "A'"):            # a chromatic line into the next section
                    step = 1 if a - 3 >= BASS_RANGE[0] else -1
                    hits = tuple((b, a - step * (3 - i), 0.85, 0.42) for i, b in enumerate((0.0, 0.5, 1.0, 1.5)))
                elif fig != "legato":
                    hits = tuple(x for x in hits if x[0] < 1.5) + ((1.5, a, 0.75, 0.4),)
            for beat, m, vel, ln in hits:
                add(ins.note("bass", m, ln * B, vel), (h + beat) * B, 0.0, 0.42 * lvl)
                if s.chords == "piano" and vel >= 0.9 and fig != "legato":
                    low = self._place(m % 12, 31, 42)
                    for j, o in enumerate((low, low + 12)):
                        add(ins.note("piano", o, 0.3 * B, vel), (h + beat) * B + 0.003 * j, -0.2, 0.13 * lvl)
            # the ensemble's percussion: chicharra chops off the marcato beats, an arrastre into the downbeat
            if perc and fig == "marcato" and e >= 0.5:
                for beat in (0.5, 1.5):
                    add(ins.chop(0.7 + 0.3 * e), (h + beat) * B, 0.3, (0.32 if s.drums == "busy" else 0.24) * lvl)
        if perc and e >= 0.45 and p.section != "breakdown":
            if figs[1] == "marcato":
                add(ins.drag(self._place(roots[1], 40, 51), 0.5 * B, 0.9), 1.5 * B, -0.3, 0.3 * lvl)
            if p.section_bar % 2 == 1 or last:
                into = approach % 12 if approach is not None else roots[1]
                add(ins.drag(self._place(into, 40, 51), 0.5 * B, 0.9), 3.5 * B, -0.3, 0.3 * lvl)

        lead = LEAD_PART.get(s.lead)
        if "lead" in p.layers and lead is not None:
            tutti = p.section in ("B'", "release") and e >= 0.6
            for n in p.melody:
                dur = n.length * B * 0.97
                add(ins.note(lead, n.midi, dur, 0.7 + 0.25 * e), n.beat * B, 0.15, 0.3 * lvl)
                if tutti:
                    other, octave = OTHER[lead], (12 if lead == "bandoneon" else -12)
                    add(ins.note(other, n.midi + octave, dur, 0.7), n.beat * B + 0.01, -0.2, 0.16 * lvl)
        if "counter" in p.layers:
            other = OTHER[lead] if lead is not None else "violin"
            for n in p.counter:
                add(ins.note(other, n.midi, n.length * B * 0.95, 0.6), n.beat * B + 0.02, -0.3, 0.14 * lvl)

    # ------------------------------------------------------------ moments
    def roll(self, piece, p):
        """Into the hit: a string tremolo on the chord and piano octaves growing over the bar, then an arrastre."""
        ins, add, B = piece.inst, piece.add, 60 / piece.style.bpm
        vc = self._voicing(piece, p.chord, p.key_offset)
        steps = 24
        for i in range(steps):
            g = 0.3 + 0.7 * i / (steps - 1)
            for j, m in enumerate(vc):
                add(ins.note("staccato", m, B / 6, g), i * B / 6 + 0.003 * j, (j - 1) * 0.25, 0.12 * MIX * g)
        low = self._place((piece.ton + p.chord.tones[0] + p.key_offset) % 12, 31, 42)
        for i, b in enumerate((2, 2.5, 3, 3.25, 3.5)):
            for o in (low, low + 12):
                add(ins.note("piano", o, 0.25 * B, 0.6 + 0.1 * i), b * B, -0.2, 0.14 * MIX)
        add(ins.drag(self._place(low % 12, 40, 51), 0.75 * B, 1.0), 3.25 * B, -0.3, 0.45 * MIX)

    def impact(self, piece, p):
        """The hit: a sforzando of the whole ensemble on the chord, piano and bass octaves, a chop."""
        ins, add, B = piece.inst, piece.add, 60 / piece.style.bpm
        g = MIX * (0.4 + 0.8 * p.energy ** 2)
        vc = self._voicing(piece, p.chord, p.key_offset)
        for i, m in enumerate(vc):
            add(ins.note("staccato", m, 0.4 * B, 1.0), 0.003 * i, (i - 1) * 0.3, 0.2 * g)
            add(ins.note("bandoneon", m, 0.9 * B, 1.0), 0.01, -(i - 1) * 0.3, 0.2 * g)
        low = self._place((piece.ton + p.chord.tones[0] + p.key_offset) % 12, 31, 42)
        for o in (low - 12 if low - 12 >= 24 else low, low, low + 12):
            add(ins.note("piano", o, 0.8 * B, 1.0), 0.0, -0.2, 0.22 * g)
        add(ins.note("bass", low, 1.2 * B, 1.0), 0.0, 0.0, 0.6 * g)
        add(ins.chop(1.0), 0.0, 0.3, 0.4 * g)

    def stinger(self, piece, p):
        """The final chord: everyone holds the tonic for a bar and a half and it rings out."""
        ins, add, B = piece.inst, piece.add, 60 / piece.style.bpm
        lvl, dur = MIX * (0.35 + 0.65 * p.energy), 6 * B
        vc = self._voicing(piece, p.chord, p.key_offset)
        for i, m in enumerate(vc):
            pan = (i - (len(vc) - 1) / 2) * 0.3
            add(ins.note("strings", m, dur, 0.85), 0.01 * i, pan, 0.2 * lvl)
            add(ins.note("bandoneon", m, dur, 0.8), 0.02, -pan, 0.14 * lvl)
            add(ins.note("piano", m, dur, 0.8), 0.0, pan, 0.1 * lvl)
        add(ins.note("violin", vc[-1] + 12, dur, 0.8), 0.03, 0.15, 0.14 * lvl)
        r = self._place((piece.ton + p.chord.tones[0] + p.key_offset) % 12)
        add(ins.note("bass", r, dur, 0.9), 0.0, 0.0, 0.42 * lvl)
        low = self._place(r % 12, 31, 42)
        for o in (low, low + 12):
            add(ins.note("piano", o, dur, 0.9), 0.0, -0.2, 0.13 * lvl)

    def master(self, s):
        return Master(space=0.3 + 0.4 * s.space, reverb=0.05 + 0.12 * s.space, room=0.35 + 0.6 * s.space,
                      vinyl=0.35 * s.vinyl, wow=0.04, tone=int(min(14000, 4200 * 2.4 ** s.brightness)), drive=1.5,
                      threshold=-18.0, ratio=2.0)   # light bus compression: keep the swells


GENRE = TangoGenre()
