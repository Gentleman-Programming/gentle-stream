"""Soft electronic pulse with restrained synth colours, not a club master."""
from .hiphop import HiphopGenre


class ChillElectronicGenre(HiphopGenre):
    name = "chill_electronic"
    description = "soft electronic instrumental: gentle steady pulse, mellow synth chords and airy melodic textures"
    bpm = (80, 110)
    synth = True
    voices = {"piano": "soft square synth", "rhodes": "FM keys", "nylon": "synth pluck", "pad": "airy string synth"}
    kicks = ((0, 1, 2, 3),)

    drum_gain = 0.6


GENRE = ChillElectronicGenre()
