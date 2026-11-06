"""Bossa nova: the nylon-guitar batida, extended harmony, a root-fifth upright bass, a cross-stick clave and a soft
flute, vibraphone or sax melody, felt in two.

The guitarist's thumb plays the root on beat 1 and the fifth (or the passing chord's root) on beat 3, while the
fingers pluck the upper chord voices in a syncopated two-bar cell on the eighth-note grid (the batida); the chord
colour picks who plucks (nylon guitar, piano or rhodes; "pad" keeps the guitar and adds a soft string bed). Before
the next bar's root the plucks sometimes plane the whole voicing onto the bass's approach note: a chromatic approach
chord. The `chord` hook extends every diatonic chord (maj9, m9, dominant 9 and 13, m7b5 with the eleventh, add9 on
plain triads). The upright bass plays root and fifth in the dotted-quarter-and-eighth rhythm with pickups on the
"and" of 2 and 4; the drums are a cross-stick on the rim playing the bossa clave across two bars, a shaker in steady
eighths, a feathered kick under the bass and a brush swirl. The master is a small, close room, warm and clean.

With the samples backend: GM 24 nylon guitar, 0 piano, 4 rhodes, 48 strings, 73 flute, 11 vibraphone, 66 tenor sax,
32 acoustic bass, the Standard kit's side stick (37) and maracas (70) and the Jazz kit's kick (the brush swirl stays
procedural). Only originally composed material.
"""
from __future__ import annotations

from collections import OrderedDict
from functools import lru_cache

import numpy as np

from ..samples import Hit, Program, reference_rms
from ..synth import SR, adsr, bp, hp, lp, mtof
from . import jazz
from .base import Genre, Master
from .lofi import Instruments

LOW_ENERGY = 0.35   # below it (or without drums) the guitar plays the chord in two, the bass has no pickups
# two-bar batida cells: the beats the fingers pluck in each bar (the thumb always plays beats 1 and 3)
BATIDA = (((0, 1, 2.5, 3.5), (0.5, 2, 3)),
          ((0, 1.5, 2.5), (0.5, 1.5, 3, 3.5)),
          ((0, 1, 2.5), (0.5, 1.5, 2.5, 3.5)))
CLAVE = ((0, 1.5, 3), (1, 2.5))   # cross-stick, two bars (3-2 bossa clave)
SLIDE_CHANCE = 0.45               # how often the last pluck planes onto the bass's approach note
LEADS = {"pad": "flute", "rhodes": "vibes", "piano": "sax", "nylon": "guitar"}
LEAD_GAIN = {"flute": 0.24, "vibes": 0.26, "sax": 0.2, "guitar": 0.3}
THUMB_RANGE, BASS_RANGE = (40, 52), (28, 52)


def _place(pc: int, near: float, lo: int, hi: int) -> int:
    return min((m for m in range(lo, hi + 1) if m % 12 == pc % 12), key=lambda m: (abs(m - near), m))


