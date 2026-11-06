"""Boom bap: 90s hip-hop. A hard, dusty kick and a cracking snare on 2 and 4 over swung sixteenths, chopped soul and
jazz chords, an upright or finger bass and a sparse hook, with vinyl and tape colour on the master.

Punchier and more swung than lo-fi, with chopped stabs instead of lush pads. The `chord` hook adds a major ninth to
minor and major seventh chords (the soul colour). The chord instrument plays short stabs of the voicing on a
two-bar chop pattern chosen per piece (the passing chord takes the stabs from beat 3); the breakdown and the
low-energy bars hold one longer chop. The bass plays the composer's bass line, short and on the swing grid. The
drums: a kick pattern per piece (always on the one), the snare on 2 and 4 (a rim with soft drums), eighth-note hats
with swung sixteenths in busy or energetic bars, an open hat on the "and" of 4, ghost snares and a fill at the end
of each eight-bar phrase. The hook plays the composer's melody only in the first two bars of every four (every bar
at the top of the climax), on the swing grid. Moments use the default roll, impact and stinger.

With the samples backend: GM 0 piano, 4 rhodes, 11 vibraphone, 26 jazz guitar, 32 acoustic (upright) bass and 33
finger bass, the Room kit (8) kick and the Standard kit (0) snare, side stick and hats; the recorded library
replaces any of them it covers. Only originally composed material.
"""
from __future__ import annotations

from collections import OrderedDict
from functools import lru_cache

import numpy as np

from ..composer import FIRST_FORM, Chord
from ..samples import Hit, Program
from ..synth import SR, adsr, bp, distort, hp, lp, mtof, osc
from .base import Genre, Master
from .lofi import Instruments

SECTION_BARS = dict(FIRST_FORM)
# kick patterns on the sixteenth grid of a bar (0-15); odd steps are swung
KICKS = ((0, 10), (0, 7, 10), (0, 3, 10), (0, 9, 10), (0, 8, 11), (0, 6, 10, 15))
# chop patterns: (sixteenth step, length in sixteenths) of each stab; two per piece make a two-bar loop
CHOPS = (((0, 3), (6, 2), (10, 4)),
         ((0, 2), (3, 2), (8, 3), (14, 2)),
         ((0, 4), (7, 2), (12, 2)),
         ((2, 2), (6, 3), (11, 2), (14, 2)),
         ((0, 3), (5, 2), (10, 2), (13, 2)))
GHOSTS = (7, 9, 15)       # sixteenth steps that may take a ghost snare
PUNCH = 0.6               # sampled kick and snare level against the procedural RMS (see `SampledVoices`)
BASS_COLOUR = {"piano": "upright", "pad": "upright", "rhodes": "finger", "nylon": "finger"}


def swing_pct(swing: float) -> float:
    """Where a swung sixteenth falls inside its eighth (0.5 = straight): MPC-style 54-66 %."""
    return 0.54 + 0.5 * float(np.clip(swing, 0.0, 0.25))


def swung(x: float, pct: float) -> float:
    """Beat position `x` with its off-sixteenths (the "e" and the "a") moved late to `pct` of their eighth."""
    q = x * 4
    k = round(q)
    if abs(q - k) < 1e-6 and k % 2 == 1:
        return (k - 1) / 4 + pct * 0.5
    return x


def hook_bar(plan) -> bool:
    """Whether the hook plays in this bar: the first two bars of every four, or every bar at the climax's top."""
    return plan.section_bar % 4 < 2 or plan.energy >= 0.95


def _bucket(dur: float, vel: float) -> tuple[float, float]:
    return max(1, round(dur / 0.05)) * 0.05, max(1, round(vel * 8)) / 8


