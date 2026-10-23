"""Cumbia: guacharaca, tambora and congas, a root-fifth bass, off-beat keyboard chords and an accordion lead.

The target sound (S6): the cumbia groove. The guacharaca scrapes steady eighths with the "ch-chik" accent (a long
scrape on the beat, a short accented one off it), the tambora marks beats 1 and 3 with a syncopated off-beat stroke
in the energetic sections, the congas slap the backbeat while open tones answer off the beat, and cowbell, shaker
and timbal fills join the high-energy sections. The bass alternates root and fifth on beats 1 and 3 with pickup
notes on the "and" of 2 and 4, the keyboard chords sit on the "and" of every beat, and an accordion (reed-like
detuned pulse/saw voices with a slow vibrato and a bellows envelope) plays the composer's melody. With a bright
style the lead turns into a digital-cumbia synth. Warm, glued master with a light room and no vinyl.

With the samples backend the voices are FluidR3_GM programs (33 Finger Bass, 21 Accordion, 23 Tango Accordion,
80/81 Square/Saw Lead...) and the Standard kit's Latin percussion (bank 128, preset 0: congas 62-64, maracas 70,
cabasa 69, guiro 73-74, cowbell 56, timbales 65-66). Only originally composed material.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from ..composer import Chord
from ..samples import Hit, Program
from ..synth import SR, adsr, bp, distort, hp, lp, mtof, osc
from .base import Genre, Master

DUR_STEP = 0.05
LOW_ENERGY = 0.4           # below it (and in the breakdown) the keys play only on the "and" of 2 and 4, no pickups
HIGH_ENERGY = 0.7          # at or above it: cowbell, shaker, tambora syncopation and extra conga mutes
DIGITAL_BRIGHTNESS = 0.7   # at or above it the accordion gives way to a digital-cumbia synth lead
REEDS = {"musette": (-9.0, 0.0, 8.0), "tango": (-4.0, 4.0), "dry": (0.0,)}   # cents of each accordion reed
REED_LEVEL = {"musette": 0.75, "tango": 0.6, "dry": 0.55}   # equal loudness across registrations
REGISTRATION = {"piano": "musette", "pad": "musette", "rhodes": "tango", "nylon": "dry"}
# (beat, stroke, low drum) per bar: slaps on the backbeat, open tones off the beat, muted tones elsewhere
CONGA_PATTERNS = (
    ((0, "mute", False), (1, "slap", False), (2, "mute", False), (2.5, "open", False), (3, "slap", False),
     (3.5, "open", True)),
    ((0, "mute", False), (0.5, "mute", False), (1, "slap", False), (1.5, "open", False), (2, "mute", False),
     (3, "slap", False), (3.5, "open", True)),
    ((0.5, "open", False), (1, "slap", False), (2, "mute", False), (2.5, "open", True), (3, "slap", False),
     (3.5, "open", True)),
)
TAMBORA_SYNCOPATIONS = ((1.5,), (3.5,), (1.5, 3.5))


def _bucket(dur: float) -> float:
    return max(1, round(dur / DUR_STEP)) * DUR_STEP


class Instruments:
    """Procedural cumbia voices. Tonal notes are cached per piece; the percussion is a few noise variants drawn once
    from the piece's generator."""

    def __init__(self, rng: np.random.Generator, sr: int = SR):
        self.rng, self.sr = rng, sr
        self._cache: dict[tuple, np.ndarray] = {}
        self._scrapes = {True: [self._make_scrape(True) for _ in range(3)],
                         False: [self._make_scrape(False) for _ in range(3)]}
        self._tambora = [self._make_tambora() for _ in range(2)]
        self._congas = {(kind, low): [self._make_conga(kind, low) for _ in range(2)]
                        for kind in ("open", "slap", "mute") for low in (False, True)}
        self._cowbell = self._make_cowbell()
        self._shakers = [self._make_shaker() for _ in range(3)]
        self._timbal = {False: self._make_timbal(False), True: self._make_timbal(True)}

    def _t(self, n):
        return np.arange(n) / self.sr

    def _cached(self, key, make):
        x = self._cache.get(key)
        if x is None:
            x = self._cache[key] = make()
        return x

    # ------------------------------------------------------------ tonal voices
    def accordion(self, m, dur, vel=1.0, color="pad"):
        """Detuned reed voices (pulse plus saw) with a slow vibrato and a bellows envelope; `color` picks the
        registration: musette (piano, pad), tango (rhodes) or a single dry reed (nylon)."""
        d = _bucket(dur)
        return self._cached(("acc", color, m, d), lambda: self._accordion(m, d, REGISTRATION[color])) * vel

    def _accordion(self, m, dur, reg, bright=1.0):
        n = int((dur + 0.1) * self.sr); t = self._t(n); f = mtof(m)
        depth = np.clip((t - 0.15) / 0.35, 0, 1) * 9                      # cents, eased in
        vib = 2 ** (depth * np.sin(2 * np.pi * 4.6 * t) / 1200)
        reeds = REEDS[reg]
        s = sum(osc("pulse", f * 2 ** (c / 1200) * vib, n, self.sr, phase=i / 3, duty=0.36)
                for i, c in enumerate(reeds)) * (0.55 / len(reeds)) + 0.45 * osc("saw", f * vib, n, self.sr, 0.2)
        if reg == "tango":                                                # the bandoneon-like low reed
            s = s + 0.35 * osc("saw", f / 2 * vib, n, self.sr, phase=0.3)
        cut = (2600 if reg == "tango" else 3800) * bright
        s = lp(s, cut, self.sr) + 0.5 * bp(s, 1100, 3200, self.sr)
        bellows = adsr(n, self.sr, 0.045, 0.15, 0.85, 0.08, hold=dur) * (0.9 + 0.1 * np.minimum(1, t / 0.6))
        return s * bellows * REED_LEVEL[reg]

    def synth(self, m, dur, vel=1.0, color="piano"):
        """The digital-cumbia lead: a saw (piano, nylon) or square (rhodes, pad) with a quick slide into the
        note and a delayed vibrato."""
        d = _bucket(dur)
        return self._cached(("synth", color, m, d), lambda: self._synth(m, d, color)) * vel

    def _synth(self, m, dur, color):
        n = int((dur + 0.1) * self.sr); t = self._t(n)
        bend = -40 * np.exp(-t / 0.025)                                   # cents: a short scoop up
        depth = np.clip((t - 0.25) / 0.3, 0, 1) * 18
        f = mtof(m) * 2 ** ((bend + depth * np.sin(2 * np.pi * 5.8 * t)) / 1200)
        shape = "square" if color in ("rhodes", "pad") else "saw"
        s = 0.7 * osc(shape, f, n, self.sr) + 0.3 * osc(shape, f * 1.004, n, self.sr, 0.4)
        s = s * (0.6 if shape == "square" else 1.0)                    # same loudness as the saw
        w = np.exp(-t * 6)
        s = lp(s, 2200, self.sr) * (1 - w) + lp(s, 6500, self.sr) * w
        return s * adsr(n, self.sr, 0.006, 0.2, 0.75, 0.08, hold=dur) * 0.42

    def keys(self, color, m, dur, vel=1.0):
        """One tone of an off-beat chord stab: electric piano (piano), percussive organ (rhodes), muted nylon
        chop (nylon) or a short accordion chord tone (pad)."""
        d = _bucket(dur)
        return self._cached(("keys", color, m, d), lambda: self._keys(color, m, d)) * vel

    def _keys(self, color, m, dur):
        f = mtof(m)
        if color == "pad":
            return self._accordion(m, dur, "musette", 0.6) * 0.8
        if color == "rhodes":
            return self._organ(m, dur, perc=True)
        if color == "nylon":
            n = int((dur + 0.08) * self.sr); t = self._t(n)
            s = osc("pulse", f, n, self.sr, duty=0.22) + 0.4 * osc("triangle", f * 2, n, self.sr)
            w = np.exp(-t * 30)
            s = lp(s, 1100, self.sr) * (1 - w) + lp(s, 4500, self.sr) * w
            return s * np.exp(-t * 14) * np.minimum(1, t / 0.002) * np.clip((dur + 0.08 - t) / 0.03, 0, 1) * 0.8
        n = int((dur + 0.15) * self.sr); t = self._t(n)                    # piano: bright electric piano stab
        s = (np.sin(2 * np.pi * f * t + 1.2 * np.exp(-t * 9) * np.sin(2 * np.pi * 2 * f * t))
             + 0.3 * osc("triangle", f * 2, n, self.sr) * np.exp(-t * 12))
        return s * adsr(n, self.sr, 0.003, 0.18, 0.45, 0.1, hold=dur) * 0.5

    def _organ(self, m, dur, perc=False):
        n = int((dur + 0.06) * self.sr); t = self._t(n); f = mtof(m)
        s = sum(a * np.sin(2 * np.pi * f * h * t) for h, a in ((0.5, 0.5), (1, 1.0), (2, 0.6), (3, 0.35), (4, 0.2)))
        if perc:                                                            # the drawbar organ's percussion click
            s = s + 0.6 * np.sin(2 * np.pi * f * 3 * t) * np.exp(-t * 22)
        return lp(s, 5000, self.sr) * adsr(n, self.sr, 0.004, 0.05, 0.9, 0.05, hold=dur) * 0.22

    def counter(self, m, dur, vel=1.0):
        """The counter-melody on a soft drawbar organ."""
        d = _bucket(dur)
        return self._cached(("counter", m, d), lambda: self._organ(m, d)) * vel

    def bass(self, m, dur, vel=1.0):
        """A round finger bass: sine plus a little triangle and saw, plucked decay, gentle saturation."""
        d, v = _bucket(dur), round(vel, 1)
        return self._cached(("bass", m, d, v), lambda: self._bass(m, d, v))

    def _bass(self, m, dur, vel):
        n = int((dur + 0.05) * self.sr); t = self._t(n); f = mtof(m)
        s = np.sin(2 * np.pi * f * t) + 0.3 * osc("triangle", f, n, self.sr) + 0.15 * lp(osc("saw", f, n, self.sr),
                                                                                       900 + 600 * vel, self.sr)
        env = adsr(n, self.sr, 0.004, 0.0, 1.0, 0.04, hold=dur) * (0.6 + 0.4 * np.exp(-t * 4))
        return distort(s * env * 1.3, 1.2) * vel * 0.65

    # ------------------------------------------------------------ percussion
    def _make_scrape(self, long_):
        """Band-passed noise chopped by the scraper's ridges: a long "ch" on the beat, a short "chik" off it."""
        dur = 0.16 if long_ else 0.05
        n = int(dur * self.sr); t = self._t(n)
        rate = (150 + 30 * self.rng.random()) * (1 + (0.25 if long_ else 0.0) * t / dur)
        ridges = (0.5 + 0.5 * np.sin(2 * np.pi * np.cumsum(rate) / self.sr)) ** 3
        noise = bp(self.rng.standard_normal(n), 2500, 9500, self.sr)
        if long_:
            env = np.minimum(1, t / 0.012) * np.clip((dur - t) / 0.05, 0, 1)
        else:
            env = np.minimum(1, t / 0.002) * np.exp(-t * 70)
            ridges = 0.5 + 0.5 * ridges
        return noise * ridges * env * 0.5

    def _make_tambora(self):
        n = int(0.45 * self.sr); t = self._t(n)
        f = 70 + 30 * np.exp(-t * 25)
        body = np.sin(2 * np.pi * np.cumsum(f) / self.sr) * np.exp(-t * 9)
        skin = lp(self.rng.standard_normal(n), 500, self.sr) * np.exp(-t * 30) * 0.4
        rim = bp(self.rng.standard_normal(n), 1500, 5000, self.sr) * np.exp(-t * 120) * 0.25
        return distort(body + skin + rim, 1.4) * 0.8

    def _make_conga(self, kind, low):
        n = int(0.4 * self.sr); t = self._t(n)
        f0 = (215 if low else 330) * (1 + self.rng.normal(0, 0.01))
        noise = self.rng.standard_normal(n)
        if kind == "open":
            f = f0 * (1 + 0.06 * np.exp(-t * 40))
            ph = 2 * np.pi * np.cumsum(f) / self.sr
            tone = (np.sin(ph) + 0.25 * np.sin(1.52 * ph)) * np.exp(-t * 13)
            return (tone + bp(noise, 800, 4000, self.sr) * np.exp(-t * 150) * 0.3) * 0.6
        if kind == "slap":
            tone = np.sin(2 * np.pi * f0 * 1.2 * t) * np.exp(-t * 35) * 0.5
            return (tone + bp(noise, 1200, 7000, self.sr) * np.exp(-t * 55) * 0.9) * 0.6
        tone = np.sin(2 * np.pi * f0 * 0.95 * t) * np.exp(-t * 50)            # muted / heel tone
        return (tone + bp(noise, 600, 2500, self.sr) * np.exp(-t * 120) * 0.15) * 0.35

    def _make_cowbell(self):
        n = int(0.3 * self.sr); t = self._t(n)
        s = osc("square", 562, n, self.sr) + 0.7 * osc("square", 845, n, self.sr, 0.3)
        env = np.minimum(1, t / 0.001) * (0.75 * np.exp(-t * 35) + 0.25 * np.exp(-t * 9))
        return bp(s, 450, 3500, self.sr) * env * 0.35

    def _make_shaker(self):
        n = int(0.08 * self.sr); t = self._t(n)
        env = np.minimum(1, t / 0.01) * np.exp(-np.maximum(0, t - 0.01) * 55)
        return hp(self.rng.standard_normal(n), 5500, self.sr) * env * 0.35

    def _make_timbal(self, low):
        n = int(0.5 * self.sr); t = self._t(n); f = 520 if low else 780
        shell = (np.sin(2 * np.pi * f * t) + 0.5 * np.sin(2 * np.pi * f * 1.48 * t)) * np.exp(-t * 12)
        rim = bp(self.rng.standard_normal(n), 2000, 8000, self.sr) * np.exp(-t * 60)
        return (shell * 0.6 + rim * 0.5) * 0.6

    def scrape(self, long_=True, g=1.0):
        pool = self._scrapes[bool(long_)]
        return pool[int(self.rng.integers(len(pool)))] * g

    def tambora(self, g=1.0):
        return self._tambora[int(self.rng.integers(len(self._tambora)))] * g

    def conga(self, kind="open", low=False, g=1.0):
        pool = self._congas[(kind, bool(low))]
        return pool[int(self.rng.integers(len(pool)))] * g

    def cowbell(self, g=1.0):
        return self._cowbell * g

    def shaker(self, g=1.0):
        return self._shakers[int(self.rng.integers(len(self._shakers)))] * g

    def timbal(self, low=False, g=1.0):
        return self._timbal[bool(low)] * g


