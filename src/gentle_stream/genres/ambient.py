"""Ambient (elegant): placeholder that currently plays the lo-fi behavior.

TODO(G1): the target sound, as described to the listener (S2): "Ambient elegante: Como la música del short del
Lunar Pilot: pads, celesta, sin batería, mucho espacio." Slow evolving pads, celesta / glass lead, no drums, long
reverb and a clean master (no vinyl or tape wow). Only originally composed material.
"""
from __future__ import annotations

from .lofi import LofiGenre


class AmbientGenre(LofiGenre):
    name = "ambient"
    description = "elegant ambient: slow pads, celesta, no drums, a lot of space"
    bpm = (50, 80)
    modes = ("lydian", "major", "dorian", "minor")
    voices = {"piano": "soft piano", "rhodes": "celesta", "nylon": "harp", "pad": "slow evolving pad"}


GENRE = AmbientGenre()
