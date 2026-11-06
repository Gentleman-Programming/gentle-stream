"""Orchestral (cinematic / epic): string ostinatos, low brass, a heroic horn line, timpani and big drums.

The trailer-music sound, built on the shared composer. Spiccato strings drive the bar with an ostinato on the
chord's root, fifth and octave (eighths, sixteenths in the B sections), a sustained string section holds the
voicing, the basses play the composer's bass line, trombones and tuba hold the roots once the energy rises and the
melody sings on french horns (trumpet, violins or choir by `Style.lead`). Timpani mark the downbeats and roll,
under a suspended-cymbal swell, into every new section; big drums and crashes join the B sections. The form sets
the size: the breakdown thins to a few held strings, B' and the release are the full tutti (the melody doubled an
octave up). Chords are plain triads. The master is clean (no vinyl, no tape wow) with a large hall.

Procedural voices: detuned band-limited saws with vibrato (strings), a filter that blooms on the attack (brass),
formant-filtered saws (choir), additive partials (harp, timpani) and synthesized drums. With samples: GM programs
48/49 strings, 40-42 violin, viola and cello (also the basses), 56 trumpet, 57 trombone, 58 tuba, 60 french horn, 61 brass section,
46 harp, 47 timpani, 52 choir, the Orchestra kit (48) bass drum, snare and cymbal and a Standard-kit floor tom as
the taiko-like hit; the recorded library replaces any of them it covers. The cymbal swell stays synthesized. Only
originally composed material.
"""
from __future__ import annotations

import numpy as np

from ..composer import FIRST_FORM, Chord
from ..samples import Hit, Program
from ..synth import SR, adsr, bp, distort, hp, lp, mtof, osc
from .base import Genre, Master

SECTION_BARS = dict(FIRST_FORM)
# ostinato over a bar of eighths (sixteenths repeat it twice): index into (root, fifth, octave)
OSTINATOS = ((0, 0, 2, 0, 1, 0, 2, 1), (0, 2, 1, 2, 0, 2, 1, 2), (0, 1, 2, 1, 0, 1, 2, 0), (0, 0, 1, 0, 2, 0, 1, 2))
BOOMS = ((0, 2), (0, 1.5, 2), (0, 0.75, 1.5, 2, 3), (0, 2, 2.5, 3.5))   # big-drum beats; beat 0 is the bass drum
LEADS = {"piano": "horn", "rhodes": "trumpet", "nylon": "violins", "pad": "choir"}
# part: (GM program, MIDI velocity range, release tail in seconds, RMS of a reference note), both backends. The
# basses play GM 42 (cello): FluidR3's 43 (contrabass) is silent at middle C, where `samples` measures levels.
PARTS = {
    "strings": (49, (45, 105), 0.8, 0.10), "spiccato": (48, (60, 120), 0.15, 0.12),
    "violins": (48, (60, 115), 0.6, 0.15), "violas": (41, (50, 105), 0.5, 0.08),
    "celli": (42, (60, 115), 0.2, 0.10), "basses": (42, (55, 110), 0.4, 0.16),
    "horn": (60, (55, 120), 0.5, 0.18), "trumpet": (56, (55, 120), 0.4, 0.15),
    "trombone": (57, (50, 115), 0.4, 0.10), "tuba": (58, (50, 115), 0.4, 0.13),
    "brass": (61, (50, 115), 0.5, 0.09), "harp": (46, (45, 105), 1.5, 0.12),
    "choir": (52, (45, 105), 0.8, 0.10), "timpani": (47, (50, 127), 1.6, 0.12),
}
# drum: (GM kit, key, MIDI velocity, seconds, RMS of the first 0.5 s)
DRUMS = {"boom": (48, 36, 120, 1.4, 0.30), "taiko": (0, 41, 120, 0.8, 0.24), "cymbal": (48, 49, 110, 2.5, 0.07),
         "snare": (48, 38, 100, 0.4, 0.10)}
MIX = 0.6                         # overall level against the other genres
DETUNE = (0.0, -9.0, 8.0, -4.0)   # cents of the section players