def _rms(x: np.ndarray, sr: int) -> float:
    return float(np.sqrt(np.mean(np.square(x[:int(0.5 * sr)])))) + 1e-12


@lru_cache(maxsize=4)
def _targets(sr: int) -> dict[str, float]:
    """RMS of the procedural voices' reference notes (middle C, 1 s, velocity 0.8): the sampled programs and hits
    are scaled to it, so both backends sit in the same mix."""
    p = Instruments(np.random.default_rng(0), sr)
    out = {"bass": _rms(p.bass(48, 1.0, 0.8), sr), "counter": _rms(p.counter(60, 1.0, 0.8), sr),
           "ch": _rms(p._scrapes[True][0], sr), "chik": _rms(p._scrapes[False][0], sr),
           "tambora": _rms(p._tambora[0], sr), "cowbell": _rms(p._cowbell, sr), "shaker": _rms(p._shakers[0], sr),
           "timbal": _rms(p._timbal[False], sr), "timbal-low": _rms(p._timbal[True], sr)}
    for kind in ("open", "slap", "mute"):
        out["conga-" + kind] = _rms(p._congas[(kind, False)][0], sr)
    out["conga-low"] = _rms(p._congas[("open", True)][0], sr)
    for c in ("piano", "rhodes", "nylon", "pad"):
        out["acc-" + c] = _rms(p.accordion(60, 1.0, 0.8, c), sr)
        out["synth-" + c] = _rms(p.synth(60, 1.0, 0.8, c), sr)
        out["keys-" + c] = _rms(p.keys(c, 60, 1.0, 0.8), sr)
    return out


