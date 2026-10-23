"""Electro house: four-on-the-floor kick, dirty distorted bass riffs, sidechain pumping, builds and drops.

The target sound (S6): a punchy, tight kick on every beat, a clap on 2 and 4, open hats on the off-beats and 16th
closed hats on the drops (B, B'); big "dirty" bass riffs (three detuned saws through a waveshaper and a low-pass
that opens on every note and drifts with a slow LFO) playing 8th-note riffs in A and 16th-note riffs on the drops,
always on the composer's bass line; supersaw chord stabs over sustained supersaw pads; sidechain pumping that
ducks the pads, stabs and bass on every kick; and before every drop (the last two bars of A and of the
breakdown) a white-noise riser, a snare roll that speeds up and a low-pass sweep that opens the pads into the
crash. The breakdown keeps only dark, filtered pads. Loud, clean master: more drive and bus compression than
lo-fi, no vinyl and no tape wow. Riffs and stab rhythms are generic grids picked per piece; the pitches always come
from the composer, so the material is original.

Sidechain: the duck curve is a function of absolute time modulo the beat (bars start on the beat grid) that is
exactly 1 on every kick, ducks within 5 ms and recovers by 80% of the beat, so the pumping is phase-continuous
across bars and never clicks. The bass LFO is read at the absolute beat (bar * 4 + beat).

With the samples backend the drums are the Electronic kit (bank 128, preset 24), the stabs and lead FluidR3_GM
81 Saw Lead and the pads 90 Polysynth (low-passed by the same sweep); the dirty bass, the riser and the sidechain
stay procedural: no GM bass has the detuned, distorted, filter-swept character the genre needs.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from ..composer import FIRST_FORM, FORM
from ..samples import Hit, Program
from ..synth import SR, adsr, bp, distort, hp, lp, mtof, osc
from .base import Genre, Master

DUR_STEP = 0.05
STRONG = ("B", "B'")                    # the drops
BUILD_BARS = 2                          # bars of riser, snare roll and filter sweep before a drop
DUCK, DUCK_STRONG = 0.45, 0.65          # sidechain depth in drum bars / on the drops
DUCK_ATTACK = 0.005                     # seconds
DUCK_RELEASE = 0.8                      # fraction of the beat after which the gain is back to 1
LFO_BEATS = 16                          # period of the bass cutoff LFO, in beats
SUPERSAW = (-25.0, -16.0, -8.0, 0.0, 7.0, 15.0, 24.0)   # cents
BASS_DETUNE = (-14.0, 0.0, 13.0)
PAD_DARK = 700                          # breakdown / intro pad cutoff, Hz
SWEEP_TOP = 9000                        # where the build's sweep ends
HAAS = 0.012                            # seconds between the left and right copies of a stab
SAMPLED_PAD_GAIN = 2.4                  # GM 90 decays under a held chord: ~7.5 dB below the procedural pad
# riffs as (16th step, interval over the composer's bass note, length in 16ths)
RIFFS_8 = (((2, 0, 2), (6, 0, 2), (10, 0, 2), (14, 0, 2)),
           ((0, 0, 2), (2, 12, 2), (4, 0, 2), (6, 12, 2), (8, 0, 2), (10, 12, 2), (12, 0, 2), (14, 7, 2)),
           ((2, 0, 2), (4, 0, 2), (6, 12, 2), (10, 0, 2), (12, 0, 2), (14, 7, 2)))
RIFFS_16 = (((2, 0, 1), (3, 0, 1), (6, 12, 1), (7, 0, 1), (10, 0, 1), (11, 0, 1), (13, 12, 1), (14, 0, 2)),
            ((1, 0, 1), (2, 0, 2), (5, 0, 1), (6, 12, 1), (8, 0, 1), (10, 0, 2), (13, 7, 1), (14, 12, 2)),
            ((2, 0, 2), (4, 0, 1), (6, 0, 2), (9, 12, 1), (10, 0, 2), (12, 0, 1), (14, 7, 1), (15, 12, 1)))
STAB_PATTERNS = ((2, 6, 10, 14), (0, 3, 6, 10, 14), (2, 5, 8, 11, 14), (3, 6, 10, 13))   # 16th steps


def _bucket(dur: float) -> float:
    return max(1, round(dur / DUR_STEP)) * DUR_STEP


def _q(cut: float) -> float:
    """Cutoff rounded to a sixth of an octave (cache-friendly, inaudible steps between notes)."""
    return float(2 ** (round(np.log2(max(cut, 40.0)) * 6) / 6))


def sidechain(n: int, t0: float, beat: float, sr: int, depth: float) -> np.ndarray:
    """Gain curve of `n` samples starting `t0` seconds after a bar start: 1 on every beat (the kick), down to
    1 - `depth` after DUCK_ATTACK, back to 1 by DUCK_RELEASE of the beat. A function of the time modulo the beat,
    so consecutive bars (which start on the beat) continue the same phase."""
    tau = (t0 + np.arange(n) / sr) % beat
    duck = np.minimum(1.0, tau / DUCK_ATTACK) * 0.5 * (1 + np.cos(np.pi * np.minimum(1.0, tau / (DUCK_RELEASE * beat))))
    return 1.0 - depth * duck


def _sweep(x: np.ndarray, c0: float, c1: float, sr: int) -> np.ndarray:
    """Low-pass whose cutoff glides from `c0` to `c1` (log scale) over `x`: a crossfade between a few fixed
    filters, cheap and click-free."""
    top = 0.45 * sr
    c0, c1 = min(c0, top), min(c1, top)
    if abs(np.log2(c1 / c0)) < 1e-3:
        return lp(x, c0, sr)
    k = max(2, int(np.ceil(abs(np.log2(c1 / c0)) * 1.5)) + 1)
    ys = np.stack([lp(x, c, sr) for c in np.geomspace(c0, c1, k)])
    u = np.linspace(0, k - 1, len(x))
    i = np.minimum(u.astype(int), k - 2); f = u - i
    cols = np.arange(len(x))
    return ys[i, cols] * (1 - f) + ys[i + 1, cols] * f


TABLE = 2048


def _supersaw(f: float, n: int, sr: int) -> np.ndarray:
    """Seven detuned saws at fixed pitch read from one band-limited wavetable (harmonics up to 0.45 sr): far
    cheaper than seven `osc` passes and alias-free."""
    top = f * 2 ** (max(SUPERSAW) / 1200)
    k = np.arange(1, int(min(TABLE // 2 - 1, 0.45 * sr / top)) + 1)
    spec = np.zeros(TABLE // 2 + 1, complex)
    spec[k] = 1j * TABLE / (np.pi * k)          # irfft of this is the saw's Fourier series, -(2/pi) sum sin(2 pi k t)/k
    table = np.fft.irfft(spec, TABLE)
    table = np.append(table, table[0])
    i = np.arange(n)
    out = np.zeros(n)
    for v, c in enumerate(SUPERSAW):           # linear interpolation by hand: several times faster than np.interp
        pos = (0.37 * v + i * (f * 2 ** (c / 1200) / sr)) % 1.0 * TABLE
        q = pos.astype(np.int32)
        fr = pos - q
        out += table[q] * (1 - fr) + table[q + 1] * fr
    return out / len(SUPERSAW)


def _echo(x: np.ndarray, delay: float, sr: int, taps=(0.3, 0.12)) -> np.ndarray:
    if delay <= 0:
        return x
    d = int(delay * sr)
    out = np.zeros(len(x) + d * len(taps))
    out[:len(x)] = x
    wet = lp(x, 3500, sr)
    for i, g in enumerate(taps, 1):
        out[i * d:i * d + len(x)] += wet * g
    return out


class Instruments:
    """Procedural electro-house voices. Tonal notes are cached per piece; the drums are a few noise variants drawn
    once from the piece's generator."""

    def __init__(self, rng: np.random.Generator, sr: int = SR):
        self.rng, self.sr = rng, sr
        self._cache: dict[tuple, np.ndarray] = {}
        self._raw: dict[int, np.ndarray] = {}
        self._kick = self._make_kick()
        self._claps = [self._make_clap() for _ in range(2)]
        self._snare = self._make_snare()
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

    def pump(self, sig, t, beat, depth):
        """`sig` (starting `t` seconds into the bar) ducked by the kick-synchronous sidechain curve."""
        return sig * sidechain(len(sig), t, beat, self.sr, depth)

    def _supersaw(self, m, n):
        """`n` samples of the raw supersaw on MIDI note `m`, rendered once per note (longest length so far) and
        sliced: every pad and stab shares it."""
        raw = self._raw.get(m)
        if raw is None or len(raw) < n:
            raw = self._raw[m] = _supersaw(mtof(m), max(n, int(1.0 * self.sr)), self.sr)
        return raw[:n]

    # ------------------------------------------------------------ tonal voices
    def pad(self, m, dur, vel=1.0, cut0=3000.0, cut1=3000.0):
        """Sustained supersaw; its low-pass glides from `cut0` to `cut1` Hz over the note."""
        d, a, b = _bucket(dur), _q(cut0), _q(cut1)
        return self._cached(("pad", m, d, a, b), lambda: self._pad(m, d, a, b)) * vel

    def _pad(self, m, dur, c0, c1):
        n = int((dur + 0.4) * self.sr)
        s = _sweep(self._supersaw(m, n), c0, c1, self.sr)
        return s * adsr(n, self.sr, 0.04, 0.3, 0.85, 0.4, hold=dur) * 0.55

    def stab(self, color, m, dur, vel=1.0):
        """The Style.chords colour: supersaw stab (piano), FM electric piano (rhodes), pluck (nylon) or a short
        pad note (pad)."""
        d = _bucket(dur)
        return self._cached(("stab", color, m, d), lambda: self._stab(color, m, d)) * vel

    def _stab(self, color, m, dur):
        f = mtof(m)
        if color == "pad":
            return self._pad(m, dur, 2500, 2500)
        if color == "rhodes":
            n = int((dur + 0.3) * self.sr); t = self._t(n)
            s = (np.sin(2 * np.pi * f * t + 1.8 * np.exp(-t * 5) * np.sin(2 * np.pi * f * t))
                 + 0.2 * np.sin(2 * np.pi * 14 * f * t) * np.exp(-t * 20))
            return s * adsr(n, self.sr, 0.003, 0.5, 0.35, 0.25, hold=dur) * 0.5
        n = int((dur + 0.15) * self.sr); t = self._t(n)
        if color == "nylon":
            s = osc("pulse", f, n, self.sr, duty=0.3)
            w = np.exp(-t * 18)
            s = lp(s, 1000, self.sr) * (1 - w) + lp(s, 6000, self.sr) * w
            return s * np.exp(-t * 7) * np.minimum(1, t / 0.002) * np.clip((dur + 0.15 - t) / 0.03, 0, 1) * 0.5
        s = self._supersaw(m, n)                          # piano: the supersaw stab
        w = np.exp(-t * 10)
        s = lp(s, 1800, self.sr) * (1 - w) + lp(s, 7000, self.sr) * w
        return s * adsr(n, self.sr, 0.003, 0.12, 0.7, 0.12, hold=dur) * 0.6

    def lead(self, m, dur, vel=1.0, color="piano", echo=0.0):
        """Saw (piano), square (rhodes), pulse (nylon) or soft triangle-saw (pad) lead with delayed vibrato;
        `echo` seconds bakes in a delay."""
        d = _bucket(dur)
        return self._cached(("lead", color, m, d, round(echo, 3)),
                            lambda: _echo(self._lead(color, m, d), echo, self.sr)) * vel

    def _lead(self, color, m, dur):
        n = int((dur + 0.12) * self.sr); t = self._t(n)
        depth = np.clip((t - 0.18) / 0.25, 0, 1) * 18
        f = mtof(m) * 2 ** (depth * np.sin(2 * np.pi * 5.8 * t) / 1200)
        if color == "rhodes":
            s, cut = osc("square", f, n, self.sr), 4000
        elif color == "nylon":
            s, cut = osc("pulse", f, n, self.sr, duty=0.2), 5000
        elif color == "pad":
            s, cut = 0.6 * osc("triangle", f, n, self.sr) + 0.4 * osc("saw", f, n, self.sr), 2800
        else:
            s, cut = 0.5 * (osc("saw", f * 2 ** (-7 / 1200), n, self.sr) + osc("saw", f * 2 ** (7 / 1200), n,
                                                                                self.sr, 0.4)), 6000
        att = 0.05 if color == "pad" else 0.006
        return lp(s, cut, self.sr) * adsr(n, self.sr, att, 0.2, 0.8, 0.12, hold=dur) * 0.5

    def bass(self, m, dur, vel=1.0, cut=900.0):
        """Dirty bass: detuned saws through a waveshaper, a low-pass that snaps open on the attack and settles
        at `cut` Hz, and a clean sine sub underneath."""
        d, c, v = _bucket(dur), _q(cut), round(vel, 1)
        return self._cached(("bass", m, d, c, v), lambda: self._bass(m, d, c, v))

    def _bass(self, m, dur, cut, vel):
        n = int((dur + 0.03) * self.sr); t = self._t(n); f = mtof(m)
        saws = sum(osc("saw", f * 2 ** (c / 1200), n, self.sr, phase=0.3 * i)
                   for i, c in enumerate(BASS_DETUNE)) / len(BASS_DETUNE)
        dirty = distort(saws * 3.0, 1.0)
        w = np.exp(-t * 14)
        top = min(0.45 * self.sr, cut * 3.5)
        dirty = lp(dirty, cut, self.sr) * (1 - w) + lp(dirty, top, self.sr) * w
        sub = np.sin(2 * np.pi * f * t)
        env = adsr(n, self.sr, 0.003, 0.08, 0.85, 0.025, hold=dur)
        return distort((0.75 * dirty + 0.55 * sub) * env * 1.2, 1.2) * (0.6 + 0.4 * vel) * 0.7

    def sub(self, m, dur):
        d = _bucket(dur)
        return self._cached(("sub", m, d), lambda: self._sub(m, d))

    def _sub(self, m, dur):
        n = int((dur + 0.1) * self.sr); t = self._t(n)
        return np.sin(2 * np.pi * mtof(m) * t) * adsr(n, self.sr, 0.02, 0.1, 0.9, 0.1, hold=dur) * 0.7

    def riser(self, dur):
        """White noise whose band climbs and swells over `dur` seconds, cut off right at the drop."""
        d = _bucket(dur)
        return self._cached(("riser", d), lambda: self._riser(d))

    def _riser(self, dur):
        n = int(dur * self.sr); t = self._t(n)
        x = _sweep(hp(self.rng.standard_normal(n), 250, self.sr), 400, 12000, self.sr)
        swell = (t / dur) ** 2 * np.clip((dur - t) / 0.015, 0, 1)
        return x * swell * 0.5

    # ------------------------------------------------------------ drums
    def _make_kick(self):
        n = int(0.35 * self.sr); t = self._t(n)
        f = 50 + 170 * np.exp(-t * 38)
        body = np.sin(2 * np.pi * np.cumsum(f) / self.sr) * np.exp(-t * 13) * np.minimum(1, t / 0.0008)
        click = hp(self.rng.standard_normal(n), 2500, self.sr) * np.exp(-t * 400) * 0.35
        return distort(body + click, 2.2) * 0.85

    def _make_clap(self):
        n = int(0.32 * self.sr); t = self._t(n)
        noise = bp(self.rng.standard_normal(n), 900, 5500, self.sr)
        env = sum(np.where(t >= d, np.exp(-(t - d) * 160), 0.0) for d in (0.0, 0.011, 0.022))
        env = env + 0.45 * np.where(t >= 0.03, np.exp(-(t - 0.03) * 16), 0.0)
        return noise * np.minimum(env, 1.2) * 0.5

    def _make_snare(self):
        n = int(0.22 * self.sr); t = self._t(n)
        body = np.sin(2 * np.pi * 195 * t) * np.exp(-t * 30) * 0.6
        snap = bp(self.rng.standard_normal(n), 1200, 8000, self.sr) * np.exp(-t * 24)
        return (body + snap) * 0.6

    def _make_hat(self, open_):
        n = int((0.26 if open_ else 0.05) * self.sr); t = self._t(n)
        return hp(self.rng.standard_normal(n), 8000, self.sr) * np.exp(-t * (14 if open_ else 95)) * 0.45

    def _make_crash(self):
        n = int(1.8 * self.sr); t = self._t(n)
        return hp(self.rng.standard_normal(n), 4500, self.sr) * np.exp(-t * 2.4) * np.minimum(1, t / 0.002) * 0.32

    def kick(self, g=1.0):
        return self._kick * g

    def clap(self, g=1.0):
        return self._claps[int(self.rng.integers(len(self._claps)))] * g

    def snare(self, g=1.0):
        return self._snare * g

    def hat(self, g=1.0):
        return self._hats[int(self.rng.integers(len(self._hats)))] * g

    def open_hat(self, g=1.0):
        return self._open[int(self.rng.integers(len(self._open)))] * g

    def crash(self, g=1.0):
        return self._crash * g


