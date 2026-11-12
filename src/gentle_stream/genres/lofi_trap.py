"""Curated lo-fi/trap: warm keys and tape over one trap arrangement."""
from .trap import TrapGenre


class LofiTrapGenre(TrapGenre):
    name = "lofi_trap"
    description = "lo-fi + trap instrumental fusion: warm dusty keys and tape, halftime drums, hat rolls and 808 bass"
    bpm = (90, 120)
    warmth = 0.7


GENRE = LofiTrapGenre()
