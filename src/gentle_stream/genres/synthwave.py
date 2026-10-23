"""Synthwave: placeholder that currently plays the lo-fi behavior.

TODO(G3): the target sound, as described to the listener (S2): "Synthwave: Sintes analógicos ochentosos, bajo en
octavas, batería con caja grande y reverb." Detuned saw pads and leads, octave-pumping bass, gated big snare with
reverb, bright master without vinyl. Only originally composed material.
"""
from __future__ import annotations

from .lofi import LofiGenre


class SynthwaveGenre(LofiGenre):
    name = "synthwave"
    description = "80s analog synths, octave bass, big reverbed snare"
    bpm = (80, 118)
    modes = ("minor", "dorian", "phrygian", "mixolydian")
    voices = {"piano": "bright synth keys", "rhodes": "FM electric piano", "nylon": "plucked synth arpeggio",
              "pad": "detuned saw pad"}


GENRE = SynthwaveGenre()
