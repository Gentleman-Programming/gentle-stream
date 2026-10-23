"""Chiptune / 8-bit: placeholder that currently plays the lo-fi behavior.

TODO(G2): the target sound, as described to the listener (S2): "Chiptune / 8-bit: Ondas cuadradas y
triangulares, arpegios rápidos y batería de ruido: música de juego retro original." Square/pulse leads, triangle
bass, fast arpeggiated chords, noise drums, a dry clean master (no vinyl, no tape wow). Original retro-game feel
only: never the melody of an existing game.
"""
from __future__ import annotations

from .lofi import LofiGenre


class ChiptuneGenre(LofiGenre):
    name = "chiptune"
    description = "8-bit: square and triangle waves, fast arpeggios, noise drums; original retro-game music"
    bpm = (100, 140)
    modes = ("major", "minor", "mixolydian", "dorian")
    voices = {"piano": "square lead", "rhodes": "25% pulse", "nylon": "fast triangle arpeggio",
              "pad": "soft pulse chords"}


GENRE = ChiptuneGenre()