# ---------------------------------------------------------------- voices
class Voices:
    """Procedural bossa voices: a plucked nylon guitar, a breathy flute, the jazz vibraphone, tenor and upright
    bass, the lo-fi keys and pad, and a cross-stick, shaker, kick and brush swirl."""

    def __init__(self, rng: np.random.Generator, sr: int = SR):
        self.rng, self.sr = rng, sr
        self.lofi, self.jazz = Instruments(rng, sr), jazz.Voices(rng, sr)
        self._notes: OrderedDict[tuple, np.ndarray] = OrderedDict()
        self._rims = [self._make_rim() for _ in range(2)]
        self._shakers = [self._make_shaker() for _ in range(3)]

    def _t(self, n):
        return np.arange(n) / self.sr

    def _cached(self, key: tuple, make) -> np.ndarray:
        """Pitched notes bucketed like the samples backend (0.05 s, eighths of velocity) and reused."""
        x = self._notes.get(key)
        if x is None:
            x = make(); x.flags.writeable = False
            self._notes[key] = x
            if len(self._notes) > 512:
                self._notes.popitem(last=False)
        else:
            self._notes.move_to_end(key)
        return x

    def _note(self, name, m, dur, vel, make):
        d, v = max(1, round(dur / 0.05)) * 0.05, max(1, round(vel * 8)) / 8
        return self._cached((name, int(m), d, v), lambda: make(int(m), d, v))

    def _make_rim(self):
        n = int(0.12 * self.sr); t = self._t(n)
        click = hp(self.rng.standard_normal(n), 2500, self.sr) * np.exp(-t * 320)
        body = (np.sin(2 * np.pi * 510 * (1 + 0.02 * self.rng.standard_normal()) * t) * np.exp(-t * 48)
                + 0.5 * np.sin(2 * np.pi * 1240 * t) * np.exp(-t * 70))
        return (0.7 * click + body) * np.minimum(1, t / 0.0008)

    def _make_shaker(self):
        n = int(0.1 * self.sr); t = self._t(n)
        x = bp(self.rng.standard_normal(n), 3500, 10000, self.sr)
        return x * np.minimum(1, t / 0.012) * np.exp(-t * 38)

    # tonal
    def guitar(self, m, dur, vel=1.0):
        """A nylon string plucked by the fingers: the lo-fi Karplus-Strong string, damped at the note's end."""
        def make(m, d, v):
            x = self.lofi.nylon(m, d, v)
            t = self._t(len(x))
            return x * np.clip((d + 0.12 - t) / 0.12, 0, 1)
        return self._note("guitar", m, dur, vel, make)

    def thumb(self, m, dur, vel=1.0):
        return self._note("thumb", m, dur, vel, lambda m, d, v: lp(self.guitar(m, d, v), 1600, self.sr))

    def flute(self, m, dur, vel=1.0):
        def make(m, d, v):
            n = int((d + 0.15) * self.sr); t = self._t(n)
            vib = 1 + 0.005 * np.sin(2 * np.pi * 5.0 * t) * np.clip((t - 0.25) / 0.3, 0, 1)
            ph = 2 * np.pi * np.cumsum(mtof(m) * vib) / self.sr
            s = np.sin(ph) + 0.22 * np.sin(2 * ph) + 0.07 * np.sin(3 * ph)
            s += bp(self.rng.standard_normal(n), 1800, 7000, self.sr) * (0.05 + 0.25 * np.exp(-t * 30))  # breath
            return s * adsr(n, self.sr, 0.06, 0.12, 0.85, 0.12, hold=d) * v * 0.55
        return self._note("flute", m, dur, vel, make)

    def keys(self, colour, m, dur, vel=1.0):
        if colour in ("piano", "rhodes"):
            return self._note(colour, m, dur, vel, getattr(self.lofi, colour))
        return self.guitar(m, dur, vel)

    def lead(self, voice, m, dur, vel=1.0):
        if voice == "vibes":
            return self._note("vibes", m, dur, vel, self.jazz.vibes)
        if voice == "sax":   # a soft, subtoned tenor
            return self._note("sax", m, dur, vel, lambda m, d, v: lp(self.jazz.sax(m, d, v), 1800, self.sr))
        return getattr(self, voice)(m, dur, vel)

    def counter(self, voice, m, dur, vel=1.0):
        return self.lead(voice, m, dur, vel)

    def pad(self, m, dur, vel=1.0):
        return self._note("pad", m, dur, vel, self.lofi.pad)

    def bass(self, m, dur, vel=1.0):
        return self.jazz.bass(m, dur, vel)

    # percussion
    def rim(self, vel=1.0):
        return self._rims[int(self.rng.integers(2))] * vel

    def shaker(self, vel=1.0):
        return self._shakers[int(self.rng.integers(3))] * vel

    def kick(self, vel=1.0):
        return self.jazz.kick(vel)

    def swirl(self, dur):
        return self.jazz.swirl(dur)


@lru_cache(maxsize=4)
def _levels(sr: int) -> dict[str, float]:
    """RMS of the procedural voices' reference notes and hits, so the sampled ones sit at the same level."""
    v = Voices(np.random.default_rng(0), sr)
    rms = lambda x: float(np.sqrt(np.mean(np.square(x[:int(0.5 * sr)]))))
    return {"guitar": rms(v.guitar(60, 1.0, 0.8)), "flute": rms(v.flute(60, 1.0, 0.8)),
            "vibes": rms(v.lead("vibes", 60, 1.0, 0.8)), "sax": rms(v.lead("sax", 60, 1.0, 0.8)),
            "bass": rms(v.bass(60, 1.0, 0.8)), "rim": rms(v.rim()), "shaker": rms(v.shaker()),
            "kick": rms(v.kick())} | reference_rms(sr)


