"""Original dembow groove, off-beat chord chops and a plucked-guitar colour.

This is a production-style approximation, not a sampled human performance or a copy of a song. It reuses the
existing electro drum/tonal voices and the existing nylon string model, leaving electro presets unchanged.
"""
from __future__ import annotations

import numpy as np

from .electro import Instruments as ElectroInstruments, SampledVoices as ElectroSamples
from .electro import ElectroGenre
from .lofi import Instruments as StringInstruments

# Two-bar dembow variations on a 16th grid; never a four-on-the-floor kick.
KICKS = (((0, 7, 8, 14), (0, 6, 8, 15)), ((0, 6, 8, 14), (0, 7, 8, 14)))
CHOPS = ((2, 6, 10, 14), (2, 7, 10, 14))
BASS = ((0, 6, 8, 14), (0, 7, 8, 14))


class Instruments(ElectroInstruments):
    def _strings(self):
        if not hasattr(self, '_nylon_strings'):
            self._nylon_strings = StringInstruments(self.rng, self.sr)
        return self._nylon_strings

    def _stab(self, color, m, dur):
        if color == 'nylon':
            return self._strings().nylon(m, dur) * 0.36
        return super()._stab(color, m, dur)

    def _lead(self, color, m, dur):
        if color == 'nylon':
            return self._strings().nylon(m, dur) * 0.36
        return super()._lead(color, m, dur)


class SampledVoices(ElectroSamples):
    # GM Nylon Guitar; the inherited kit and other voices are the same as electro.
    LEAD = ElectroSamples.LEAD | {'nylon': 24}
    STAB = ElectroSamples.STAB | {'nylon': 24}


class ReggaetonGenre(ElectroGenre):
    name = 'reggaeton'
    description = 'original dembow kick/backbeat, off-beat guitar and keys, round syncopated bass'
    bpm = (85, 110)
    modes = ('minor', 'major', 'dorian')
    voices = {'piano': 'short keyboard chops / synth lead', 'rhodes': 'electric piano chops / square lead',
              'nylon': 'plucked nylon guitar chords and lead', 'pad': 'sustained synth pad'}

    def instruments(self, rng, sr, sampled):
        return SampledVoices(sampled, rng, sr) if sampled is not None else Instruments(rng, sr)

    def start(self, piece):
        r = piece.rng
        piece.state.update(kicks=KICKS[int(r.integers(len(KICKS)))],
                           chops=CHOPS[int(r.integers(len(CHOPS)))],
                           bass=BASS[int(r.integers(len(BASS)))])

    def schedule(self, piece, p):
        style, ins, add = piece.style, piece.inst, piece.add
        beat = 60 / style.bpm
        step = beat / 4
        offset = p.key_offset
        # Let the composer's progression and passing chord supply harmony, not a fixed riff.
        sections = [(p.chord, 0, 16 if p.passing is None else 8)]
        if p.passing is not None:
            sections.append((p.passing, 8, 8))
        voicings = []
        for chord, at, duration in sections:
            voices = piece.voice([tone + offset for tone in chord.tones])
            piece.center = float(np.mean(voices))
            voicings.append((at, voices))
            if style.chords == 'pad' or p.section in ('intro', 'breakdown'):
                for i, midi in enumerate(voices):
                    add(ins.pad(midi, duration * step * 0.9, 0.75), at * step, (i - 1) * 0.28, 0.08)

        def chord_at(position):
            return voicings[-1][1] if len(voicings) > 1 and position >= 8 else voicings[0][1]

        if style.chords != 'pad' and p.section not in ('intro', 'breakdown'):
            for position in piece.state['chops']:
                for i, midi in enumerate(chord_at(position)):
                    add(ins.stab(style.chords, midi + 12, step * 1.6, 0.8),
                        position * step + i * 0.004, (i - 1) * 0.28, 0.065)
        # A round short bass follows the composer's root changes with an upbeat pickup.
        if p.section in ('intro', 'breakdown'):
            for note in p.bass:
                add(ins.bass(note.midi, note.length * beat * 0.9, 0.6), note.beat * beat, 0, 0.2)
        else:
            for position in piece.state['bass']:
                note = max((n for n in p.bass if n.beat <= position / 4), key=lambda n: n.beat,
                           default=p.bass[0])
                add(ins.bass(note.midi, step * 1.8, 0.78), position * step, 0, 0.24)
        if 'drums' in p.layers:
            self._drums(piece, p, step)
        if 'lead' in p.layers and style.lead != 'none':
            for note in p.melody:
                add(ins.lead(note.midi, note.length * beat * 0.8, 0.8, style.lead), note.beat * beat, 0.12, 0.18)
        if 'counter' in p.layers:
            for note in p.counter:
                add(ins.stab(style.chords, note.midi, min(note.length * beat, beat / 2), 0.5),
                    note.beat * beat, -0.25, 0.07)

    def _drums(self, piece, p, step):
        style, ins, add = piece.style, piece.inst, piece.add
        level = (0.65 if style.drums == 'soft' else 1.0) * (0.8 + 0.3 * p.energy)
        for position in piece.state['kicks'][p.bar % 2]:
            add(ins.kick(), position * step, 0, 0.35 * level)
        for position in (4, 12):
            add(ins.snare(), position * step, 0.06, 0.18 * level)
            add(ins.clap(), position * step, -0.06, 0.22 * level)
        for position in range(0, 16, 2):
            delay = style.swing * step * 2 if position % 4 else 0
            add(ins.hat(), position * step + delay, -0.2, (0.06 if position % 4 else 0.08) * level)
        if style.drums == 'busy' and p.energy >= 0.65:
            for position in (3, 11):
                add(ins.rim(), position * step, 0.25, 0.08 * level)


GENRE = ReggaetonGenre()