def _rms(x: np.ndarray, sr: int) -> float:
    return float(np.sqrt(np.mean(np.square(x[:int(0.5 * sr)])))) + 1e-12


@lru_cache(maxsize=4)
def _targets(sr: int) -> dict[str, float]:
    """RMS of the procedural voices' reference notes (middle C, 1 s): the sampled programs are scaled to it, so
    both backends sit in the same mix."""
    p = Instruments(np.random.default_rng(0), sr)
    out = {"pad": _rms(p.pad(60, 1.0, 0.8, 3000, 3000), sr), "kick": _rms(p.kick(), sr),
           "clap": _rms(p._claps[0], sr), "snare": _rms(p.snare(), sr), "hat": _rms(p._hats[0], sr),
           "open": _rms(p._open[0], sr), "crash": _rms(p.crash(), sr)}
    for c in ("piano", "rhodes", "nylon", "pad"):
        out["lead-" + c] = _rms(p.lead(60, 1.0, 0.8, c), sr)
        out["stab-" + c] = _rms(p.stab(c, 60, 1.0, 0.8), sr)
    return out


class SampledVoices:
    """FluidR3_GM synths and the Electronic kit (bank 128, preset 24); the dirty bass, sub, riser and the
    sidechain stay procedural."""

    KIT = 24
    LEAD = {"piano": 81, "rhodes": 80, "nylon": 87, "pad": 90}     # Saw Lead, Square Lead, Bass+Lead, Polysynth
    STAB = {"piano": 81, "rhodes": 4, "nylon": 80, "pad": 90}      # Saw Lead, Rhodes, Square, Polysynth

    def __init__(self, sampled, rng: np.random.Generator, sr: int = SR):
        self.s, self.rng, self.sr = sampled, rng, sr
        self.proc = Instruments(rng, sr)
        r = _targets(sr)
        self.pad_p = Program(90, velocity=(55, 105), tail=0.4, rms=r["pad"])
        self.lead_p = {c: Program(p, velocity=(60, 115), tail=0.25, rms=r["lead-" + c]) for c, p in self.LEAD.items()}
        self.stab_p = {c: Program(p, velocity=(70, 120), tail=0.15, rms=r["stab-" + c]) for c, p in self.STAB.items()}
        # the kit's closed hat rings longer than the procedural one: half the reference RMS sits at the same level
        self.drums = {"kick": Hit(self.KIT, 36, 120, 0.4, r["kick"]), "clap": Hit(self.KIT, 39, 110, 0.35, r["clap"]),
                      "snare": Hit(self.KIT, 38, 105, 0.25, r["snare"]),
                      "hat": Hit(self.KIT, 42, 90, 0.12, r["hat"] * 0.5), "open": Hit(self.KIT, 46, 90, 0.3, r["open"]),
                      "crash": Hit(self.KIT, 49, 100, 1.8, r["crash"])}
        self._cache: dict[tuple, np.ndarray] = {}

    def pump(self, sig, t, beat, depth):
        return self.proc.pump(sig, t, beat, depth)

    def pad(self, m, dur, vel=1.0, cut0=3000.0, cut1=3000.0):
        """Polysynth through the same low-pass sweep as the procedural pad."""
        a, b = _q(cut0), _q(cut1)
        key = (int(round(m)), _bucket(dur), a, b)
        y = self._cache.get(key)
        if y is None:
            if len(self._cache) > 512:
                self._cache.clear()
            y = self._cache[key] = _sweep(self.s.play(self.pad_p, m, dur, 0.8), a, b, self.sr) * SAMPLED_PAD_GAIN
        return y * vel

    def stab(self, color, m, dur, vel=1.0):
        return self.s.play(self.stab_p[color], m, dur, vel)

    def lead(self, m, dur, vel=1.0, color="piano", echo=0.0):
        x = self.s.play(self.lead_p[color], m, dur, vel)
        return _echo(x, echo, self.sr) if echo > 0 else x

    def bass(self, m, dur, vel=1.0, cut=900.0):
        return self.proc.bass(m, dur, vel, cut)

    def sub(self, m, dur):
        return self.proc.sub(m, dur)

    def riser(self, dur):
        return self.proc.riser(dur)

    def kick(self, g=1.0):
        return self.s.hit(self.drums["kick"], g)

    def clap(self, g=1.0):
        return self.s.hit(self.drums["clap"], g)

    def snare(self, g=1.0):
        return self.s.hit(self.drums["snare"], g)

    def hat(self, g=1.0):
        return self.s.hit(self.drums["hat"], g)

    def open_hat(self, g=1.0):
        return self.s.hit(self.drums["open"], g)

    def crash(self, g=1.0):
        return self.s.hit(self.drums["crash"], g)


