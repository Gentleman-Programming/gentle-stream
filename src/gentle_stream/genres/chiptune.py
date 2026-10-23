"""Chiptune / 8-bit: original retro-game music on the four channels of an 8-bit console.

As described to the listener (S2): "Ondas cuadradas y triangulares, arpegios rápidos y batería de ruido: música de
juego retro original." Pulse 1 plays the lead (12.5/25/50% duty, 4-bit volume steps, vibrato that starts after the
attack, a quiet "echo" copy when the style has some space) and the counter-melody; pulse 2 plays every chord as a
fast single-note arpeggio (16ths or 32nds cycling root, third, fifth and octave or seventh); the triangle plays the
bass in eighths that jump octaves (4-bit stepped, no volume control, like the console's); the noise channel plays
the drums (pitch-dropping square kick, white-noise snare, very short hat ticks). The master is dry, clean and
bright: no vinyl, no tape wow, at most a tiny room. The tunes are the composer's own motifs: never the melody of an
existing game.

Samples do not fit 8-bit, so the samples backend plays these same procedural voices (the shared FluidR3_GM set
is ignored on purpose).
"""
from __future__ import annotations

import numpy as np

from ..composer import FIRST_FORM
from ..synth import SR, adsr, hp, lp, mtof, osc
from .base import Genre, Master

DUTY = {"piano": 0.5, "rhodes": 0.25, "nylon": 0.125, "pad": 0.25}   # Style.chords/lead -> pulse width
ARP_SHAPES = ((0, 1, 2, 3), (0, 1, 2, 3, 2, 1), (0, 2, 1, 3), (3, 2, 1, 0))   # indexes into the 4-note pool
KICK_PATTERNS = ((0, 2), (0, 2.5), (0, 1.5, 2), (0, 0.75, 2))
SECTION_BARS = dict(FIRST_FORM)
RAMP = 0.002   # seconds of de-click at the start and end of every tone
MIX = 1.6      # channel gains below are relative; this sets the level against the lo-fi reference


