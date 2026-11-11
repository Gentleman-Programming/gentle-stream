"""Halftime trap: tuned synthesized 808 bass and rapid hat rolls."""
from .hiphop import HiphopGenre


class TrapGenre(HiphopGenre):
    name = "trap"
    description = "instrumental trap, no vocals: halftime snare, rapid hi-hat rolls and deep tuned 808 bass"
    bpm = (110, 140)
    modes = ("minor", "phrygian", "dorian")
    sub808 = True
    halftime = True
    kicks = ((0, 1.75, 3), (0, 0.75, 2.75, 3.5))


GENRE = TrapGenre()
