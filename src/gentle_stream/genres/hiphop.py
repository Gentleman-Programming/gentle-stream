"""Instrumental boom-bap and the shared single-composition beat arrangement.

Only the new beat family uses this scheduler; existing genres are unchanged.
Voices adapt the existing warm keys and electro kit on either engine backend.
"""
from __future__ import annotations

import numpy as np

from ..synth import SR, mtof
from .base import Genre, Master
from .electro import Instruments as Electronic, SampledVoices
from .lofi import Instruments as Keys


class BeatVoices:
    def __init__(self, rng, sr=SR, sampled=None, *, synth=False, sub808=False):
        self.sr, self.synth, self.sub808 = sr, synth, sub808
        self.keys = sampled if sampled is not None else Keys(rng, sr)
        self.electronic = SampledVoices(sampled, rng, sr) if sampled is not None else Electronic(rng, sr)

    def stab(self, color, midi, dur, vel=1.0):
        if self.synth:
            return self.electronic.stab(color, midi, dur, vel)
        return getattr(self.keys, color)(midi, dur, vel)

    def lead(self, midi, dur, vel=1.0, color="piano"):
        if self.synth:
            return self.electronic.lead(midi, dur, vel, color)
        return getattr(self.keys, color)(midi, dur, vel)

    def bass(self, midi, dur):
        if not self.sub808:
            return self.keys.sub(midi, dur)
        # A tuned 808-style sine body, not an unpitched kick or a second bass track.
        # Keep it synthesized on both backends: no downloaded sample is required.
        n = int(dur * self.sr)
        t = np.arange(n) / self.sr
        frequency = mtof(midi) * (1 + 0.65 * np.exp(-t * 45))
        phase = 2 * np.pi * np.cumsum(frequency) / self.sr
        body = np.sin(phase) + 0.16 * np.sin(2 * phase)
        env = np.minimum(1, t / 0.004) * np.exp(-t * 0.9) * np.clip((dur - t) / 0.06, 0, 1)
        return np.tanh(1.5 * body) * env * 0.8

    def kick(self):
        return self.electronic.kick()

    def snare(self):
        return self.electronic.snare()

    def hat(self):
        return self.electronic.hat()


class HiphopGenre(Genre):
    name = "hiphop"
    description = "instrumental rap / hip-hop beats, no vocals: boom-bap kick, snare backbeat and warm keys"
    bpm = (78, 100)
    modes = ("minor", "dorian", "major")
    voices = {"piano": "warm piano", "rhodes": "electric keys", "nylon": "muted guitar", "pad": "warm pad"}
    synth = False
    sub808 = False
    halftime = False
    drum_gain = 1.0
    warmth = 0.0
    kicks = ((0, 1.5, 2.5), (0, 1.75, 2.5, 3.5))

    def instruments(self, rng, sr, sampled):
        return BeatVoices(rng, sr, sampled, synth=self.synth, sub808=self.sub808)

    def start(self, piece):
        piece.state["kicks"] = self.kicks[int(piece.rng.integers(len(self.kicks)))]

    def schedule(self, piece, p):
        s, ins, add = piece.style, piece.inst, piece.add
        beat = 60 / s.bpm
        # One Composer supplies all harmony, bass and melody, even for fusions.
        segments = [(p.chord, 0, 4 if p.passing is None else 2)]
        if p.passing is not None:
            segments.append((p.passing, 2, 2))
        for chord, at, length in segments:
            notes = piece.voice([x + p.key_offset for x in chord.tones])
            piece.center = float(np.mean(notes))
            duration = length * beat * (0.7 if self.synth and s.chords != "pad" else 0.96)
            for i, midi in enumerate(notes):
                add(ins.stab(s.chords, midi, duration, 0.75), at * beat + i * 0.008,
                    (i - (len(notes) - 1) / 2) * 0.25, 0.10)
        for note in p.bass:
            add(ins.bass(note.midi, note.length * beat * 0.95), note.beat * beat, 0, 0.30)
        if "drums" in p.layers and s.drums != "none":
            self._drums(piece, p, beat)
        if "lead" in p.layers and s.lead != "none":
            for note in p.melody:
                add(ins.lead(note.midi, note.length * beat * 0.9, 0.8, s.lead), note.beat * beat, 0.12, 0.18)
        if "counter" in p.layers and s.lead != "none":
            for note in p.counter:
                add(ins.stab(s.chords, note.midi, note.length * beat * 0.9, 0.6), note.beat * beat, -0.25, 0.07)

    def _drums(self, piece, p, beat):
        s, ins, add = piece.style, piece.inst, piece.add
        soft = s.drums == "soft"
        gain = self.drum_gain * (0.65 + 0.35 * p.energy) * (0.55 if soft else 1)
        kicks = (0, 2) if soft else piece.state["kicks"]
        for at in kicks:
            add(ins.kick(), at * beat, 0, 0.34 * gain)
        for at in ((2,) if self.halftime else (1, 3)):
            add(ins.snare(), at * beat, 0.05, 0.23 * gain)
        steps = 1 if soft else 4 if s.drums == "busy" and p.energy >= 0.6 else 2
        hats = [j / steps for j in range(4 * steps)]
        # Replace the final half-beat by a short 32nd roll; do not double a hit.
        if self.halftime and not soft and p.energy >= 0.45:
            hats = [t for t in hats if t < 3.5] + [3.5, 3.625, 3.75, 3.875]
        for at in hats:
            # Keep trap rolls straight; swing only ordinary off-beat hats.
            delay = s.swing * 0.5 if not self.halftime and at % 1 == 0.5 else 0
            add(ins.hat(), (at + delay) * beat, -0.25, (0.035 if soft else 0.055) * gain)

    def master(self, s):
        return Master(space=s.space * 0.5, reverb=0.04 + 0.10 * s.space, room=0.12 + 0.3 * s.space,
                      vinyl=s.vinyl * self.warmth, wow=self.warmth,
                      tone=int((4200 if self.warmth else 6500) * 2 ** s.brightness), drive=1.7)


GENRE = HiphopGenre()
