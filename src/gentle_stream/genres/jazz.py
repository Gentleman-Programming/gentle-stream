"""Jazz: placeholder that currently plays the lo-fi behavior.

TODO(G4): the target sound, as described to the listener (S2): "Jazz: Acordes extendidos, walking bass, batería
con escobillas y swing marcado." Extended chords (the `chord` hook), comping, walking bass, brushed drums with a
strong swing, a warm clean master. Only originally composed material.
"""
from __future__ import annotations

from .lofi import LofiGenre


class JazzGenre(LofiGenre):
    name = "jazz"
    description = "extended chords, walking bass, brushed drums, strong swing"
    bpm = (70, 140)
    modes = ("dorian", "major", "mixolydian", "minor")
    voices = {"piano": "acoustic piano comping", "rhodes": "rhodes", "nylon": "jazz guitar",
              "pad": "vibraphone"}


GENRE = JazzGenre()
