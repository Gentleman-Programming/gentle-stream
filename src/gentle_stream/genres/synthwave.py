"""Synthwave: 80s analog synths, a pulsing octave bass and a big gated-reverb snare.

What the listener was promised (S2): "Sintes analógicos ochentosos, bajo en octavas, batería con caja grande y
reverb." Detuned saw pads whose low-pass slowly opens over each chord, a bright saw/square lead with delayed vibrato
and a dotted-eighth echo, a 16th-note arpeggiator in the high-energy sections, an eighth-note bass alternating root
and octave through a resonant low-pass, and drums with four-on-the-floor kicks in the B sections, a gated-reverb
snare on 2 and 4 and straight 16th hats. Clean, open, roomy master: no vinyl, only a hint of tape wow.

With the samples backend the voices are FluidR3_GM programs (81 Saw Lead, 90 Polysynth, 38 Synth Bass 1...) and
the Electronic drum kit (bank 128, preset 24), with the procedural gated tail layered on the sampled snare.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from ..samples import Hit, Program
from ..synth import SR, adsr, bp, distort, hp, lp, mtof, osc
from .base import Genre, Master

DUR_STEP = 0.05
ARP_ENERGY = 0.7           # sections at or above this energy get the 16th-note arpeggiator
PULSE_ENERGY = 0.4         # below it (and in the breakdown) the bass holds its notes instead of pulsing
FOUR_ON_THE_FLOOR = ("B", "B'")
PAD_DETUNE = (-9.0, 0.0, 8.0)          # cents: the three saws of a pad voice
# 8th-note positions (in beats) of the chord-colour stabs when Style.chords is not the pad itself
STAB_PATTERNS = ((0, 1.5, 3), (0, 2, 2.5), (0.5, 1.5, 2.5, 3.5), (0, 0.75, 2, 2.75))
ARP_SHAPES = ("up", "updown", "octaves")
OPEN_HATS = ((), (1.5, 3.5), (0.5, 1.5, 2.5, 3.5))   # 8th-note offbeats where the hat opens


def _bucket(dur: float) -> float:
    return max(1, round(dur / DUR_STEP)) * DUR_STEP


def _echo(x: np.ndarray, delay: float, sr: int, taps=(0.32, 0.12)) -> np.ndarray:
    """`x` followed by darker repeats every `delay` seconds (a tape-style delay baked into the note)."""
    if delay <= 0:
        return x
    d = int(delay * sr)
    out = np.zeros(len(x) + d * len(taps))
    out[:len(x)] = x
    wet = lp(x, 3000, sr)
    for i, g in enumerate(taps, 1):
        out[i * d:i * d + len(x)] += wet * g
    return out


class Instruments:
    """Procedural synthwave voices. Notes are cached per piece (no randomness in the tonal voices); the drums are a
    few noise variants drawn once from the piece's generator."""

    def __init__(self, rng: np.random.Generator, sr: int = SR):
        self.rng, self.sr = rng, sr
        self._cache: dict[tuple, np.ndarray] = {}
        self._kick = self._make_kick()
        self._snares = [self._make_snare() for _ in range(2)]   # (dry hit, gated tail)
        self._hats = [self._make_hat(False) for _ in range(3)]
        self._open = [self._make_hat(True) for _ in range(2)]
        self._crash = self._make_crash()

    def _t(self, n):
        return np.arange(n) / self.sr

    def _cached(self, key, make):
        x = self._cache.get(key)
        if x is None:
            x = self._cache[key] = make()
        return x

    # ------------------------------------------------------------ tonal voices
    def pad(self, m, dur, vel=1.0, bright=0.5):
        """Three detuned saws; the low-pass opens slowly over the note (by `bright`, 0-1)."""
        d, b = _bucket(dur), round(bright, 1)
        return self._cached(("pad", m, d, b), lambda: self._pad(m, d, b)) * vel

    def _pad(self, m, dur, bright):
        n = int((dur + 0.8) * self.sr); t = self._t(n); f = mtof(m)
        s = sum(osc("saw", f * 2 ** (c / 1200), n, self.sr, phase=i / 3) for i, c in enumerate(PAD_DETUNE)) / 3
        w = bright * (1 - np.exp(-t / 1.4))
        s = lp(s, 900, self.sr) * (1 - w) + lp(s, 3500, self.sr) * w
        return s * adsr(n, self.sr, 0.3, 0.4, 0.85, 0.8) * 0.5

    def stab(self, color, m, dur, vel=1.0):
        """The Style.chords colour: polysynth stab (piano), FM electric piano (rhodes), synth pluck (nylon) or a
        soft pad note (pad)."""
        d = _bucket(dur)
        return self._cached(("stab", color, m, d), lambda: self._stab(color, m, d)) * vel

    def _stab(self, color, m, dur):
        f = mtof(m)
        if color == "pad":
            return self._pad(m, dur, 0.3)
        if color == "rhodes":
            n = int((dur + 0.4) * self.sr); t = self._t(n)
            s = (np.sin(2 * np.pi * f * t + 1.6 * np.exp(-t * 4) * np.sin(2 * np.pi * f * t))
                 + 0.25 * np.sin(2 * np.pi * 14 * f * t) * np.exp(-t * 18))
            return s * adsr(n, self.sr, 0.004, 1.2, 0.3, 0.3, hold=dur) * 0.45
        if color == "nylon":
            n = int((dur + 0.25) * self.sr); t = self._t(n)
            s = osc("pulse", f, n, self.sr, duty=0.3)
            w = np.exp(-t * 12)
            s = lp(s, 900, self.sr) * (1 - w) + lp(s, 5000, self.sr) * w
            return s * np.exp(-t * 5) * np.minimum(1, t / 0.003) * np.clip((dur + 0.25 - t) / 0.05, 0, 1) * 0.45
        n = int((dur + 0.2) * self.sr); t = self._t(n)   # piano: brassy polysynth
        s = (osc("saw", f * 2 ** (-6 / 1200), n, self.sr) + osc("saw", f * 2 ** (6 / 1200), n, self.sr, 0.5)) / 2
        w = np.exp(-t * 7)
        s = lp(s, 1200, self.sr) * (1 - w) + lp(s, 5500, self.sr) * w
        return s * adsr(n, self.sr, 0.005, 0.25, 0.55, 0.2, hold=dur) * 0.4

    def lead(self, m, dur, vel=1.0, color="piano", echo=0.0):
        """Bright saw (piano), square (rhodes), thin pulse (nylon) or soft triangle-saw (pad) with delayed
        vibrato; `echo` seconds bakes in a dotted-eighth style delay."""
        d = _bucket(dur)
        return self._cached(("lead", color, m, d, round(echo, 3)),
                            lambda: _echo(self._lead(color, m, d), echo, self.sr)) * vel

    def _lead(self, color, m, dur):
        n = int((dur + 0.15) * self.sr); t = self._t(n)
        depth = np.clip((t - 0.2) / 0.3, 0, 1) * 15                       # cents
        f = mtof(m) * 2 ** (depth * np.sin(2 * np.pi * 5.5 * t) / 1200)
        if color == "rhodes":
            s, cut = osc("square", f, n, self.sr), 3800
        elif color == "nylon":
            s, cut = osc("pulse", f, n, self.sr, duty=0.2), 4500
        elif color == "pad":
            s, cut = 0.6 * osc("triangle", f, n, self.sr) + 0.4 * osc("saw", f, n, self.sr), 2500
        else:
            s, cut = osc("saw", f, n, self.sr), 5000
        att = 0.06 if color == "pad" else 0.008
        return lp(s, cut, self.sr) * adsr(n, self.sr, att, 0.2, 0.8, 0.15, hold=dur) * 0.5

    def arp(self, m, dur, vel=1.0):
        d = _bucket(dur)
        return self._cached(("arp", m, d), lambda: self._arp(m, d)) * vel

    def _arp(self, m, dur):
        n = int((dur + 0.12) * self.sr); t = self._t(n)
        s = 0.6 * osc("square", mtof(m), n, self.sr) + 0.4 * osc("saw", mtof(m) * 1.003, n, self.sr, 0.3)
        w = np.exp(-t * 25)
        s = lp(s, 1500, self.sr) * (1 - w) + lp(s, 6000, self.sr) * w
        return s * np.exp(-t * 9) * np.minimum(1, t / 0.002) * np.clip((dur + 0.12 - t) / 0.03, 0, 1) * 0.4

    def bass(self, m, dur, vel=1.0):
        """Saw plus square through a low-pass with a resonant bump and a fast filter envelope."""
        d, v = _bucket(dur), round(vel, 1)
        return self._cached(("bass", m, d, v), lambda: self._bass(m, d, v))

    def _bass(self, m, dur, vel):
        n = int((dur + 0.04) * self.sr); t = self._t(n); f = mtof(m)
        s = 0.6 * osc("saw", f, n, self.sr) + 0.4 * osc("square", f, n, self.sr, 0.25)
        cut = 260 + 900 * vel
        w = np.exp(-t * 18)
        dark = lp(s, cut * 0.5, self.sr) + 0.5 * bp(s, cut * 0.4, cut * 0.65, self.sr)
        bright = lp(s, cut * 1.6, self.sr) + 0.5 * bp(s, cut * 1.2, cut * 2.0, self.sr)
        env = adsr(n, self.sr, 0.003, 0.08, 0.7, 0.04, hold=dur)
        return distort((dark * (1 - w) + bright * w) * env * 1.4, 1.3) * vel * 0.6

    # ------------------------------------------------------------ drums
    def _make_kick(self):
        n = int(0.4 * self.sr); t = self._t(n)
        f = 48 + 120 * np.exp(-t * 30)
        body = np.sin(2 * np.pi * np.cumsum(f) / self.sr) * np.exp(-t * 8)
        click = hp(self.rng.standard_normal(n), 2000, self.sr) * np.exp(-t * 350) * 0.3
        return distort(body + click, 1.6) * 0.8

    def _make_snare(self):
        """A snare hit through a gated reverb: a dense, bright tail that stops dead after ~0.27 s."""
        gate, n = 0.27, int(0.32 * self.sr); t = self._t(n)
        noise = self.rng.standard_normal(n)
        body = np.sin(2 * np.pi * 185 * t) * np.exp(-t * 28) * 0.6
        snap = bp(noise, 1500, 8000, self.sr) * np.exp(-t * 32)
        tail_env = np.minimum(1, t / 0.01) * (1 - 0.45 * np.clip(t / gate, 0, 1)) * np.clip((gate - t) / 0.012, 0, 1)
        tail = lp(bp(self.rng.standard_normal(n), 500, 7000, self.sr), 5000, self.sr) * tail_env * 0.45
        return (body + snap) * 0.75, tail * 0.75

    def gated_tail(self):
        return self._snares[0][1]

    def _make_hat(self, open_):
        n = int((0.32 if open_ else 0.06) * self.sr); t = self._t(n)
        return hp(self.rng.standard_normal(n), 7500, self.sr) * np.exp(-t * (11 if open_ else 85)) * 0.45

    def _make_crash(self):
        n = int(1.6 * self.sr); t = self._t(n)
        return hp(self.rng.standard_normal(n), 5000, self.sr) * np.exp(-t * 2.8) * np.minimum(1, t / 0.003) * 0.3

    def kick(self, g=1.0):
        return self._kick * g

    def snare(self, g=1.0):
        dry, tail = self._snares[int(self.rng.integers(len(self._snares)))]
        return (dry + tail) * g

    def hat(self, open=False, g=1.0):
        pool = self._open if open else self._hats
        return pool[int(self.rng.integers(len(pool)))] * g

    def crash(self, g=1.0):
        return self._crash * g