def _section_lengths() -> tuple[dict[str, int], dict[str, str]]:
    """Bars per section and the section that follows each one, from the composer's form."""
    lengths = dict(FIRST_FORM)
    nxt = {name: FORM[(i + 1) % len(FORM)][0] for i, (name, _) in enumerate(FORM)}
    nxt[FIRST_FORM[0][0]] = FIRST_FORM[1][0]
    return lengths, nxt


LENGTHS, NEXT = _section_lengths()


def build_bar(p) -> int | None:
    """0 or 1 for the two bars that build into a drop, otherwise None."""
    if NEXT.get(p.section) not in STRONG:
        return None
    i = p.section_bar - (LENGTHS[p.section] - BUILD_BARS)
    return i if 0 <= i < BUILD_BARS else None


def _active(p, beat):
    """The composer's bass note sounding at `beat`."""
    return max((x for x in p.bass if x.beat <= beat), key=lambda x: x.beat, default=p.bass[0])


class ElectroHouseGenre(Genre):
    name = "electrohouse"
    description = "electro house: four-on-the-floor kick, dirty distorted bass, sidechain pumping, builds and drops"
    bpm = (124, 132)
    modes = ("minor", "phrygian", "dorian")
    voices = {"piano": "supersaw chord stabs / bright saw lead", "rhodes": "FM electric piano stabs / square lead",
              "nylon": "short pluck synth", "pad": "wide sustained supersaw pad"}

    def instruments(self, rng, sr, sampled):
        return SampledVoices(sampled, rng, sr) if sampled is not None else Instruments(rng, sr)

    def start(self, piece):
        r = piece.rng
        piece.state["riff8"] = RIFFS_8[int(r.integers(len(RIFFS_8)))]
        piece.state["riff16"] = RIFFS_16[int(r.integers(len(RIFFS_16)))]
        piece.state["stabs"] = STAB_PATTERNS[int(r.integers(len(STAB_PATTERNS)))]

    def schedule(self, piece, p):
        s, ins, add, st = piece.style, piece.inst, piece.add, piece.state
        B = 60 / s.bpm
        e, k = p.energy, p.key_offset
        strong = p.section in STRONG
        drums = "drums" in p.layers
        build = build_bar(p)
        depth = (DUCK_STRONG if strong else DUCK) if drums else 0.0

        def tonal(sig, t, pan, g):
            add(ins.pump(sig, t, B, depth) if depth else sig, t, pan, g)

        # pads: the chord (and the passing chord on beats 3-4); dark in the intro and breakdown, swept open in
        # the builds (the cutoff follows the build's progress across both bars)
        dark = p.section in ("intro", "breakdown")
        base = PAD_DARK if dark else 1800 + 2500 * e

        def pad_cut(beat):
            if build is None:
                return base
            return base * (SWEEP_TOP / base) ** ((build * 4 + beat) / (4 * BUILD_BARS))

        segments = [(p.chord, 0.0, 4.0 if p.passing is None else 2.0)]
        if p.passing is not None:
            segments.append((p.passing, 2.0, 2.0))
        voicings = []
        pad_g = 0.3 if dark or s.chords == "pad" else 0.2
        for chord, at, beats in segments:
            vc = piece.voice([x + k for x in chord.tones]); piece.center = float(np.mean(vc))
            root = piece.ton + 12 + (chord.tones[0] + k) % 12
            while root > min(vc) - 3:
                root -= 12
            notes = [root] + vc
            voicings.append((at, vc))
            for i, m in enumerate(notes):
                pan = 0.0 if i == 0 else (-0.7 if i % 2 else 0.7) * (0.5 + 0.5 * i / len(notes))
                tonal(ins.pad(m, beats * B * 0.99, 0.8, pad_cut(at), pad_cut(at + beats)), at * B, pan,
                      pad_g * (0.7 if i == 0 else 1.0))

        def chord_at(beat):
            return voicings[-1][1] if beat >= 2 and len(voicings) > 1 else voicings[0][1]

        # supersaw (or Style.chords colour) stabs on the piece's 16th pattern, made wide by a Haas copy: the left
        # side plays on the grid, the right side HAAS seconds later
        if s.chords != "pad" and not dark and e >= 0.35:
            g = 0.38 if strong else 0.17
            for step in st["stabs"]:
                vc = chord_at(step / 4)
                for i, m in enumerate(vc):
                    x, t = ins.stab(s.chords, m + 12, 0.55 * B, 0.85), step * B / 4 + i * 0.003
                    tonal(x, t, -0.85, g)
                    tonal(x, t + HAAS, 0.85, g)
        # bass: a sub under the intro, nothing in the breakdown, otherwise the dirty riff on the composer's line
        if p.section == "intro":
            for n in p.bass:
                add(ins.sub(n.midi, n.length * B * 0.95), n.beat * B, 0, 0.1)
        elif p.section != "breakdown":
            for step, iv, ln in (st["riff16"] if strong else st["riff8"]):
                beat = step / 4
                m = _active(p, beat).midi + iv
                tonal(ins.bass(m, ln * B / 4 * 0.85, 0.9 if iv == 0 else 0.75, self.bass_cut(p, beat, build)),
                      beat * B, 0, 0.36)
        # drums: kick on every beat, clap on 2 and 4, open hats on the off-beats, 16th closed hats on the drops
        if drums:
            dg = (0.8 + 0.4 * e) * (0.75 if s.drums == "soft" else 1.0)
            for b in range(4):
                add(ins.kick(), b * B, 0, 0.38 * dg)
            for b in (1, 3):
                add(ins.clap(), b * B, 0.05, 1.2 * dg)
            for b in (0.5, 1.5, 2.5, 3.5):
                add(ins.open_hat(), b * B, -0.3, 0.25 * dg)
            if (strong and s.drums != "soft") or (s.drums == "busy" and e >= 0.5):
                for j in range(16):
                    add(ins.hat(), j * B / 4, 0.3, (0.25 if j % 2 else 0.16) * dg * (1.0 if strong else 0.7))
            if strong and p.section_bar == 0:
                add(ins.crash(), 0, -0.4, 0.22 * dg)
        # the build: one riser across both bars, a snare roll that speeds up and swells into the drop
        if build is not None:
            if build == 0:
                add(ins.riser(BUILD_BARS * 4 * B), 0, 0, 0.12 + 0.06 * e)
            if s.drums != "none":
                hits = [j / 2 for j in range(8)] if build == 0 else (
                    [j / 4 for j in range(12)] + [3 + j / 8 for j in range(8)])
                for b in hits:
                    prog = (build * 4 + b) / (4 * BUILD_BARS)
                    add(ins.snare(), b * B, 0.1, (0.08 + 0.2 * prog) * (0.8 + 0.4 * e))
        # lead with a dotted-eighth echo; the counter-melody on a quiet pluck
        if "lead" in p.layers and s.lead != "none":
            for n in p.melody:
                add(ins.lead(n.midi, n.length * B * 0.9, 0.9, s.lead, 0.75 * B), n.beat * B, 0.1, 0.3)
        if "counter" in p.layers:
            for n in p.counter:
                add(ins.lead(n.midi, n.length * B * 0.85, 0.6, "nylon"), n.beat * B + 0.01, -0.35, 0.06)

    @staticmethod
    def bass_cut(p, beat, build):
        """Dirty-bass cutoff: brighter with energy, drifting with a slow LFO in absolute beats; darker at the
        start of a build, opening into the drop."""
        lfo = 0.5 - 0.5 * np.cos(2 * np.pi * (p.bar * 4 + beat) / LFO_BEATS)
        cut = (350 + 900 * p.energy) * 2 ** (1.0 * lfo)
        if build is not None:
            cut *= 2 ** (-1 + 1.5 * (build * 4 + beat) / (4 * BUILD_BARS))
        return _q(cut)

    def master(self, s):
        return Master(space=0.25 + 0.3 * s.space, reverb=0.05 + 0.12 * s.space, room=0.1 + 0.4 * s.space,
                      vinyl=0.0, wow=0.0, tone=min(20000, int(10000 * 2 ** s.brightness)), drive=2.6,
                      threshold=-24.0, ratio=4.0)


GENRE = ElectroHouseGenre()