class SampledVoices:
    """The same voices on FluidR3_GM programs and the Standard kit's Latin percussion (bank 128, preset 0)."""

    KIT = 0
    ACCORDION = {"piano": 21, "pad": 21, "rhodes": 23, "nylon": 23}    # Accordion, Tango Accordion
    SYNTH = {"piano": 81, "nylon": 81, "rhodes": 80, "pad": 80}        # Saw Lead, Square Lead
    KEYS = {"piano": 4, "rhodes": 17, "nylon": 24, "pad": 21}          # E. Piano 1, Perc. Organ, Nylon, Accordion
    LONG_SCRAPE = 0.2                                                  # seconds: the long guiro is cut to an 8th

    def __init__(self, sampled, rng: np.random.Generator, sr: int = SR):
        self.s, self.rng, self.sr = sampled, rng, sr
        r = _targets(sr)
        self.acc_p = {c: Program(p, velocity=(60, 112), tail=0.25, rms=r["acc-" + c])
                      for c, p in self.ACCORDION.items()}
        self.synth_p = {c: Program(p, velocity=(60, 115), tail=0.2, rms=r["synth-" + c])
                        for c, p in self.SYNTH.items()}
        self.keys_p = {c: Program(p, velocity=(55, 110), tail=0.15, rms=r["keys-" + c]) for c, p in self.KEYS.items()}
        self.counter_p = Program(16, velocity=(50, 100), tail=0.15, rms=r["counter"])   # Drawbar Organ
        self.bass_p = Program(33, velocity=(70, 120), tail=0.08, rms=r["bass"])        # Finger Bass
        k = self.KIT
        self.drums = {"ch": Hit(k, 74, 95, 0.4, r["ch"]), "chik": Hit(k, 73, 105, 0.2, r["chik"]),
                      "tambora": Hit(k, 36, 110, 0.5, r["tambora"]),
                      "conga-open": Hit(k, 63, 105, 0.4, r["conga-open"]),
                      "conga-low": Hit(k, 64, 105, 0.45, r["conga-low"]),
                      "conga-slap": Hit(k, 62, 120, 0.3, r["conga-slap"]),
                      "conga-mute": Hit(k, 62, 55, 0.2, r["conga-mute"]),
                      "cowbell": Hit(k, 56, 100, 0.3, r["cowbell"]),
                      "maracas": Hit(k, 70, 95, 0.15, r["shaker"]), "cabasa": Hit(k, 69, 90, 0.15, r["shaker"]),
                      "timbal": Hit(k, 65, 110, 0.5, r["timbal"]), "timbal-low": Hit(k, 66, 110, 0.5, r["timbal-low"])}
        self._long = None

    def accordion(self, m, dur, vel=1.0, color="pad"):
        return self.s.play(self.acc_p[color], m, dur, vel)

    def synth(self, m, dur, vel=1.0, color="piano"):
        return self.s.play(self.synth_p[color], m, dur, vel)

    def keys(self, color, m, dur, vel=1.0):
        return self.s.play(self.keys_p[color], m, dur, vel)

    def counter(self, m, dur, vel=1.0):
        return self.s.play(self.counter_p, m, dur, vel)

    def bass(self, m, dur, vel=1.0):
        return self.s.play(self.bass_p, m, dur, vel)

    def scrape(self, long_=True, g=1.0):
        if not long_:
            return self.s.hit(self.drums["chik"], g)
        if self._long is None:                    # the long guiro rings past the next 8th: cut it with a fade
            x = np.array(self.s.hit(self.drums["ch"]), dtype=float)[:int(self.LONG_SCRAPE * self.sr)]
            k = min(len(x), int(0.03 * self.sr))
            x[len(x) - k:] *= np.linspace(1, 0, k)
            self._long = x
        return self._long * g

    def tambora(self, g=1.0):
        return self.s.hit(self.drums["tambora"], g)

    def conga(self, kind="open", low=False, g=1.0):
        return self.s.hit(self.drums["conga-low" if kind == "open" and low else "conga-" + kind], g)

    def cowbell(self, g=1.0):
        return self.s.hit(self.drums["cowbell"], g)

    def shaker(self, g=1.0):
        return self.s.hit(self.drums["maracas" if self.rng.random() < 0.6 else "cabasa"], g)

    def timbal(self, low=False, g=1.0):
        return self.s.hit(self.drums["timbal-low" if low else "timbal"], g)


