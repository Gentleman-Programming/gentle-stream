"""Jazz: extended chords, walking bass, brushed drums and a strong swing (S2: "Acordes extendidos, walking bass,
batería con escobillas y swing marcado").

The `chord` hook adds extensions to every diatonic chord (sevenths always, ninths, elevenths on minor chords,
thirteenths on dominants). The chord instrument comps rootless voicings (third and seventh plus two colour tones)
in swung, syncopated stabs (Charleston and its relatives); the upright bass walks one quarter note per beat (chord
tones on beats 1 and 3, a passing tone on 2, an approach into the next root on 4) and plays in two when the drums
rest; the drums are a brush swirl, brush taps and a pedal hat on 2 and 4, a feathered kick and a spang-a-lang ride
whose skip notes fall on the triplet. The lead swings the composer's melody. The master is a warm, clean room: a
touch of vinyl, almost no tape wow, low drive. With the samples backend: GM 32 acoustic bass, 0 piano, 4 rhodes,
26 jazz guitar, 11 vibraphone, 66 tenor sax and the Jazz (32) and Brush (40) kits.
"""
from __future__ import annotations

from collections import OrderedDict
from functools import lru_cache

import numpy as np

from ..composer import FIRST_FORM
from ..samples import Hit, Program, reference_rms
from ..style import MODES
from ..synth import SR, adsr, bp, hp, lp, mtof, osc
from .base import Genre, Master
from .lofi import Instruments

SECTION_BARS = dict(FIRST_FORM)
# comping rhythms: (beat, swung off-beat?, length in beats); the off-beat lands on the swing position
CALM = (((0, 0, 1.4), (1, 1, 1.0)),                      # Charleston
        ((0, 1, 1.0), (2, 0, 1.4)),                      # reverse Charleston
        ((1, 1, 0.8), (3, 1, 0.3)),                      # pushes on the "and" of 2 and 4
        ((0, 0, 0.6), (2, 1, 1.0)))
BUSY = (((0, 1, 0.5), (1, 1, 0.5), (3, 0, 0.6)),
        ((1, 0, 0.4), (2, 1, 0.5), (3, 1, 0.3)),
        ((0, 0, 0.5), (1, 1, 0.6), (2, 1, 0.3)))
# rootless voicing colour tones per chord quality, in order of preference (semitones above the root)
COLOURS = {"dominant": (14, 13, 15, 21, 20, 7), "minor": (14, 17, 7), "half-diminished": (6, 17),
           "major": (14, 7, 21)}
BASS_RANGE = (28, 55)


def swing_position(bpm: float) -> float:
    """Where a swung off-beat falls inside its beat: the triplet (2/3), slightly straighter at fast tempos."""
    return 2 / 3 - 0.05 * float(np.clip((bpm - 110) / 30, 0, 1))


def swing(x: float, pos: float) -> float:
    """Beat position `x` with its eighth-note off-beats moved to `pos`."""
    w = np.floor(x); f = x - w
    return float(w + (f * pos / 0.5 if f < 0.5 else pos + (f - 0.5) * (1 - pos) / 0.5))


def quality(iv) -> str:
    third, fifth, seventh = iv[1], iv[2], iv[3] if len(iv) > 3 else None
    if third == 4:
        return "dominant" if seventh == 10 else "major"
    return "half-diminished" if fifth == 6 else "minor"


