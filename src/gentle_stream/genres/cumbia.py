"""Cumbia: placeholder that currently plays the lo-fi behavior.

TODO(G7): the target sound (S6): the cumbia groove. Guacharaca/güira scraping a steady "ch-chik" eighth pattern,
the tambora/bass drum and congas with the characteristic syncopation, a bass alternating root and fifth on beats 1
and 3, off-beat keyboard chords, and an accordion (or a digital-cumbia synth) lead. Warm, danceable master.
Only originally composed material.
"""
from __future__ import annotations

from .lofi import LofiGenre


class CumbiaGenre(LofiGenre):
    name = "cumbia"
    description = "guacharaca scrape, tambora and congas, root-fifth bass, accordion lead"
    bpm = (85, 110)
    modes = ("minor", "major")
    voices = {"piano": "off-beat keyboard", "rhodes": "organ", "nylon": "guitar", "pad": "accordion"}


GENRE = CumbiaGenre()