# ---------------------------------------------------------------- voices
class Voices:
    """Procedural boom bap voices: the lo-fi keys and nylon (a darker guitar), a vibraphone, upright and finger
    basses, and a hard, saturated kit. Pitched notes are bucketed and cached per piece."""

    def __init__(self, rng: np.random.Generator, sr: int = SR):
        self.rng, self.sr = rng, sr
        self.lofi = Instruments(rng, sr)
        self._notes: OrderedDict[tuple, np.ndarray] = OrderedDict()
        self._kicks = [self._make_kick() for _ in range(2)]
        self._snares = [self._make_snare() for _ in range(3)]
        self._hats = [self._make_hat(0.07) for _ in range(3)]
        self._open = self._make_hat(0.4)
        t = self._t(int(0.08 * sr))
        self._rim = bp(rng.standard_normal(len(t)), 1500, 4000, sr) * np.exp(-t * 80) * 1.4

    def _t(self, n):
        return np.arange(n) / self.sr

    def _cached(self, key: tuple, make) -> np.ndarray:
        x = self._notes.get(key)
        if x is None:
            x = make(); x.flags.writeable = False
            self._notes[key] = x
            if len(self._notes) > 512:
                self._notes.popitem(last=False)
        else:
            self._notes.move_to_end(key)
        return x

    # tonal
    def _vibes(self, m, dur, vel):
        f = mtof(m); n = int((dur + 0.6) * self.sr); t = self._t(n)
        s = (np.sin(2 * np.pi * f * t) + 0.25 * np.sin(2 * np.pi * 3.98 * f * t) * np.exp(-t * 6)
             + lp(self.rng.standard_normal(n), 3000, self.sr) * np.exp(-t * 250) * 0.15)
        env = np.minimum(1, t / 0.002) * np.exp(-t * 1.4) * np.clip((dur + 0.6 - t) / 0.6, 0, 1)
        return s * env * (1 + 0.2 * np.sin(2 * np.pi * 5.5 * t)) * vel * 0.8

    def _tone(self, colour, m, dur, vel):
        if colour == "pad":
            return self._vibes(m, dur, vel)
        if colour == "nylon":   # a muted jazz guitar
            return lp(self.lofi.nylon(m, dur, vel), 2200, self.sr)
        x = getattr(self.lofi, colour)(m, dur, vel)
        k = min(len(x), int(0.02 * self.sr))   # the chop: a fast cut after the note
        x = x[:int((dur + 0.08) * self.sr)].copy()
        x[len(x) - k:] *= np.linspace(1, 0, k)
        return x

    def keys(self, colour, m, dur, vel=1.0):
        d, v = _bucket(dur, vel)
        return self._cached(("keys", colour, int(m), d, v), lambda: self._tone(colour, int(m), d, v))

    def lead(self, colour, m, dur, vel=1.0):
        return self.keys(colour, m, dur, vel)

    def bass(self, m, dur, vel=1.0, colour="piano"):
        d, v = _bucket(dur, vel)
        kind = BASS_COLOUR[colour]
        return self._cached(("bass", kind, int(m), d, v), lambda: self._bass(kind, int(m), d, v))

    def _bass(self, kind, m, dur, vel):
        f = mtof(m); n = int((dur + 0.08) * self.sr); t = self._t(n)
        if kind == "upright":
            s = sum(np.sin(2 * np.pi * k * f * t) * k ** -1.2 * np.exp(-t * (1.5 + 1.2 * k)) for k in range(1, 7))
            s = s + lp(self.rng.standard_normal(n), 400, self.sr) * np.exp(-t * 45) * 0.4
            s = lp(s, 900 + 900 * vel, self.sr)
        else:
            s = np.sin(2 * np.pi * f * t) + 0.3 * osc("triangle", f, n, self.sr) \
                + 0.12 * lp(osc("saw", f, n, self.sr), 800 + 500 * vel, self.sr)
            s = distort(s * 1.2, 1.3) * (0.65 + 0.35 * np.exp(-t * 5))
        return s * adsr(n, self.sr, 0.004, 0.0, 1.0, 0.06, hold=dur) * vel

    # drums
    def _make_kick(self):
        n = int(0.5 * self.sr); t = self._t(n)
        f = 48 + 110 * np.exp(-t * 38)
        body = np.sin(2 * np.pi * np.cumsum(f) / self.sr) * np.exp(-t * 6.5)
        click = hp(self.rng.standard_normal(n), 2500, self.sr) * np.exp(-t * 350) * 0.35
        return lp(distort((body + click) * 1.8, 1.6), 5500, self.sr)

    def _make_snare(self):
        n = int(0.3 * self.sr); t = self._t(n)
        body = (np.sin(2 * np.pi * 185 * t) + 0.6 * np.sin(2 * np.pi * 330 * t)) * np.exp(-t * 28)
        crack = bp(self.rng.standard_normal(n), 1500, 9000, self.sr) * np.exp(-t * 17)
        return lp(distort((0.6 * body + crack) * 1.5, 1.8), 9000, self.sr) * np.minimum(1, t / 0.0008)

    def _make_hat(self, seconds):
        n = int(seconds * self.sr); t = self._t(n)
        return lp(hp(self.rng.standard_normal(n), 7000, self.sr), 10000, self.sr) * np.exp(-t * 4 / seconds)

    def kick(self, vel=1.0):
        return self._kicks[int(self.rng.integers(2))] * vel

    def snare(self, vel=1.0):
        return self._snares[int(self.rng.integers(3))] * vel

    def hat(self, vel=1.0):
        return self._hats[int(self.rng.integers(3))] * vel

    def open_hat(self, vel=1.0):
        return self._open * vel

    def rim(self, vel=1.0):
        return self._rim * vel


