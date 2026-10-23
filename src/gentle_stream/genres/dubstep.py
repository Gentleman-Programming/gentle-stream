"""Dubstep: placeholder that currently plays the lo-fi behavior.

TODO(G8): the target sound (S6): 140 bpm with a half-time feel (snare on beat 3), a deep sine sub, wobble basses
(saw/square through a low-pass whose cutoff an LFO synced to 1/4-1/16 notes opens and closes), a sparse build-up
and a heavy "drop" on the strong sections, dark pads and a gritty but controlled master. Only originally composed
material.
"""
from __future__ import annotations

from .lofi import LofiGenre


class DubstepGenre(LofiGenre):
    name = "dubstep"
    description = "half-time drums, deep sub, LFO wobble bass, heavy drops"
    bpm = (136, 140)
    modes = ("minor", "phrygian")
    voices = {"piano": "wobble bass", "rhodes": "growl bass", "nylon": "pluck", "pad": "dark pad"}


GENRE = DubstepGenre()