class CumbiaGenre(Genre):
    name = "cumbia"
    description = "cumbia: guacharaca scrape, tambora and congas, root-fifth bass, off-beat keys, accordion lead"
    bpm = (85, 110)
    modes = ("minor", "major")
    voices = {"piano": "off-beat electric piano chords / musette accordion lead",
              "rhodes": "off-beat percussive organ / tango accordion lead",
              "nylon": "off-beat nylon guitar chops / dry-reed accordion lead",
              "pad": "off-beat accordion chords / musette accordion lead"}

    def instruments(self, rng, sr, sampled):
        return SampledVoices(sampled, rng, sr) if sampled is not None else Instruments(rng, sr)

    def start(self, piece):
        r = piece.rng
        piece.state["congas"] = CONGA_PATTERNS[int(r.integers(len(CONGA_PATTERNS)))]
        piece.state["tambora"] = TAMBORA_SYNCOPATIONS[int(r.integers(len(TAMBORA_SYNCOPATIONS)))]

    def chord(self, chord, scale):
        """Plain triads, the cumbia harmony; chromatic dominants keep their seventh."""
        return Chord(chord.degree, chord.tones[:3]) if chord.degree else chord

    def schedule(self, piece, p):
        s, ins, add, st, rng = piece.style, piece.inst, piece.add, piece.state, piece.rng
        B = 60 / s.bpm
        e, k = p.energy, p.key_offset
        low = e < LOW_ENERGY or p.section == "breakdown"
        high = e >= HIGH_ENERGY and p.section != "breakdown"
        # off-beat keyboard chords: the chord (and the passing chord from beat 3) voice-led around the last one
        segments = [(p.chord, 0.0)] + ([(p.passing, 2.0)] if p.passing is not None else [])
        voicings = []
        for chord, at in segments:
            vc = piece.voice([x + k for x in chord.tones]); piece.center = float(np.mean(vc))
            pc = (piece.ton + chord.tones[0] + k) % 12            # the root too: a full triad on each stab
            root = min((m for m in range(36, 96) if m % 12 == pc), key=lambda m: abs(m - piece.center))
            voicings.append((at, sorted(set(vc) | {root})))
        strum = 0.006 if s.chords == "nylon" else 0.002
        for b in ((1.5, 3.5) if low else (0.5, 1.5, 2.5, 3.5)):
            vc = voicings[-1][1] if b >= 2 else voicings[0][1]
            for i, m in enumerate(vc):
                add(ins.keys(s.chords, m, 0.3 * B, 0.8), b * B + i * strum, 0.25 + 0.15 * (i - 1),
                    0.07 + 0.02 * e)
        # bass: root on 1 and fifth (or the passing chord's root) on 3, pickups on the "and" of 2 and 4
        root = p.bass[0].midi
        second = next((n.midi for n in p.bass if n.beat == 2), None) if p.passing is not None else None
        if second is None:
            second = root + 7 if root + 7 <= 52 else root - 5
        if low:
            add(ins.bass(root, 1.85 * B, 0.7), 0, 0, 0.24)
            add(ins.bass(second, 1.85 * B, 0.65), 2 * B, 0, 0.22)
        else:
            approach = next((n.midi for n in p.bass if n.beat == 3.5), second)
            for beat, m, ln, v in ((0, root, 1.3, 0.9), (1.5, root, 0.4, 0.6), (2, second, 1.3, 0.85),
                                   (3.5, approach, 0.4, 0.6)):
                add(ins.bass(m, ln * B, v), beat * B, 0, 0.25)
        # percussion
        if "drums" in p.layers:
            dg = (0.75 + 0.5 * e) * (0.7 if s.drums == "soft" else 1.0)
            hum = lambda: float(rng.uniform(0.92, 1.08))
            for j in range(8):                                   # guacharaca: "ch" on the beat, "chik" off it
                long_ = j % 2 == 0
                add(ins.scrape(long_), j * 0.5 * B, 0.35, (0.24 if long_ else 0.34) * dg * hum())
            for b in (0, 2) + (st["tambora"] if high else ()):
                add(ins.tambora(), b * B, 0, (0.34 if b in (0, 2) else 0.24) * dg)
            pattern = st["congas"] + (((0.75, "mute", False), (2.75, "mute", False)) if high else ())
            for b, kind, lo in pattern:
                add(ins.conga(kind, lo), b * B, -0.3 if lo else -0.15,
                    {"open": 0.26, "slap": 0.26, "mute": 0.18}[kind] * dg * hum())
            if high:
                for b in range(4):
                    add(ins.cowbell(), b * B, -0.35, (0.07 if b % 2 else 0.09) * dg)
                for j in range(16):
                    add(ins.shaker(), j * 0.25 * B, 0.45, (0.06 if j % 2 == 0 else 0.04) * dg * hum())
            if (high or s.drums == "busy") and p.section_bar % 8 == 7:   # timbal fill into the next section
                for b, lo in ((3, False), (3.25, False), (3.5, True), (3.75, True)):
                    add(ins.timbal(lo), b * B, 0.2 if lo else -0.1, 0.12 * dg)
        # lead: the accordion (or the digital-cumbia synth) on the melody; counter-melody on the organ
        if "lead" in p.layers and s.lead != "none":
            digital = s.brightness >= DIGITAL_BRIGHTNESS
            for n in p.melody:
                if digital:
                    add(ins.synth(n.midi, n.length * B * 0.9, 0.9, s.lead), n.beat * B, 0.1, 0.2)
                else:
                    add(ins.accordion(n.midi, n.length * B * 0.95, 0.9, s.lead), n.beat * B, 0.1, 0.24)
        if "counter" in p.layers:
            for n in p.counter:
                add(ins.counter(n.midi, n.length * B * 0.9, 0.6), n.beat * B + 0.01, -0.3, 0.1)

    def master(self, s):
        return Master(space=0.15 + 0.3 * s.space, reverb=0.04 + 0.1 * s.space, room=0.25 + 0.55 * s.space,
                      vinyl=0.0, wow=0.05, tone=int(min(16000, 4500 * 2.6 ** s.brightness)), drive=1.7,
                      threshold=-18.0, ratio=3.0)


GENRE = CumbiaGenre()