# ---------------------------------------------------------------- voices
class Voices:
    """Procedural jazz voices: the lo-fi keys, a vibraphone, a breathy tenor, an upright bass and a brushed kit."""

    def __init__(self, rng: np.random.Generator, sr: int = SR):
        self.rng, self.sr = rng, sr
        self.lofi = Instruments(rng, sr)
        self._ride = [self._make_ride() for _ in range(2)]
        t = self._t(int(0.4 * sr))
        self._kick = lp(np.sin(2 * np.pi * np.cumsum(48 + 30 * np.exp(-t * 30)) / sr) * np.exp(-t * 9), 220)
        t = self._t(int(0.09 * sr))
        self._pedal = hp(rng.standard_normal(len(t)), 5000) * np.exp(-t * 70) * np.minimum(1, t / 0.004)
        self._swish = bp(rng.standard_normal(3 * sr), 1800, 7500)
        self._notes: OrderedDict[tuple, np.ndarray] = OrderedDict()

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

    def _t(self, n):
        return np.arange(n) / self.sr

    def _make_ride(self):
        n = int(1.6 * self.sr); t = self._t(n)
        ping = sum(np.sin(2 * np.pi * f * (1 + 0.01 * self.rng.standard_normal()) * t + self.rng.random() * 6)
                   * np.exp(-t * (2.5 + f / 800)) for f in (347, 523, 791, 1189, 1612, 2253, 3011, 3897))
        wash = hp(lp(self.rng.standard_normal(n), 12000), 4500) * np.exp(-t * 2.2)
        stick = hp(self.rng.standard_normal(n), 2500) * np.exp(-t * 300)
        x = hp(ping * 0.12 + wash * 0.8 + stick * 0.6, 350) * np.minimum(1, t / 0.0015)
        return x / np.max(np.abs(x))

    # tonal
    def vibes(self, m, dur, vel=1.0):
        f = mtof(m); n = int((dur + 0.8) * self.sr); t = self._t(n)
        s = (np.sin(2 * np.pi * f * t) + 0.25 * np.sin(2 * np.pi * 3.98 * f * t) * np.exp(-t * 6)
             + 0.06 * np.sin(2 * np.pi * 9.9 * f * t) * np.exp(-t * 14))
        s += lp(self.rng.standard_normal(n), 3000) * np.exp(-t * 250) * 0.15          # mallet
        env = np.minimum(1, t / 0.002) * np.exp(-t * 1.1) * np.clip((dur + 0.8 - t) / 0.8, 0, 1)
        return s * env * (1 + 0.22 * np.sin(2 * np.pi * 5.5 * t)) * vel * 0.8

    def sax(self, m, dur, vel=1.0):
        f = mtof(m); n = int((dur + 0.12) * self.sr); t = self._t(n)
        vib = 1 + 0.004 * np.sin(2 * np.pi * 5.2 * t) * np.clip((t - 0.15) / 0.2, 0, 1)
        scoop = 2 ** (-0.3 / 12 * np.exp(-t / 0.03))
        fr = f * vib * scoop
        s = osc("saw", fr, n, self.sr) * 0.6 + osc("pulse", fr, n, self.sr, duty=0.35) * 0.4
        s = lp(lp(s, 900 + 1800 * vel), 2600 + 1000 * vel)
        s += bp(self.rng.standard_normal(n), 1500, 5000) * 0.05                          # breath
        return s * adsr(n, self.sr, 0.03, 0.12, 0.8, 0.1) * vel * 0.9

    def bass(self, m, dur, vel=1.0):
        d, v = max(1, round(dur / 0.05)) * 0.05, max(1, round(vel * 8)) / 8
        return self._cached(("bass", int(m), d, v), lambda: self._bass(int(m), d, v))

    def _bass(self, m, dur, vel):
        f = mtof(m); n = int((dur + 0.12) * self.sr); t = self._t(n)
        s = sum(np.sin(2 * np.pi * k * f * t + self.rng.random() * 0.3) * k ** -1.2 * np.exp(-t * (1.2 + 1.1 * k))
                for k in range(1, 7))
        s += lp(self.rng.standard_normal(n), 400) * np.exp(-t * 45) * 0.4                 # finger on the string
        env = np.minimum(1, t / 0.004) * np.clip((dur + 0.12 - t) / 0.12, 0, 1)
        return lp(s * env, 900 + 900 * vel) * vel

    def _colour(self, colour, m, dur, vel, lead):
        d, v = max(1, round(dur / 0.05)) * 0.05, max(1, round(vel * 8)) / 8
        return self._cached((colour, lead, int(m), d, v), lambda: self._play(colour, int(m), d, v, lead))

    def _play(self, colour, m, dur, vel, lead):
        if colour == "pad":
            return self.sax(m, dur, vel) if lead else self.vibes(m, dur, vel)
        if colour == "nylon":   # a warm archtop: the plucked string, darker
            return lp(self.lofi.nylon(m, dur, vel), 2400)
        return getattr(self.lofi, colour)(m, dur, vel)

    def keys(self, colour, m, dur, vel=1.0):
        return self._colour(colour, m, dur, vel, False)

    def lead(self, colour, m, dur, vel=1.0):
        return self._colour(colour, m, dur, vel, True)

    # drums
    def ride(self, vel=1.0):
        return self._ride[int(self.rng.integers(2))] * vel

    def swirl(self, dur):
        n = int((dur + 0.15) * self.sr); t = self._t(n)
        x = np.resize(np.roll(self._swish, -int(self.rng.integers(len(self._swish)))), n)
        circle = 0.65 + 0.35 * np.sin(2 * np.pi * t / max(dur / 2, 0.05) - np.pi / 2)
        return x * np.sin(np.pi * t / t[-1]) ** 1.5 * circle

    def tap(self, vel=1.0):
        return self.lofi.brush() * vel

    def kick(self, vel=1.0):
        return self._kick * vel

    def pedal(self, vel=1.0):
        return self._pedal * vel