@lru_cache(maxsize=4)
def _levels(sr: int) -> dict[str, float]:
    """RMS of the procedural voices' reference notes and hits, so the sampled ones sit at the same level."""
    v = Voices(np.random.default_rng(0), sr)
    rms = lambda x: float(np.sqrt(np.mean(np.square(x[:int(0.5 * sr)])))) + 1e-9
    out = {c: rms(v.keys(c, 60, 1.0, 0.8)) for c in ("piano", "rhodes", "nylon", "pad")}
    return out | {"upright": rms(v.bass(48, 1.0, 0.8, "piano")), "finger": rms(v.bass(48, 1.0, 0.8, "rhodes")),
                  "kick": rms(v._kicks[0]), "snare": rms(v._snares[0]), "hat": rms(v._hats[0]),
                  "open_hat": rms(v._open), "rim": rms(v._rim)}


class SampledVoices:
    """The same voices played by FluidR3_GM programs (or the recorded library) and the Room/Standard kits."""

    def __init__(self, sampled, sr: int = SR):
        self.s = sampled
        lv = _levels(sr)
        self.colours = {"piano": Program(0, velocity=(45, 110), tail=0.3, rms=lv["piano"]),
                        "rhodes": Program(4, velocity=(45, 110), tail=0.3, rms=lv["rhodes"]),
                        "nylon": Program(26, velocity=(50, 110), tail=0.2, rms=lv["nylon"]),
                        "pad": Program(11, velocity=(45, 105), tail=0.8, rms=lv["pad"])}
        self.upright = Program(32, velocity=(60, 115), tail=0.12, rms=lv["upright"])
        self.finger = Program(33, velocity=(65, 120), tail=0.08, rms=lv["finger"])
        # the saturated procedural kick and snare have a low crest factor: matching their RMS would push the
        # recorded transients far above them, so the sampled ones are matched a little lower
        self.hits = {"kick": Hit(8, 36, 120, 0.5, PUNCH * lv["kick"]),
                     "snare": Hit(0, 38, 120, 0.4, PUNCH * lv["snare"]),
                     "hat": Hit(0, 42, 90, 0.2, lv["hat"]), "open_hat": Hit(0, 46, 90, 0.5, lv["open_hat"]),
                     "rim": Hit(0, 37, 100, 0.2, lv["rim"])}

    def keys(self, colour, m, dur, vel=1.0):
        return self.s.play(self.colours[colour], m, dur, vel)

    def lead(self, colour, m, dur, vel=1.0):
        return self.s.play(self.colours[colour], m, dur, vel)

    def bass(self, m, dur, vel=1.0, colour="piano"):
        return self.s.play(self.upright if BASS_COLOUR[colour] == "upright" else self.finger, m, dur, vel)

    def kick(self, vel=1.0):
        return self.s.hit(self.hits["kick"], vel)

    def snare(self, vel=1.0):
        return self.s.hit(self.hits["snare"], vel)

    def hat(self, vel=1.0):
        return self.s.hit(self.hits["hat"], vel)

    def open_hat(self, vel=1.0):
        return self.s.hit(self.hits["open_hat"], vel)

    def rim(self, vel=1.0):
        return self.s.hit(self.hits["rim"], vel)