class SampledVoices:
    """The same voices played by FluidR3_GM programs (or the recorded library where it covers them)."""

    def __init__(self, sampled, proc: Voices, sr: int = SR):
        self.s, self.proc = sampled, proc
        lv = _levels(sr)
        self.programs = {"guitar": Program(24, velocity=(40, 100), tail=0.8, rms=lv["guitar"]),
                         "piano": Program(0, velocity=(35, 95), tail=1.0, rms=lv["piano"]),
                         "rhodes": Program(4, velocity=(40, 100), tail=0.8, rms=lv["rhodes"]),
                         "pad": Program(48, velocity=(30, 80), tail=1.0, rms=lv["pad"]),
                         "flute": Program(73, velocity=(45, 100), tail=0.3, rms=lv["flute"]),
                         "vibes": Program(11, velocity=(40, 100), tail=1.2, rms=lv["vibes"]),
                         "sax": Program(66, velocity=(40, 95), tail=0.2, rms=lv["sax"]),
                         "bass": Program(32, velocity=(55, 110), tail=0.15, rms=lv["bass"])}
        self.hits = {"rim": Hit(0, 37, 80, 0.3, lv["rim"]), "shaker": Hit(0, 70, 80, 0.2, lv["shaker"]),
                     "kick": Hit(32, 36, 80, 0.4, lv["kick"])}

    def _play(self, name, m, dur, vel):
        return self.s.play(self.programs[name], m, dur, vel)

    def keys(self, colour, m, dur, vel=1.0):
        return self._play(colour if colour in ("piano", "rhodes") else "guitar", m, dur, vel)

    def thumb(self, m, dur, vel=1.0):
        return self._play("guitar", m, dur, vel)

    def lead(self, voice, m, dur, vel=1.0):
        return self._play(voice, m, dur, vel)

    def counter(self, voice, m, dur, vel=1.0):
        return self._play(voice, m, dur, vel)

    def pad(self, m, dur, vel=1.0):
        return self._play("pad", m, dur, vel)

    def bass(self, m, dur, vel=1.0):
        return self._play("bass", m, dur, vel)

    def rim(self, vel=1.0):
        return self.s.hit(self.hits["rim"], vel)

    def shaker(self, vel=1.0):
        return self.s.hit(self.hits["shaker"], vel)

    def kick(self, vel=1.0):
        return self.s.hit(self.hits["kick"], vel)

    def swirl(self, dur):
        return self.proc.swirl(dur)


