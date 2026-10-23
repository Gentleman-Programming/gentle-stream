"""Electro house: placeholder that currently plays the lo-fi behavior.

TODO(G9): the target sound (S6): electro house. A punchy kick on every beat with off-beat open hats and a clap on 2
and 4, big detuned and distorted "dirty" bass riffs, sidechain pumping on pads and chords, white-noise risers,
snare rolls and filter sweeps into the drops on the strong sections, a loud wide master. Only originally composed
material.
"""
from __future__ import annotations

from .lofi import LofiGenre


class ElectroHouseGenre(LofiGenre):
    name = "electrohouse"
    description = "four-on-the-floor kick, dirty distorted bass riffs, sidechain pumping, builds and drops"
    bpm = (124, 132)
    modes = ("minor", "phrygian", "dorian")
    voices = {"piano": "house piano stabs", "rhodes": "electric piano", "nylon": "pluck synth",
              "pad": "supersaw pad"}


GENRE = ElectroHouseGenre()
