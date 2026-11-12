"""Curated electro/trap: robotic synth timbres on one halftime groove."""
from .electro import ElectroGenre
from .trap import TrapGenre


class ElectroTrapGenre(TrapGenre):
    name = "electro_trap"
    description = "electro + trap instrumental fusion: robotic synth stabs and lead, halftime snare, hat rolls and 808 bass"
    bpm = (110, 140)
    synth = True
    voices = ElectroGenre.voices


GENRE = ElectroTrapGenre()