# ---------------------------------------------------------------- genre
class BossaNovaGenre(Genre):
    name = "bossanova"
    description = ("bossa nova: nylon guitar batida felt in two, maj7 and ninth chords, upright bass, cross-stick "
                   "clave and shaker, soft flute or vibraphone melody")
    bpm = (120, 140)
    modes = ("major", "dorian", "minor", "lydian", "mixolydian")
    voices = {"piano": "piano comping the batida (soft tenor sax when it plays the lead)",
              "rhodes": "rhodes comping the batida (vibraphone when it plays the lead)",
              "nylon": "nylon guitar batida (the guitar melody when it plays the lead)",
              "pad": "nylon guitar over soft strings (flute when it plays the lead)"}

    def instruments(self, rng, sr, sampled):
        proc = Voices(rng, sr)
        return proc if sampled is None else SampledVoices(sampled, proc, sr)

    def start(self, piece):
        piece.state["cell"] = BATIDA[int(piece.rng.integers(len(BATIDA)))]
        piece.state["clave"] = int(piece.rng.integers(2))   # which bar of the clave the piece starts on
        piece.center = piece.ton + 14

    def chord(self, chord, scale):
        tones = list(chord.tones[:4])
        if len(tones) < 4:
            return chord
        root = tones[0]
        iv = [t - root for t in tones]
        third, fifth, top = iv[1], iv[2], iv[3]
        if top == 12:                                   # a plain triad (moments' final chords): add the ninth
            return chord if third not in (3, 4) or fifth != 7 else type(chord)(chord.degree, (*tones, root + 14))
        if top not in (10, 11):                         # diminished sevenths stay as they are
            return chord
        d = chord.degree - 1

        def deg(k):
            return scale[(d + k) % 7] + 12 * ((d + k) // 7)

        if fifth == 6:                                  # m7b5: the eleventh
            ext = [17]
        elif third == 4 and top == 10 and chord.degree:  # dominant: the scale's ninth and thirteenth when idiomatic
            ninth, thirteenth = deg(8) - root, deg(12) - root
            ext = [ninth if ninth in (13, 14) else 14, thirteenth if thirteenth in (20, 21) else 21]
        elif third == 4 and top == 10:                  # the composer's chromatic dominants
            ext = [14, 21]
        else:                                           # maj9, m9
            ext = [14]
        return type(chord)(chord.degree, (*tones, *(root + x for x in ext)))

    # ------------------------------------------------------------ schedule
    def schedule(self, piece, p):
        B = 60 / piece.style.bpm
        quiet = "drums" not in p.layers or p.energy < LOW_ENERGY
        self._batida(piece, p, B, quiet)
        self._bass(piece, p, B, quiet)
        if "drums" in p.layers:
            self._drums(piece, p, B)
        self._melodies(piece, p, B)

    def _voicing(self, piece, chord, k):
        """Three upper voices for the fingers: the third, the seventh and a colour tone (ninth or thirteenth, else
        the fifth), voice-led in the guitar's middle register."""
        iv = [t - chord.tones[0] for t in chord.tones]
        picked = [iv[1]] + ([iv[3]] if len(iv) > 3 and iv[3] in (9, 10, 11) else [])
        for c in (14, 13, 21, 20, 17, iv[2]):
            if len(picked) >= 3:
                break
            if c in iv and c not in picked:
                picked.append(c)
        root = chord.tones[0] + k
        vc = piece.voice([root] + [root + x for x in picked])
        piece.center = 0.75 * float(np.mean(vc)) + 0.25 * (piece.ton + 14 + k)
        return vc

    def _roots(self, piece, p):
        """Pitch classes of beat 1 and beat 3: the root and its fifth, the passing chord's root, or a held pedal."""
        k, ton = p.key_offset, piece.ton
        if len(p.bass) == 1 and p.bass[0].length >= 4:          # a moment's pedal or final root
            return p.bass[0].midi % 12, p.bass[0].midi % 12
        root = (ton + p.chord.tones[0] + k) % 12
        second = (ton + p.passing.tones[0] + k) % 12 if p.passing is not None else (root + 7) % 12
        return root, second

    def _batida(self, piece, p, B, quiet):
        s, ins, rng, add = piece.style, piece.inst, piece.rng, piece.add
        k = p.key_offset
        colour = s.chords if s.chords in ("piano", "rhodes") else "nylon"
        first, second = self._roots(piece, p)
        t1 = _place(first, 45, *THUMB_RANGE)
        for beat, pc in ((0, first), (2, second)):              # the thumb, felt in two
            add(ins.thumb(_place(pc, t1, *THUMB_RANGE), 1.8 * B, 0.8), beat * B, -0.1, 0.16)
        vc = self._voicing(piece, p.chord, k)
        vp = self._voicing(piece, p.passing, k) if p.passing is not None else vc
        cell = piece.state["cell"][p.bar % 2]
        plucks = [0.0, 2.0] if quiet else list(cell)
        approach = [n for n in p.bass if n.beat >= 3]
        slide = None
        if not quiet and approach and rng.random() < SLIDE_CHANCE:
            shift = (approach[-1].midi - (piece.ton + (p.passing or p.chord).tones[0] + k)) % 12
            shift = shift - 12 if shift > 6 else shift
            if shift:
                slide = shift
                if 3.5 not in plucks:
                    plucks.append(3.5)
        plucks.sort()
        g = 0.1 * (0.85 + 0.3 * p.energy)
        for i, beat in enumerate(plucks):
            nxt = plucks[i + 1] if i + 1 < len(plucks) else 4.0
            ln = min(nxt - beat, 2.0) * B * 0.9
            voicing = vp if beat >= 2 else vc
            if slide is not None and beat >= 3.5:
                voicing = [m + slide for m in voicing]
            vel = (0.8 if beat in (0, 2) or beat % 1 else 0.7) + 0.1 * float(rng.random())
            t = beat * B + 0.004 * float(rng.random())
            for j, m in enumerate(voicing):                    # p-i-m-a: nearly together, low to high
                add(ins.keys(colour, m, ln, vel), t + j * 0.004, (j - 1) * 0.15, g)
        if s.chords == "pad":                                  # a soft string bed under the guitar
            halves = [(0, vc, 2 if p.passing is not None else 4)] + ([(2, vp, 2)] if p.passing is not None else [])
            for beat, voicing, ln in halves:
                for j, m in enumerate(voicing):
                    add(ins.pad(m, ln * B * 0.98, 0.6), beat * B, (j - 1) * 0.3, 0.045)

    def _bass(self, piece, p, B, quiet):
        ins, add = piece.inst, piece.add
        first, second = self._roots(piece, p)
        root = _place(first, 38, *BASS_RANGE)
        fifth = _place(second, root + 5, *BASS_RANGE)
        if quiet or (len(p.bass) == 1 and p.bass[0].length >= 4):   # in two
            for beat, m in ((0, root), (2, fifth)):
                add(ins.bass(m, 2 * B * 0.92, 0.75), beat * B, 0, 0.40)
            return
        approach = [n for n in p.bass if n.beat >= 3]
        last = _place(approach[-1].midi % 12, fifth, *BASS_RANGE) if approach else root
        # the bossa bass: dotted quarter and eighth, twice; the eighths anticipate beat 3 and the next bar
        for beat, m, ln, vel in ((0, root, 1.5, 0.85), (1.5, fifth, 0.5, 0.7), (2, fifth, 1.5, 0.8),
                                 (3.5, last, 0.5, 0.7)):
            add(ins.bass(m, ln * B * 0.92, vel), beat * B, 0, 0.42)

    def _drums(self, piece, p, B):
        s, ins, rng, add = piece.style, piece.inst, piece.rng, piece.add
        e = p.energy
        dg = 0.7 + 0.5 * e
        for beat in CLAVE[(p.bar + piece.state["clave"]) % 2]:
            add(ins.rim(0.85 + 0.15 * float(rng.random())), beat * B, 0.2, 0.12 * dg)
        steps = 4 if s.drums == "busy" and e >= 0.6 else 2
        for i in range(4 * steps):
            beat = i / steps
            accent = 1.0 if beat % 1 == 0 else 0.6 if beat % 0.5 == 0 else 0.45
            add(ins.shaker(accent), beat * B, -0.35, 0.05 * dg * accent * (0.9 + 0.2 * float(rng.random())))
        for beat in ((0, 1.5, 2, 3.5) if e >= 0.6 else (0, 2)):
            add(ins.kick(0.7 if beat % 1 == 0 else 0.5), beat * B, 0, 0.12 * dg)
        for h in (0, 2):
            add(ins.swirl(2 * B), h * B, 0.1, 0.035 * dg)

    def _melodies(self, piece, p, B):
        s, ins, rng, add = piece.style, piece.inst, piece.rng, piece.add
        voice = LEADS.get(s.lead, "flute")
        if "lead" in p.layers:
            for n in p.melody:
                add(ins.lead(voice, n.midi, n.length * B * 0.95, 0.8), n.beat * B + 0.006 * float(rng.random()),
                    0.15, LEAD_GAIN[voice])
        if "counter" in p.layers:
            other = "vibes" if voice != "vibes" else "flute"
            for n in p.counter:
                add(ins.counter(other, n.midi, n.length * B * 0.95, 0.55), n.beat * B + 0.02, -0.3, 0.08)

    # ------------------------------------------------------------ moments
    def stinger(self, piece, p):
        """The final chord: the thumb and a slow upward roll of the extended tonic on the guitar, the bass and the
        lead's top note, held for a bar and a half."""
        s, ins, add = piece.style, piece.inst, piece.add
        B = 60 / s.bpm; dur = 6 * B; lvl = 0.6 + 0.4 * p.energy
        first, _ = self._roots(piece, p)
        add(ins.thumb(_place(first, 45, *THUMB_RANGE), dur, 0.85), 0.0, -0.1, 0.18 * lvl)
        add(ins.bass(_place(first, 38, *BASS_RANGE), dur, 0.8), 0.0, 0.0, 0.42 * lvl)
        vc = self._voicing(piece, p.chord, p.key_offset)
        colour = s.chords if s.chords in ("piano", "rhodes") else "nylon"
        for j, m in enumerate(vc):
            add(ins.keys(colour, m, dur, 0.8), 0.04 * (j + 1), (j - 1) * 0.2, 0.12 * lvl)
            if s.chords == "pad":
                add(ins.pad(m, dur, 0.6), 0.0, (j - 1) * 0.3, 0.05 * lvl)
        if s.lead != "none" and vc:
            voice = LEADS.get(s.lead, "flute")
            add(ins.lead(voice, vc[-1] + 12, dur, 0.7), 0.2, 0.15, LEAD_GAIN[voice] * lvl)

    def master(self, s):
        return Master(space=0.15 + 0.5 * s.space, reverb=0.06 + 0.12 * s.space, room=0.3 + 0.8 * s.space,
                      vinyl=0.08 * s.vinyl, wow=0.08, tone=int(min(16000, 3600 * 4 ** s.brightness)), drive=1.35,
                      threshold=-18.0, ratio=2.0)   # a close, warm room; light compression keeps the dynamics


GENRE = BossaNovaGenre()