def _rms(x: np.ndarray, sr: int) -> float:
    return float(np.sqrt(np.mean(np.square(x[:int(0.5 * sr)])))) + 1e-12


@lru_cache(maxsize=4)
def _targets(sr: int) -> dict[str, float]:
    """RMS of the procedural voices' reference notes (middle C, 1 s, velocity 0.8): the sampled programs are scaled
    to it, so both backends sit in the same mix."""
    p = Instruments(np.random.default_rng(0), sr)
    out = {"pad": _rms(p.pad(60, 1.0, 0.8, 0.5), sr), "arp": _rms(p.arp(60, 1.0, 0.8), sr),
           "bass": _rms(p.bass(60, 1.0, 0.8), sr), "kick": _rms(p.kick(), sr), "hat": _rms(p.hat(), sr),
           "open": _rms(p.hat(True), sr), "crash": _rms(p.crash(), sr),
           "snare": _rms(p._snares[0][0], sr)}
    for c in ("piano", "rhodes", "nylon", "pad"):
        out["lead-" + c] = _rms(p.lead(60, 1.0, 0.8, c), sr)
        out["stab-" + c] = _rms(p.stab(c, 60, 1.0, 0.8), sr)
    return out


class SampledVoices:
    """The same voices on FluidR3_GM programs and the Electronic kit (bank 128, preset 24)."""

    KIT = 24
    LEAD = {"piano": 81, "rhodes": 80, "nylon": 87, "pad": 90}     # Saw Lead, Square Lead, Bass+Lead, Polysynth
    STAB = {"piano": 62, "rhodes": 5, "nylon": 80, "pad": 90}      # Synth Brass 1, DX piano, Square, Polysynth

    def __init__(self, sampled, rng: np.random.Generator, sr: int = SR):
        self.s, self.rng, self.sr = sampled, rng, sr
        self.proc = Instruments(rng, sr)
        r = _targets(sr)
        self.pad_p = Program(90, velocity=(50, 105), tail=1.0, rms=r["pad"])
        self.arp_p = Program(80, velocity=(60, 110), tail=0.12, rms=r["arp"])
        self.bass_p = Program(38, velocity=(70, 120), tail=0.06, rms=r["bass"])
        self.lead_p = {c: Program(p, velocity=(60, 115), tail=0.3, rms=r["lead-" + c]) for c, p in self.LEAD.items()}
        self.stab_p = {c: Program(p, velocity=(55, 110), tail=0.4, rms=r["stab-" + c]) for c, p in self.STAB.items()}
        self.drums = {"kick": Hit(self.KIT, 36, 115, 0.45, r["kick"]), "snare": Hit(self.KIT, 38, 110, 0.4, r["snare"]),
                      "hat": Hit(self.KIT, 42, 90, 0.15, r["hat"]), "open": Hit(self.KIT, 46, 90, 0.4, r["open"]),
                      "crash": Hit(self.KIT, 49, 100, 1.6, r["crash"])}
        self._lead_cache: dict[tuple, np.ndarray] = {}
        self._snare = None

    def pad(self, m, dur, vel=1.0, bright=0.5):
        return self.s.play(self.pad_p, m, dur, 0.55 + 0.45 * bright) * vel

    def stab(self, color, m, dur, vel=1.0):
        return self.s.play(self.stab_p[color], m, dur, vel)

    def lead(self, m, dur, vel=1.0, color="piano", echo=0.0):
        x = self.s.play(self.lead_p[color], m, dur, vel)
        if echo <= 0:
            return x
        key = (color, int(round(m)), _bucket(dur), round(vel, 1), round(echo, 3))
        y = self._lead_cache.get(key)
        if y is None:
            if len(self._lead_cache) > 512:
                self._lead_cache.clear()
            y = self._lead_cache[key] = _echo(x, echo, self.sr)
        return y

    def arp(self, m, dur, vel=1.0):
        return self.s.play(self.arp_p, m, dur, vel)

    def bass(self, m, dur, vel=1.0):
        return self.s.play(self.bass_p, m, dur, vel)

    def kick(self, g=1.0):
        return self.s.hit(self.drums["kick"], g)

    def snare(self, g=1.0):
        """The sampled snare with the procedural gated-reverb tail layered on it."""
        if self._snare is None:
            hit, tail = self.s.hit(self.drums["snare"]), self.proc.gated_tail()
            x = np.zeros(max(len(hit), len(tail)))
            x[:len(hit)] += hit
            x[:len(tail)] += tail
            self._snare = x
        return self._snare * g

    def hat(self, open=False, g=1.0):
        return self.s.hit(self.drums["open" if open else "hat"], g)

    def crash(self, g=1.0):
        return self.s.hit(self.drums["crash"], g)


