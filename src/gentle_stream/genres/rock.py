"""Rock / metal: placeholder that currently plays the lo-fi behavior.

TODO(G5): the target sound (S2): "Rock/metal: power chords, palm mutes, distorted guitar, driving drums (double kick
for metal)." Sampled distorted guitar from FluidR3_GM (GM 30 Distortion Guitar / 29 Overdriven Guitar), sounding
like well-produced MIDI rock rather than a recorded band; procedural fallback with `synth.distort`.

One genre name covers both: rock by default; `Style.drums == "busy"` with `Style.energy >= 0.7` is the metal
flavor (double kick, tighter palm mutes, more drive). This keeps the LLM schema and presets unchanged (no separate
"metal" value). Only originally composed material.
"""
from __future__ import annotations

from .lofi import LofiGenre


class RockGenre(LofiGenre):
    name = "rock"
    description = ("rock or metal: distorted guitar power chords, palm mutes, driving drums "
                   "(busy drums with energy 0.7+ is metal, with double kick)")
    bpm = (90, 140)
    modes = ("minor", "phrygian", "mixolydian", "dorian")
    voices = {"piano": "distorted guitar", "rhodes": "overdriven guitar", "nylon": "clean electric guitar",
              "pad": "string pad"}


GENRE = RockGenre()