@lru_cache(maxsize=4)
def _levels(sr: int) -> dict[str, float]:
    """RMS of the procedural voices' reference notes and hits, so the sampled ones sit at the same level."""
    v = Voices(np.random.default_rng(0), sr)
    rms = lambda x: float(np.sqrt(np.mean(np.square(x[:int(0.5 * sr)]))))
    return {"guitar": rms(v.keys("nylon", 60, 1.0, 0.8)), "vibes": rms(v.vibes(60, 1.0, 0.8)),
            "sax": rms(v.sax(60, 1.0, 0.8)), "bass": rms(v.bass(60, 1.0, 0.8)), "ride": rms(v.ride(1.0)),
            "tap": rms(v.tap(1.0)), "kick": rms(v.kick(1.0)), "pedal": rms(v.pedal(1.0))} | reference_rms(sr)


class SampledVoices:
    """The same voices played by FluidR3_GM programs and the Jazz/Brush kits (the brush swirl stays procedural:
    the kit's swirl is a short hit, the swirl is a continuous sweep)."""

    def __init__(self, sampled, proc: Voices, sr: int = SR):
        self.s, self.proc = sampled, proc
        lv = _levels(sr)
        self.colours = {"piano": Program(0, velocity=(35, 100), tail=1.0, rms=lv["piano"]),
                        "rhodes": Program(4, velocity=(40, 105), tail=0.8, rms=lv["rhodes"]),
                        "nylon": Program(26, velocity=(45, 105), tail=0.6, rms=lv["guitar"]),
                        "pad": Program(11, velocity=(45, 105), tail=1.2, rms=lv["vibes"])}
        self.sax = Program(66, velocity=(55, 110), tail=0.2, rms=lv["sax"])
        self.upright = Program(32, velocity=(60, 115), tail=0.15, rms=lv["bass"])
        self.hits = {"ride": Hit(32, 51, 90, 1.5, lv["ride"]), "tap": Hit(40, 38, 100, 0.4, lv["tap"]),
                     "kick": Hit(32, 36, 90, 0.4, lv["kick"]), "pedal": Hit(32, 44, 100, 0.3, lv["pedal"])}

    def keys(self, colour, m, dur, vel=1.0):
        return self.s.play(self.colours[colour], m, dur, vel)

    def lead(self, colour, m, dur, vel=1.0):
        return self.s.play(self.sax if colour == "pad" else self.colours[colour], m, dur, vel)

    def bass(self, m, dur, vel=1.0):
        return self.s.play(self.upright, m, dur, vel)

    def swirl(self, dur):
        return self.proc.swirl(dur)

    def ride(self, vel=1.0):
        return self.s.hit(self.hits["ride"], vel)

    def tap(self, vel=1.0):
        return self.s.hit(self.hits["tap"], vel)

    def kick(self, vel=1.0):
        return self.s.hit(self.hits["kick"], vel)

    def pedal(self, vel=1.0):
        return self.s.hit(self.hits["pedal"], vel)


