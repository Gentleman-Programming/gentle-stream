"""Electro: placeholder that currently plays the lo-fi behavior.

TODO(G6): the target sound (S6): classic electro (electro-funk). Syncopated 808-style breakbeats (boomy pitched
kick, snappy clap/snare, crisp closed hats, cowbell or rim accents), robotic square/saw synth riffs and 16th-note
arpeggios, a funky syncopated synth bass, short stabs, a tight dry master. Not four-on-the-floor (that is
electrohouse). Only originally composed material.
"""
from __future__ import annotations

from .lofi import LofiGenre


class ElectroGenre(LofiGenre):
    name = "electro"
    description = "808 breakbeats, robotic synth riffs and arpeggios, funky synth bass (not four-on-the-floor)"
    bpm = (100, 130)
    modes = ("minor", "dorian", "major")
    voices = {"piano": "square synth riff", "rhodes": "electric piano", "nylon": "pluck synth",
              "pad": "string synth pad"}


GENRE = ElectroGenre()