# ---------------------------------------------------------------- genre
class BoomBapGenre(Genre):
    name = "boombap"
    description = ("90s boom bap hip-hop: hard dusty kick, cracking snare on 2 and 4, swung sixteenths, "
                   "chopped soul and jazz chords, upright bass, sparse hook, vinyl and tape")
    bpm = (84, 96)
    modes = ("minor", "dorian", "phrygian", "major")
    voices = {"piano": "chopped soul piano stabs", "rhodes": "chopped rhodes stabs over a finger bass",
              "nylon": "muted jazz guitar chops over a finger bass", "pad": "vibraphone chops"}

    def instruments(self, rng, sr, sampled):
        return Voices(rng, sr) if sampled is None else SampledVoices(sampled, sr)

    def start(self, piece):
        r = piece.rng
        piece.state["kick"] = (0, 10) if piece.style.drums == "soft" else KICKS[int(r.integers(len(KICKS)))]
        a, b = r.choice(len(CHOPS), 2, replace=False)
        piece.state["chops"] = (CHOPS[int(a)], CHOPS[int(b)])

    def chord(self, chord, scale):
        """Minor and major sevenths get a major ninth (the soul colour); dominants and triads stay as they are."""
        tones = chord.tones
        if len(tones) != 4 or not chord.degree:
            return chord
        iv = [t - tones[0] for t in tones]
        d = chord.degree - 1
        ninth = scale[(d + 1) % 7] + 12 * ((d + 1) // 7) + 12 - tones[0]
        if ninth != 14 or iv[1:] in ([4, 7, 10], [3, 6, 10], [3, 6, 9]):
            return chord
        return Chord(chord.degree, tuple(tones) + (tones[0] + 14,))

    # ------------------------------------------------------------ schedule
    def schedule(self, piece, p):
        s, ins, add, rng = piece.style, piece.inst, piece.add, piece.rng
        B, pct = 60 / s.bpm, swing_pct(s.swing)
        e, k = p.energy, p.key_offset
        at = lambda x: swung(x, pct) * B + 0.004 * float(rng.random())
        # chops: short stabs of the voicing (the passing chord from beat 3)
        held = p.section == "breakdown" or e < 0.35 or "drums" not in p.layers
        chops = ((0, 7), (8, 7)) if held else piece.state["chops"][p.bar % 2]
        vc = piece.voice([x + k for x in p.chord.tones]); piece.center = float(np.mean(vc))
        vp = None
        if p.passing is not None:
            vp = piece.voice([x + k for x in p.passing.tones]); piece.center = float(np.mean(vp))
        g = (0.075 if held else 0.09) * (0.8 + 0.4 * e)
        for step, ln in chops:
            if held and step == 8 and vp is None and p.bar % 2 == 0:
                continue                                          # one long chop a bar, a re-chop every other
            voicing = vp if vp is not None and step >= 8 else vc
            vel = 0.8 if step % 4 == 0 else 0.7
            for i, m in enumerate(voicing):
                add(ins.keys(s.chords, m, ln / 4 * B * 0.9, vel), at(step / 4) + i * 0.004, (i - 1.5) * 0.15, g)
        # bass: the composer's line, short and on the swing grid
        for n in sorted(p.bass, key=lambda n: n.beat):
            add(ins.bass(n.midi, min(n.length, 1.5) * B * 0.85, 0.85 if n.beat == 0 else 0.7, s.chords),
                at(n.beat), 0, 0.36)
        if "drums" in p.layers:
            self._drums(piece, p, B, at)
        # the hook: the composer's melody, sparse
        if "lead" in p.layers and s.lead != "none" and hook_bar(p):
            for n in sorted(p.melody, key=lambda n: n.beat):
                add(ins.lead(s.lead, n.midi, min(n.length, 2) * B * 0.9, 0.8), at(n.beat), 0.2,
                    0.22 if s.lead == "pad" else 0.18)
        if "counter" in p.layers:
            colour = "pad" if s.chords != "pad" else "rhodes"
            for n in p.counter:
                add(ins.keys(colour, n.midi, n.length * B * 0.9, 0.5), at(n.beat) + 0.02, -0.3, 0.07)

    def _drums(self, piece, p, B, at):
        s, ins, add, rng = piece.style, piece.inst, piece.add, piece.rng
        e = p.energy
        dg = 0.75 + 0.5 * e
        soft = s.drums == "soft"
        for step in piece.state["kick"]:
            add(ins.kick(1.0 if step == 0 else 0.85), at(step / 4), 0, (0.5 if step % 4 == 0 else 0.4) * dg)
        back = ins.rim if soft else ins.snare
        for b in (1, 3):
            add(back(1.0), at(b), 0.05, (0.3 if soft else 0.44) * dg)
        if not soft and (s.drums == "busy" or e >= 0.6):
            for step in GHOSTS:
                if rng.random() < 0.35:
                    add(ins.snare(0.5), at(step / 4), 0.05, 0.07 * dg)
        sixteenths = s.drums == "busy" or e >= 0.7
        open_bar = not soft and p.bar % 2 == 1
        for step in range(16):
            if step % 2 and not sixteenths:
                continue
            if open_bar and step == 14:
                add(ins.open_hat(0.8), at(3.5), -0.3, 0.07 * dg)
                continue
            vel = 1.0 if step % 4 == 0 else 0.8 if step % 2 == 0 else 0.55
            add(ins.hat(vel), at(step / 4), -0.3, 0.085 * dg * (0.85 + 0.3 * float(rng.random())))
        if e >= 0.5 and p.section_bar == SECTION_BARS.get(p.section, 8) - 1:   # a fill into the next section
            for step in (13, 14, 15):
                add(back(0.6 + 0.1 * (step - 13)), at(step / 4), 0.1, 0.16 * dg)

    def master(self, s):
        return Master(space=0.35 * s.space, reverb=0.03 + 0.08 * s.space, room=0.3 + 0.6 * s.space,
                      vinyl=min(1.0, 0.35 + 0.55 * s.vinyl), wow=0.4, tone=int(3200 * 3.5 ** s.brightness),
                      drive=1.8, threshold=-18.0, ratio=3.5)


GENRE = BoomBapGenre()