# ---------------------------------------------------------------- genre
class JazzGenre(Genre):
    name = "jazz"
    description = "small-group jazz: extended chords, rootless comping, walking bass, brushed drums, strong swing"
    bpm = (60, 140)
    modes = ("dorian", "major", "mixolydian", "minor")
    voices = {"piano": "acoustic piano comping", "rhodes": "rhodes electric piano", "nylon": "warm jazz guitar",
              "pad": "vibraphone (tenor sax when it plays the lead)"}

    def instruments(self, rng, sr, sampled):
        proc = Voices(rng, sr)
        return proc if sampled is None else SampledVoices(sampled, proc, sr)

    def start(self, piece):
        piece.state["bass"] = None                                   # last bass note, to walk from
        piece.state["busy"] = 0.3 + 0.4 * float(piece.rng.random())  # how often the comping gets busier

    def chord(self, chord, scale):
        if len(chord.tones) < 4:
            return chord
        d = chord.degree - 1

        def deg(k):
            return scale[(d + k) % 7] + 12 * ((d + k) // 7)

        tones = list(chord.tones[:4])
        root = tones[0]
        kind = quality([t - root for t in tones])
        ninth, eleventh, thirteenth = deg(8) - root, deg(10) - root, deg(12) - root
        # the raised dominant (minor modes) takes its scale's b9/b13: idiomatic on a V7
        if kind == "dominant":
            tones += [root + (ninth if ninth in (13, 14, 15) else 14), root + (thirteenth if thirteenth in (20, 21)
                                                                               else 21)]
        elif kind == "minor":
            tones += [root + 14, root + 17]
        elif kind == "half-diminished":
            tones += [root + 17]
        else:
            tones += [root + 14]
        return type(chord)(chord.degree, tuple(tones))

    # ------------------------------------------------------------ schedule
    def schedule(self, piece, p):
        s = piece.style
        B, S = 60 / s.bpm, swing_position(s.bpm)
        self._comp(piece, p, B, S)
        self._bass(piece, p, B)
        if "drums" in p.layers:
            self._drums(piece, p, B, S)
        jitter = lambda: 0.004 * float(piece.rng.random())
        if "lead" in p.layers:
            for n in sorted(p.melody, key=lambda n: n.beat):
                on, off = swing(n.beat, S), swing(n.beat + n.length, S)
                vel = 0.85 if n.beat % 1 else 0.75                     # jazz eighths lean on the off-beat
                piece.add(piece.inst.lead(s.lead, n.midi, (off - on) * B * 0.95, vel), on * B + jitter(), 0.15,
                          0.30 if s.lead == "pad" else 0.26)
        if "counter" in p.layers:
            colour = "pad" if s.chords != "pad" else "nylon"
            for n in p.counter:
                on, off = swing(n.beat, S), swing(n.beat + n.length, S)
                piece.add(piece.inst.keys(colour, n.midi, (off - on) * B * 0.95, 0.55), on * B + 0.02, -0.3, 0.09)

    def _voicing(self, piece, chord, k):
        """Rootless: third, seventh and two colour tones (ninth, eleventh, thirteenth or fifth), voice-led."""
        tones = list(chord.tones)
        root = tones[0]
        iv = [t - root for t in tones]
        kind = quality(iv)
        if len(iv) == 4 and kind == "dominant":     # the composer's chromatic dominants: add 9 and 13
            iv += [14, 21]
        picked = [iv[1], iv[3]] if len(iv) > 3 else iv[1:]
        for c in COLOURS[kind]:
            if len(picked) >= 4:
                break
            if c in iv and c not in picked:
                picked.append(c)
        vc = piece.voice([root + k] + [root + k + x for x in picked])
        piece.center = 0.75 * float(np.mean(vc)) + 0.25 * (piece.ton + 16 + k)
        return vc

    def _comp(self, piece, p, B, S):
        s, rng = piece.style, piece.rng
        if "drums" not in p.layers or p.energy < 0.35:
            hits = [(0.0, 2.0 if p.passing is not None else 3.9, 0.55)]
            if p.passing is not None:
                hits.append((2.0, 1.9, 0.5))
        else:
            busy = s.drums == "busy" or p.energy >= 0.75
            pool = CALM + BUSY if busy and rng.random() < piece.state["busy"] else CALM
            pattern = pool[int(rng.integers(len(pool)))]
            hits = [(b + (S if off else 0.0), ln, 0.6 + 0.2 * float(rng.random())) for b, off, ln in pattern]
            if p.passing is not None:
                hits = [(b, min(ln, 2 - b) if b < 2 else ln, v) for b, ln, v in hits]
                if not any(b >= 2 for b, _, _ in hits):
                    hits.append((2.0, 1.2, 0.65))
        g = 0.12 * (0.8 + 0.4 * p.energy)
        for beat, ln, vel in hits:
            chord = p.passing if p.passing is not None and beat >= 2 else p.chord
            vc = self._voicing(piece, chord, p.key_offset)
            t = beat * B + 0.005 * float(rng.random())
            for i, m in enumerate(vc):
                piece.add(piece.inst.keys(s.chords, m, ln * B * 0.95, vel), t, (i - 1.5) * 0.15, g)

    def _bass(self, piece, p, B):
        st, ins, ton, k = piece.state, piece.inst, piece.ton, p.key_offset
        pcs = lambda c: [(ton + t + k) % 12 for t in c.tones]

        def place(pc, near):
            return min((m for m in range(BASS_RANGE[0], BASS_RANGE[1] + 1) if m % 12 == pc),
                       key=lambda m: (abs(m - near), m))

        chord_pcs = pcs(p.chord)
        prev = st["bass"] if st["bass"] is not None else 38
        b1 = place(chord_pcs[0], prev)
        third_beat = p.passing if p.passing is not None else p.chord
        if p.section in ("intro", "breakdown"):     # in two: root and the fifth (or the passing root)
            b3 = place(pcs(third_beat)[0] if p.passing is not None else chord_pcs[2], b1 + 3)
            for beat, m in ((0, b1), (2, b3)):
                piece.add(ins.bass(m, 2 * B * 0.92, 0.75), beat * B, 0, 0.40)
            st["bass"] = b3
            return
        rng = piece.rng
        up = b1 < 40 if abs(b1 - 40) > 4 else rng.random() < 0.5
        if p.passing is not None:
            pc3 = pcs(p.passing)[0]
        else:
            pc3 = chord_pcs[2] if rng.random() < 0.6 else chord_pcs[1]
        b3 = place(pc3, b1 + (5 if up else -5))
        if b3 == b1:
            b3 = place(chord_pcs[1], b1 + (4 if up else -4))
        # beat 2: a scale tone between beats 1 and 3, else a chromatic one, else the chord's third
        scale = {(ton + k + o) % 12 for o in MODES[piece.style.mode]}
        lo, hi = sorted((b1, b3))
        between = [m for m in range(lo + 1, hi) if m % 12 in scale] or list(range(lo + 1, hi))
        b2 = between[len(between) // 2] if between else place(chord_pcs[1], b1 + (3 if up else -3))
        # beat 4: the composer's approach into the next root, or a chromatic neighbour of this root
        approach = [n for n in p.bass if n.beat >= 3]
        pc4 = approach[-1].midi % 12 if approach else (chord_pcs[0] + (1 if b3 > b1 else -1)) % 12
        b4 = place(pc4, b3)
        for beat, m in enumerate((b1, b2, b3, b4)):
            piece.add(ins.bass(m, B * 0.92, 0.85 if beat % 2 else 0.75), beat * B, 0, 0.42)
        st["bass"] = b4

    def _drums(self, piece, p, B, S):
        s, ins, rng, add = piece.style, piece.inst, piece.rng, piece.add
        e = p.energy
        dg = 0.75 + 0.5 * e
        for h in (0, 2):                                         # the brush circles, one per half bar
            add(ins.swirl(2 * B), h * B, -0.15, 0.07 * dg)
        for b in (1, 3):                                         # brush taps and pedal hat on 2 and 4
            add(ins.tap(0.7), b * B, 0.1, 0.18 * dg)
            add(ins.pedal(0.8), b * B, -0.35, 0.10 * dg)
        for b in range(4):                                       # feathered kick
            add(ins.kick(0.6), b * B, 0, 0.10 * dg)
        if s.drums == "soft":                                    # brushes only: taps take the skip notes
            for b in (1, 3):
                add(ins.tap(0.45), (b + S) * B, 0.1, 0.09 * dg)
        else:
            ride = [(0, 1.0), (1, 0.85), (1 + S, 0.6), (2, 1.0), (3, 0.85), (3 + S, 0.6)]
            if s.drums == "busy" or e >= 0.75:
                ride += [(b + S, 0.5) for b in (0, 2) if rng.random() < 0.3]
            for beat, vel in ride:
                add(ins.ride(vel), beat * B, 0.35, 0.17 * dg)
            if e >= 0.6 and rng.random() < 0.5:                  # a snare "drop" on a swung off-beat
                add(ins.tap(0.55), (int(rng.integers(4)) + S) * B, 0.1, 0.11 * dg)
        if e >= 0.5 and p.section_bar == SECTION_BARS[p.section] - 1:   # a triplet pickup into the next section
            for j in (1, 2):
                add(ins.tap(0.5 + 0.15 * j), (3 + j / 3) * B, 0.1, 0.12 * dg)

    def master(self, s):
        return Master(space=0.8 * s.space, reverb=0.05 + 0.15 * s.space, room=0.25 + 0.9 * s.space,
                      vinyl=0.25 * s.vinyl, wow=0.12, tone=int(min(20000, 3800 * 4 ** s.brightness)), drive=1.35,
                      threshold=-16.0, ratio=2.0)   # light bus compression: keep the dynamics


GENRE = JazzGenre()