class SynthwaveGenre(Genre):
    name = "synthwave"
    description = "synthwave: 80s analog synths, detuned saw pads, pulsing octave bass, big gated-reverb snare"
    bpm = (80, 118)
    modes = ("minor", "dorian", "major")
    voices = {"piano": "brassy polysynth stabs / bright saw lead", "rhodes": "FM electric piano / square lead",
              "nylon": "plucked pulse synth", "pad": "detuned saw pad"}

    def instruments(self, rng, sr, sampled):
        return SampledVoices(sampled, rng, sr) if sampled is not None else Instruments(rng, sr)

    def start(self, piece):
        r = piece.rng
        piece.state["stabs"] = STAB_PATTERNS[int(r.integers(len(STAB_PATTERNS)))]
        piece.state["arp"] = ARP_SHAPES[int(r.integers(len(ARP_SHAPES)))]
        piece.state["open_hats"] = OPEN_HATS[int(r.integers(len(OPEN_HATS)))]

    def schedule(self, piece, p):
        s, ins, add, st = piece.style, piece.inst, piece.add, piece.state
        B = 60 / s.bpm; BAR = 4 * B
        e, k = p.energy, p.key_offset
        high = e >= ARP_ENERGY and p.section != "breakdown"
        # pads: the chord (and the passing chord on beats 3-4) as root plus voice-led upper tones
        segments = [(p.chord, 0.0, 4.0 if p.passing is None else 2.0)]
        if p.passing is not None:
            segments.append((p.passing, 2.0, 2.0))
        voicings = []
        for chord, at, beats in segments:
            vc = piece.voice([x + k for x in chord.tones]); piece.center = float(np.mean(vc))
            root = piece.ton + 12 + (chord.tones[0] + k) % 12
            while root > min(vc) - 3:
                root -= 12
            notes = [root] + vc
            voicings.append((at, vc))
            for i, m in enumerate(notes):
                add(ins.pad(m, beats * B * 0.99, 0.8, 0.25 + 0.6 * e), at * B, (i - (len(notes) - 1) / 2) * 0.3,
                    0.10 if i else 0.07)

        def chord_at(beat):
            return voicings[-1][1] if beat >= 2 and len(voicings) > 1 else voicings[0][1]

        # chord-colour stabs (the pad colour is the pad itself)
        if s.chords != "pad" and e >= PULSE_ENERGY and p.section != "breakdown":
            for b in st["stabs"]:
                for i, m in enumerate(chord_at(b)):
                    add(ins.stab(s.chords, m + 12, 0.45 * B, 0.8), b * B + i * 0.004, (i - 1) * 0.35,
                        0.05 + 0.03 * e)
        # arpeggiator: 16ths over the chord tones, an octave above the pad
        if high:
            for j in range(16):
                vc = [m + 12 for m in chord_at(j / 4)]
                if st["arp"] == "updown":
                    seq = vc + vc[-2:0:-1]
                elif st["arp"] == "octaves":
                    seq = vc + [m + 12 for m in vc]
                else:
                    seq = vc
                add(ins.arp(seq[j % len(seq)], 0.25 * B * 0.9, 1.0 if j % 4 == 0 else 0.75), j * 0.25 * B,
                    0.3 if j % 2 else -0.3, 0.08 + 0.04 * e)
        # bass: root / octave eighths through the composer's line (held notes when the energy is low)
        if e >= PULSE_ENERGY and p.section != "breakdown":
            for j in range(8):
                beat = j / 2
                n = max((x for x in p.bass if x.beat <= beat), key=lambda x: x.beat, default=p.bass[0])
                add(ins.bass(n.midi + (12 if j % 2 else 0), 0.5 * B * 0.8, 0.9 if j % 2 == 0 else 0.7), beat * B,
                    0, 0.30)
        else:
            for n in p.bass:
                add(ins.bass(n.midi, n.length * B * 0.95, 0.6), n.beat * B, 0, 0.22)
        # drums
        if "drums" in p.layers:
            dg = (0.75 + 0.5 * e) * (0.7 if s.drums == "soft" else 1.0)
            for b in ((0, 1, 2, 3) if p.section in FOUR_ON_THE_FLOOR else (0, 2)):
                add(ins.kick(), b * B, 0, 0.38 * dg)
            if s.drums == "busy" and p.section not in FOUR_ON_THE_FLOOR and p.bar % 2:
                add(ins.kick(), 2.5 * B, 0, 0.3 * dg)
            for b in (1, 3):
                add(ins.snare(), b * B, 0.05, 0.32 * dg)
            if s.drums == "busy" and e >= 0.6 and p.section_bar % 8 == 7:
                for b in (3.25, 3.5, 3.75):
                    add(ins.snare(), b * B, 0.05, 0.16 * dg)
            if p.section_bar == 0 and e >= 0.6:
                add(ins.crash(), 0, -0.35, 0.18 * dg)
            steps = 4 if s.drums != "soft" and e >= 0.45 else 2
            for j in range(4 * steps):
                beat = j / steps
                is_open = beat in st["open_hats"]
                add(ins.hat(is_open), beat * B, -0.25, (0.09 if j % (steps // 2) == 0 else 0.055) * dg
                    * (1.3 if is_open else 1.0))
        # lead with a dotted-eighth echo, and the counter-melody on the chord colour
        if "lead" in p.layers and s.lead != "none":
            for n in p.melody:
                add(ins.lead(n.midi, n.length * B * 0.92, 0.9, s.lead, 0.75 * B), n.beat * B, 0.1, 0.22)
        if "counter" in p.layers:
            for n in p.counter:
                add(ins.stab(s.chords, n.midi, n.length * B * 0.9, 0.6), n.beat * B + 0.02, -0.3, 0.08)

    def master(self, s):
        return Master(space=0.45 + 0.55 * s.space, reverb=0.16 + 0.24 * s.space, room=0.35 + 1.2 * s.space,
                      vinyl=0.0, wow=0.25, tone=min(20000, int(6000 * 3 ** s.brightness)), drive=1.8)


GENRE = SynthwaveGenre()