class Chip:
    """The console's channels as cached one-shot voices. Tones are deterministic (cached by note, duration and
    velocity bucket); the drums are rendered once per piece from noise drawn from the piece's generator."""

    DUR_STEP = 0.01
    VEL_STEPS = 8

    def __init__(self, rng: np.random.Generator, sr: int = SR):
        self.sr = sr
        self._cache: dict[tuple, np.ndarray] = {}
        noise = rng.choice((-1.0, 1.0), int(0.4 * sr))
        self._drums = {"kick": self._kick(noise), "snare": self._snare(noise), "hat": self._hat(noise)}
        for x in self._drums.values():
            x.flags.writeable = False

    # ------------------------------------------------------------ helpers
    def _t(self, n):
        return np.arange(n) / self.sr

    def _cached(self, key, make):
        x = self._cache.get(key)
        if x is None:
            if len(self._cache) > 2048:
                self._cache.clear()
            x = self._cache[key] = make()
            x.flags.writeable = False
        return x

    def _bucket(self, dur, vel):
        return max(1, round(dur / self.DUR_STEP)) * self.DUR_STEP, int(np.clip(round(vel * self.VEL_STEPS), 1,
                                                                              self.VEL_STEPS))

    def _declick(self, x):
        t = self._t(len(x))
        return x * np.minimum(1, t / RAMP) * np.clip((len(x) / self.sr - t) / RAMP, 0, 1)

    def _pulse(self, m, dur, vel, duty, attack, decay, sustain, release, vibrato):
        n = int((dur + release) * self.sr); t = self._t(n)
        f = mtof(m)
        if vibrato:   # delayed vibrato: steady pitch through the attack, then a ~6 Hz wobble fading in
            depth = vibrato * np.clip((t - 0.12) / 0.15, 0, 1)
            f = f * 2 ** (depth * np.sin(2 * np.pi * 5.8 * t) / 12)
        env = adsr(n, self.sr, attack, decay, sustain, release, hold=dur)
        env = np.where(t < dur, np.round(env * 15) / 15, env)   # 4-bit volume steps while the note is held
        return self._declick(osc("pulse", f, n, self.sr, duty=duty) * env * vel)

    # ------------------------------------------------------------ tonal channels
    def lead(self, m, dur, vel=1.0, duty=0.5, soft=False):
        """Pulse 1: the melody voice; `soft` swells in and wobbles deeper."""
        d, vb = self._bucket(dur, vel)
        return self._cached(("lead", int(m), d, vb, duty, soft), lambda: self._pulse(
            m, d, vb / self.VEL_STEPS, duty, 0.06 if soft else 0.004, 0.2, 0.75 if soft else 0.6, 0.03,
            0.45 if soft else 0.3))

    def arp(self, m, dur, vel=1.0, duty=0.25, soft=False):
        """Pulse 2: one short arpeggio step, a plucked blip (`soft`: held flatter)."""
        d, vb = self._bucket(dur, vel)
        return self._cached(("arp", int(m), d, vb, duty, soft), lambda: self._pulse(
            m, d, vb / self.VEL_STEPS, duty, 0.002, 0.06, 0.8 if soft else 0.45, 0.01, 0))

    def bass(self, m, dur, vel=1.0):
        """Triangle: 16-step quantized waveform at a constant level (the channel has no volume control)."""
        d, vb = self._bucket(dur, vel)

        def make():
            n = int(d * self.sr)
            x = np.round((osc("triangle", mtof(m), n, self.sr) + 1) * 7.5) / 7.5 - 1
            return self._declick(x * vb / self.VEL_STEPS)
        return self._cached(("bass", int(m), d, vb), make)

    # ------------------------------------------------------------ noise channel
    def _kick(self, noise):
        n = int(0.22 * self.sr); t = self._t(n)
        f = 48 + 150 * np.exp(-t * 45)
        body = osc("square", f, n, self.sr) * np.exp(-t * 16)
        click = lp(np.repeat(noise[:n // 8 + 1], 8)[:n], 1200) * np.exp(-t * 120) * 0.6
        return self._declick(lp(body, 900) + click)

    def _snare(self, noise):
        n = int(0.2 * self.sr); t = self._t(n)
        crunch = np.repeat(noise[1000:1000 + n // 2 + 1], 2)[:n]       # noise clocked at half rate
        tone = osc("square", 210 - 60 * np.minimum(1, t / 0.05), n, self.sr) * np.exp(-t * 45) * 0.35
        env = np.round(np.exp(-t * 26) * 15) / 15
        return self._declick(crunch * env * 0.8 + tone)

    def _hat(self, noise):
        n = int(0.035 * self.sr); t = self._t(n)
        return self._declick(hp(noise[9000:9000 + n], 6000) * np.exp(-t * 170))

    def kick(self):
        return self._drums["kick"]

    def snare(self):
        return self._drums["snare"]

    def hat(self):
        return self._drums["hat"]


class ChiptuneGenre(Genre):
    name = "chiptune"
    description = "8-bit: square and triangle waves, fast arpeggios, noise drums; original retro-game music"
    bpm = (100, 140)
    modes = ("major", "mixolydian", "minor", "dorian")
    voices = {"piano": "50% square wave", "rhodes": "25% pulse wave", "nylon": "thin 12.5% pulse wave",
              "pad": "soft swelling 25% pulse wave"}

    def instruments(self, rng, sr, sampled):
        return Chip(rng, sr)   # samples do not fit 8-bit: both backends play the chip voices

    def start(self, piece):
        r, s = piece.rng, piece.style
        piece.state["arp_shape"] = ARP_SHAPES[int(r.integers(len(ARP_SHAPES)))]
        # 32nds at moderate tempi (still under ~70 ms a step), 16ths otherwise; the soft colour stays at 16ths
        piece.state["arp_div"] = 8 if s.chords != "pad" and s.bpm <= 125 and r.random() < 0.5 else 4
        piece.state["sevenths"] = bool(r.random() < 0.4)
        piece.state["kick_beats"] = (0, 2) if s.drums == "soft" else KICK_PATTERNS[int(r.integers(len(KICK_PATTERNS)))]

    def _pool(self, piece, tones, k):
        """Four arpeggio notes: root under the voice-led upper tones, then third, fifth and seventh or octave."""
        iv = [x + k for x in tones]
        upper = piece.voice(iv[:4] if piece.state["sevenths"] and len(iv) >= 4 else iv[:3])
        piece.center = float(np.mean(upper))
        root = upper[0] - ((upper[0] - (piece.ton + iv[0])) % 12 or 12)
        pool = [root] + upper
        return (pool + [x + 12 for x in pool])[:4]

    def schedule(self, piece, p):
        s, ins, st = piece.style, piece.inst, piece.state

        def add(sig, t, pan=0.0, g=1.0):
            piece.add(sig, t, pan, g * MIX)
        B = 60 / s.bpm
        e, k = p.energy, p.key_offset
        calm = e < 0.4 or p.section == "breakdown"
        # pulse 2: the chords as a fast arpeggio; the passing chord takes over on beat 3
        if "chords" in p.layers:
            div = 4 if calm else st["arp_div"]
            step, shape = B / div, st["arp_shape"]
            pools = [self._pool(piece, p.chord.tones, k)]
            if p.passing is not None:
                pools.append(self._pool(piece, p.passing.tones, k))
            duty, soft = DUTY[s.chords], s.chords == "pad"
            g = (0.04 if calm else 0.05) * (1.15 if duty < 0.2 else 1.0)
            for i in range(4 * div):
                pool = pools[-1] if i * step >= 2 * B - 1e-9 else pools[0]
                vel = 1.0 if i % div == 0 else 0.75
                add(ins.arp(pool[shape[i % len(shape)]], step * 0.9, vel, duty, soft), i * step, 0.25, g)
        # triangle: eighths jumping octaves over the planned line (quarters on the root when calm)
        if "bass" in p.layers and p.bass:
            sub = 1 if calm else 2
            for i in range(4 * sub):
                beat = i / sub
                n = ([x for x in p.bass if x.beat <= beat + 1e-9] or [p.bass[0]])[-1]
                approach = abs(n.beat - beat) < 1e-9 and n.length <= 0.5 and n.beat > 0
                m = n.midi if approach or i % 2 == 0 or sub == 1 else n.midi + 12
                add(ins.bass(m, B / sub * 0.85, 1.0), beat * B, 0.0, 0.10)
        # noise channel
        if "drums" in p.layers:
            dg = 0.75 + 0.4 * e
            full = e >= 0.45
            busy = s.drums == "busy" and e >= 0.6
            for kb in (st["kick_beats"] if full else (0,)):
                add(ins.kick(), kb * B, 0, 0.22 * dg)
            if busy:
                add(ins.kick(), 3.5 * B, 0, 0.16 * dg)
            fill = full and s.drums != "soft" and p.section_bar == SECTION_BARS.get(p.section, 0) - 1
            for sb in ((1, 3) if full else (2,)):
                if not (fill and sb == 3):
                    add(ins.snare(), sb * B, 0.1, 0.20 * dg)
            if fill:   # 16th-note snare roll into the next section
                for j in range(4):
                    add(ins.snare(), (3 + j / 4) * B, 0.1, (0.11 + 0.04 * j) * dg)
            steps = 4 if busy else (2 if s.drums != "soft" else 1)
            for i in range(4 * steps):
                add(ins.hat(), i * B / steps, -0.3, (0.06 if i % steps else 0.08) * dg)
        # pulse 1: lead (with a quiet echo when the style has space) and the slower counter-melody
        if "lead" in p.layers:
            duty, soft = DUTY[s.lead if s.lead != "none" else "piano"], s.lead == "pad"
            for n in p.melody:
                sig = ins.lead(n.midi, n.length * B * 0.92, 0.9, duty, soft)
                add(sig, n.beat * B, 0.1, 0.09)
                if s.space > 0.2:
                    add(sig, n.beat * B + 0.75 * B, -0.3, 0.09 * 0.3 * min(1.0, s.space + 0.3))
        if "counter" in p.layers:
            duty = 0.125 if DUTY.get(s.lead, 0.5) != 0.125 else 0.25
            for n in p.counter:
                add(ins.lead(n.midi, n.length * B * 0.9, 0.6, duty, True), n.beat * B, -0.2, 0.04)

    def master(self, s):
        return Master(space=0.15 * s.space, reverb=0.03 * s.space, room=0.05 + 0.25 * s.space, vinyl=0.0, wow=0.0,
                      tone=int(9000 * 2 ** s.brightness), drive=1.6)


GENRE = ChiptuneGenre()