def _rms(x: np.ndarray, sr: int) -> float:
    return float(np.sqrt(np.mean(x[:sr // 2] ** 2))) + 1e-9


def _fade(x: np.ndarray, sr: int, ms: float = 10) -> np.ndarray:
    k = min(len(x), int(ms / 1000 * sr))
    x[len(x) - k:] *= np.linspace(1, 0, k)
    return x


class Voices:
    """Procedural orchestra. Every note is rendered once per piece and cached (piece rng, so deterministic)."""

    def __init__(self, rng: np.random.Generator, sr: int = SR):
        self.rng, self.sr = rng, sr
        self._cache: dict[tuple, np.ndarray] = {}

    def _cached(self, key, make):
        x = self._cache.get(key)
        if x is None:
            if len(self._cache) > 1024:
                self._cache.clear()
            x = self._cache[key] = make()
        return x

    def _t(self, n):
        return np.arange(n) / self.sr

    # ------------------------------------------------------------ public
    def note(self, part: str, m, dur: float, vel: float = 1.0) -> np.ndarray:
        """MIDI note `m` on `part` (a PARTS name) held `dur` seconds at velocity `vel` (0-1)."""
        d = max(1, round(dur / 0.05)) * 0.05 if part != "spiccato" else round(dur, 2)
        vb = int(np.clip(round(vel * 8), 1, 8))
        return self._cached((part, int(m), d, vb), lambda: self._note(part, int(m), d, vb / 8))

    def hit(self, name: str, vel: float = 1.0) -> np.ndarray:
        vb = int(np.clip(round(vel * 8), 1, 8))
        return self._cached(("hit", name, vb), lambda: self._hit(name) * (0.3 + 0.7 * vb / 8))

    def swell(self, dur: float) -> np.ndarray:
        """A suspended-cymbal roll growing for `dur` seconds (ends at full level: the next downbeat takes over)."""
        d = round(dur, 3)

        def make():
            n = int(round(d * self.sr)); t = self._t(n)
            w = self.rng.standard_normal(n)
            x = hp(w, 3000) + 0.35 * bp(w, 700, 3000)
            env = (t / d) ** 3 * np.minimum(1, (d - t) / 0.02 + 0.3)
            return _fade(x * env / _rms(hp(w, 3000), self.sr) * 0.08, self.sr, 3)
        return self._cached(("swell", d), make)

    # ------------------------------------------------------------ tonal
    def _note(self, part, m, dur, vel):
        *_, tail, rms = PARTS[part]
        make = {"strings": lambda: self._bowed(m, dur, 0.18, 4, 4200, tail),
                "violins": lambda: self._bowed(m, dur, 0.06, 4, 6000, tail),
                "violas": lambda: self._bowed(m, dur, 0.12, 3, 3500, tail),
                "basses": lambda: self._bowed(m, dur, 0.05, 3, 1400, tail),
                "spiccato": lambda: self._bowed(m, dur, 0.006, 3, 5000, 0.06, short=True),
                "celli": lambda: self._bowed(m, dur, 0.006, 3, 2600, 0.06, short=True),
                "horn": lambda: self._brass(m, dur, vel, 1, 1100, 2400, tail),
                "trumpet": lambda: self._brass(m, dur, vel, 1, 2200, 6500, tail),
                "trombone": lambda: self._brass(m, dur, vel, 2, 900, 2600, tail),
                "tuba": lambda: self._brass(m, dur, vel, 1, 400, 1100, tail),
                "brass": lambda: self._brass(m, dur, vel, 3, 1200, 3800, tail),
                "harp": lambda: self._harp(m),
                "choir": lambda: self._choir(m, dur, tail),
                "timpani": lambda: self._timpani(m, dur)}[part]
        x = make()
        ref = x if part in ("spiccato", "celli", "harp", "timpani") else x[:int(min(0.5, dur) * self.sr)]
        return _fade(x * (rms / _rms(ref, self.sr)) * (0.3 + 0.7 * vel) / 0.86, self.sr)

    def _bowed(self, m, dur, attack, players, cutoff, release, short=False):
        n = int((dur + release) * self.sr); t = self._t(n); f = mtof(m)
        vib = 1 + 0.003 * np.sin(2 * np.pi * 5.3 * t + self.rng.random() * 6) * np.clip((t - 0.2) / 0.4, 0, 1)
        x = sum(osc("saw", f * vib * 2 ** (c / 1200), n, self.sr, phase=self.rng.random())
                for c in DETUNE[:players]) / players
        x = lp(x, min(cutoff, 18000)) + 0.6 * bp(x, 250, 1100)    # bright bow, wooden body
        if short:   # spiccato: a bite of bow noise, then a quick decay
            x = x * np.exp(-t * 14) + bp(self.rng.standard_normal(n), 1500, 6000) * np.exp(-t * 120) * 0.15
        return x * adsr(n, self.sr, attack=attack, decay=0.2, sustain=0.85, release=release, hold=dur)

    def _brass(self, m, dur, vel, players, dark, bright, release):
        n = int((dur + release) * self.sr); t = self._t(n); f = mtof(m)
        x = sum(osc("saw", f * 2 ** (c / 1200), n, self.sr, phase=self.rng.random()) for c in DETUNE[:players])
        x = x / players
        bloom = np.clip(t / 0.06, 0, 1) * (1 - 0.35 * np.clip((t - 0.1) / 0.4, 0, 1)) * (0.4 + 0.6 * vel)
        lo, hi = lp(x, dark), lp(lp(x, bright), bright)
        y = lo + (hi - lo) * bloom                                  # the filter opens on the attack
        y = distort(y * (1 + 1.5 * vel), 1.2)
        return y * adsr(n, self.sr, attack=0.03, decay=0.15, sustain=0.85, release=release, hold=dur)

    def _harp(self, m):
        n = int(1.6 * self.sr); t = self._t(n); f = mtof(m)
        x = sum(np.sin(2 * np.pi * k * f * t + self.rng.random()) * k ** -1.4 * np.exp(-t * (1.6 + 0.7 * k))
                for k in range(1, 8) if k * f < 12000)
        return x * np.minimum(1, t / 0.002)

    def _choir(self, m, dur, release):
        n = int((dur + release) * self.sr); t = self._t(n); f = mtof(m)
        vib = 1 + 0.004 * np.sin(2 * np.pi * 4.8 * t + self.rng.random() * 6)
        x = sum(osc("saw", f * vib * 2 ** (c / 1200), n, self.sr, phase=self.rng.random()) for c in DETUNE[:3])
        y = bp(x, 550, 950) + 0.5 * bp(x, 1000, 1400) + 0.15 * bp(x, 2400, 3100)   # an open "ah"
        return y * adsr(n, self.sr, attack=0.3, decay=0.2, sustain=0.9, release=release, hold=dur)

    def _timpani(self, m, dur):
        n = int((max(dur, 0.1) + 1.6) * self.sr); t = self._t(n); f = mtof(m)
        bend = 1 + 0.02 * np.exp(-t * 25)                          # the head settles after the stroke
        x = sum(a * np.sin(2 * np.pi * np.cumsum(f * r * bend) / self.sr) * np.exp(-t * dk)
                for r, a, dk in ((1, 1, 2.2), (1.5, 0.5, 3.0), (1.99, 0.35, 3.5), (2.44, 0.2, 5.0)))
        x += lp(self.rng.standard_normal(n), 400) * np.exp(-t * 40) * 0.6   # felt mallet
        return x * np.minimum(1, t / 0.002)

    # ------------------------------------------------------------ drums
    def _hit(self, name):
        n = int(DRUMS[name][3] * self.sr); t = self._t(n); w = self.rng.standard_normal(n)
        if name == "boom":
            f = 38 + 30 * np.exp(-t * 18)
            x = np.sin(2 * np.pi * np.cumsum(f) / self.sr) * np.exp(-t * 2.5) + lp(w, 200) * np.exp(-t * 12) * 0.8
        elif name == "taiko":
            f = 58 + 45 * np.exp(-t * 25)
            x = np.sin(2 * np.pi * np.cumsum(f) / self.sr) * np.exp(-t * 5) + bp(w, 100, 900) * np.exp(-t * 25)
        elif name == "cymbal":
            x = (hp(w, 3500) + 0.4 * bp(w, 900, 4000)) * np.exp(-t * 1.6)
        else:   # concert snare
            x = bp(w, 1500, 8000) * np.exp(-t * 16) + np.sin(2 * np.pi * 200 * t) * np.exp(-t * 30) * 0.5
        x = x * np.minimum(1, t / 0.001)
        return _fade(x * DRUMS[name][4] / _rms(x, self.sr), self.sr)


class SampledVoices(Voices):
    """The same parts on GM programs and the Orchestra kit (the shared `SampleInstruments`, recorded library
    first); the cymbal swell stays synthesized."""

    def __init__(self, sampled, rng: np.random.Generator, sr: int = SR):
        super().__init__(rng, sr)
        self.s = sampled
        self._programs = {k: Program(p, velocity=v, tail=tail, rms=rms) for k, (p, v, tail, rms) in PARTS.items()}
        self._hits = {k: Hit(kit, key, v, secs, rms) for k, (kit, key, v, secs, rms) in DRUMS.items()}

    def note(self, part, m, dur, vel=1.0):
        return self.s.play(self._programs[part], m, dur, vel)

    def hit(self, name, vel=1.0):
        return self.s.hit(self._hits[name], 0.3 + 0.7 * vel)


class OrchestralGenre(Genre):
    name = "orchestral"
    description = ("cinematic epic orchestral (trailer music): string ostinatos, low brass, heroic horn melody, "
                   "timpani and big drums, choir; a hall that grows from thin strings to a full tutti")
    bpm = (66, 132)
    modes = ("minor", "dorian", "major", "lydian", "phrygian")
    voices = {"piano": "strings, horn melody", "rhodes": "brass section chords, trumpet melody",
              "nylon": "harp arpeggios, violin melody", "pad": "choir chords, choir melody"}

    def instruments(self, rng, sr, sampled):
        return Voices(rng, sr) if sampled is None else SampledVoices(sampled, rng, sr)

    def chord(self, chord, scale):
        return chord if chord.degree == 0 else Chord(chord.degree, chord.tones[:3])

    def start(self, piece):
        piece.state["ostinato"] = OSTINATOS[int(piece.rng.integers(len(OSTINATOS)))]
        piece.state["booms"] = BOOMS[int(piece.rng.integers(len(BOOMS)))]

    # ------------------------------------------------------------ per bar
    def schedule(self, piece, p):
        s, ins, add = piece.style, piece.inst, piece.add
        B = 60 / s.bpm; e, k, sec = p.energy, p.key_offset, p.section
        breakdown, quiet = sec == "breakdown", sec in ("intro", "breakdown")
        big, tutti = sec in ("B", "B'"), sec in ("B'", "release")
        lvl = MIX * (0.3 + 0.7 * e) * (0.6 if breakdown else 1.0)
        halves = ((0, p.chord, 4),) if p.passing is None else ((0, p.chord, 2), (2, p.passing, 2))

        def root(chord, lo):
            return (piece.ton + chord.tones[0] + k - lo) % 12 + lo

        def chord_at(beat):
            return p.passing if p.passing is not None and beat >= 2 else p.chord

        # the string section holds the voicing; the chord colour adds a brass section, a choir or a harp
        for beat, chord, beats in halves:
            vc = piece.voice([x + k for x in chord.tones]); piece.center = float(np.mean(vc))
            if breakdown:
                vc = vc[-2:]
            dur = beats * B * 0.98
            for i, m in enumerate(vc):
                pan = (i - (len(vc) - 1) / 2) * 0.35
                add(ins.note("strings", m, dur, 0.5 + 0.4 * e), beat * B + i * 0.015, pan, lvl)
                if s.chords == "rhodes" and not quiet:
                    add(ins.note("brass", m - 12, dur, 0.5 + 0.4 * e), beat * B + 0.02, -pan, lvl)
                elif s.chords == "pad":
                    add(ins.note("choir", m, dur, 0.6 + 0.3 * e), beat * B + 0.03, -pan, 0.8 * lvl)
        if s.chords == "nylon":   # harp: the chord broken upward in eighths
            for i in range(8):
                chord = chord_at(i / 2)
                vc = piece.voice([x + k for x in chord.tones])
                up = vc + [m + 12 for m in vc]
                add(ins.note("harp", up[i % len(up)], B / 2, 0.7), i * B / 2, 0.4, 0.9 * lvl)

        # spiccato ostinato on root, fifth and octave; celli double it an octave down in the tutti
        if not breakdown:
            step = 4 if big or (s.drums == "busy" and e >= 0.6 and not quiet) else 2
            pat, g = piece.state["ostinato"], (0.55 if quiet else 1.0) * lvl
            for i in range(4 * step):
                chord = chord_at(i / step)
                r = root(chord, 48)
                m = (r, r + chord.tones[2] - chord.tones[0], r + 12)[pat[i % 8]]
                vel = 1.0 if i % step == 0 else 0.75
                add(ins.note("spiccato", m, B / step * 0.8, vel), i * B / step, -0.3 + 0.15 * (i % 2), g)
                if tutti and (2 * i) % step == 0:
                    add(ins.note("celli", m - 12, B / 2 * 0.8, vel), i * B / step, 0.25, 0.8 * g)

        # basses on the composer's line; trombones and tuba hold the roots once the energy rises
        for n in p.bass:
            add(ins.note("basses", n.midi, n.length * B * 0.95, 0.8), n.beat * B, 0.0, lvl)
        if not quiet and (e >= 0.5 or tutti):
            for beat, chord, beats in halves:
                r, fifth = root(chord, 40), chord.tones[2] - chord.tones[0]
                dur = beats * B * 0.98
                add(ins.note("trombone", r, dur, 0.5 + 0.4 * e), beat * B, -0.2, lvl)
                add(ins.note("trombone", r + fifth, dur, 0.5 + 0.4 * e), beat * B + 0.01, 0.2, 0.9 * lvl)
                if big or tutti:
                    add(ins.note("tuba", root(chord, 33), dur, 0.5 + 0.4 * e), beat * B, 0.0, lvl)

        # the melody on the lead colour, doubled an octave up (or down, on horns) in the tutti
        if "lead" in p.layers and s.lead != "none":
            part = LEADS[s.lead]
            for n in p.melody:
                dur = n.length * B * 0.95
                add(ins.note(part, n.midi, dur, 0.85), n.beat * B, 0.1, lvl)
                if tutti:
                    dbl, octave = ("violins", 12) if part != "violins" else ("horn", -12)
                    add(ins.note(dbl, n.midi + octave, dur, 0.75), n.beat * B + 0.01, -0.15, 0.6 * lvl)
        if "counter" in p.layers:
            for n in p.counter:
                add(ins.note("violas", n.midi, n.length * B * 0.95, 0.6), n.beat * B + 0.02, -0.3, lvl)

        if s.drums != "none":
            self._percussion(piece, p, B, root(p.chord, 40), root(chord_at(2), 40))

    def _percussion(self, piece, p, B, tr, tr2):
        ins, add, s, e = piece.inst, piece.add, piece.style, p.energy
        big, tutti = p.section in ("B", "B'"), p.section in ("B'", "release")
        g = MIX * (0.4 + 0.6 * e)
        if "drums" in p.layers:
            add(ins.note("timpani", tr, B / 2, 0.9), 0.0, 0.15, g)
            if big:
                add(ins.note("timpani", tr2, B / 2, 0.7), 2 * B, 0.15, 0.8 * g)
            if p.section_bar == 0:
                add(ins.hit("cymbal"), 0.0, 0.3, g)
            if (big or tutti) and e >= 0.5:
                for b in piece.state["booms"]:
                    add(ins.hit("boom" if b == 0 else "taiko", 1.0 if b == int(b) else 0.8), b * B,
                        0.0 if b == 0 else -0.2, g)
            if s.drums == "busy" and big and p.bar % 2:
                for i, b in enumerate((3, 3.25, 3.5, 3.75)):
                    add(ins.hit("snare", 0.5 + 0.15 * i), b * B, 0.2, g)
        if p.section_bar == SECTION_BARS.get(p.section, 4) - 1:   # into the next section
            for i in range(8):
                add(ins.note("timpani", tr, B / 4, 0.45 + 0.55 * i / 7), (2 + i / 4) * B, 0.15, g)
            add(ins.swell(2 * B), 2 * B, -0.25, g)

    # ------------------------------------------------------------ moments
    @staticmethod
    def _root(piece, chord, lo):
        return (piece.ton + chord.tones[0] - lo) % 12 + lo

    def roll(self, piece, p):
        """Into the hit: a timpani roll on the dominant growing over the whole bar, a snare roll from beat 2 and a
        cymbal swell."""
        ins, add, B = piece.inst, piece.add, 60 / piece.style.bpm
        g, tr = MIX * (0.4 + 0.6 * p.energy), self._root(piece, p.chord, 40)
        for i in range(16):
            add(ins.note("timpani", tr, B / 4, 0.35 + 0.65 * i / 15), i * B / 4, 0.15, g)
        for i in range(12):
            add(ins.hit("snare", 0.35 + 0.65 * i / 11), (1 + i / 4) * B, 0.2, g)
        add(ins.swell(4 * B), 0.0, -0.25, 1.4 * g)

    def impact(self, piece, p):
        """The hit: big drum, taiko, cymbal and timpani on the root, over the default sub boom."""
        super().impact(piece, p)
        ins, add = piece.inst, piece.add
        g = 1.4 * MIX * p.energy
        add(ins.hit("boom", 1.0), 0.0, 0.0, g)
        add(ins.hit("taiko", 1.0), 0.0, -0.2, g)
        add(ins.hit("cymbal", 1.0), 0.0, 0.3, g)
        add(ins.note("timpani", self._root(piece, p.chord, 40), 0.5, 1.0), 0.0, 0.15, g)

    def stinger(self, piece, p):
        """The final chord: the whole orchestra holds the tonic for a bar and a half and rings out in the hall."""
        s, ins, add = piece.style, piece.inst, piece.add
        B = 60 / s.bpm; dur = 6 * B; lvl = MIX * (0.3 + 0.7 * p.energy)
        vc = piece.voice(list(p.chord.tones)); piece.center = float(np.mean(vc))
        for i, m in enumerate(vc):
            pan = (i - (len(vc) - 1) / 2) * 0.35
            add(ins.note("strings", m, dur, 0.9), i * 0.01, pan, lvl)
            add(ins.note("violins", m + 12, dur, 0.8), 0.01, -pan, 0.6 * lvl)
            add(ins.note("brass" if s.chords != "pad" else "choir", m, dur, 0.85), 0.02, -pan, 0.8 * lvl)
        add(ins.note(LEADS[s.lead] if s.lead != "none" else "horn", vc[-1], dur, 0.9), 0.0, 0.1, lvl)
        r = self._root(piece, p.chord, 40)
        add(ins.note("trombone", r, dur, 0.85), 0.0, -0.2, lvl)
        add(ins.note("trombone", r + 7, dur, 0.85), 0.01, 0.2, 0.9 * lvl)
        add(ins.note("tuba", self._root(piece, p.chord, 33), dur, 0.85), 0.0, 0.0, lvl)
        for n in p.bass:
            add(ins.note("basses", n.midi, dur, 0.85), 0.0, 0.0, lvl)

    def master(self, s):
        return Master(space=min(1.0, 0.45 + 0.55 * s.space), reverb=0.14 + 0.22 * s.space, room=0.5 + 1.4 * s.space,
                      vinyl=0.0, wow=0.0, tone=int(5000 * 2 ** (1.5 * s.brightness)), drive=1.7, threshold=-20.0,
                      ratio=2.5)


GENRE = OrchestralGenre()
