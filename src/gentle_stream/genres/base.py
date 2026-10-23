"""The genre plugin interface.

The composer (`composer.Composer`) decides WHAT plays in every bar: song form, harmony, melody, counter-melody and
bass line, shared by every genre. A `Genre` decides how it sounds: which voices play each part (procedural, or
FluidR3_GM programs and drum kits when the samples backend is available), the rhythmic treatment of the chords
(sustained, comped, arpeggiated, strummed...), the drum pattern for each section's energy, and the master chain.
It also describes itself to the language model (description, tempo and mode hints, what the four shared
`Style.chords`/`Style.lead` names mean in it).

A genre is a stateless singleton; everything chosen for one piece lives on its `Piece`. The engine calls, per piece,
`instruments` then `start`, and per bar `schedule` then `master`. Output must be deterministic per seed: draw
randomness only from `piece.rng` (the audio generator, also used by the master chain) and never from a global one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from ..composer import BarPlan, Chord
    from ..samples import SampleInstruments
    from ..style import Style


@dataclass(frozen=True)
class Master:
    """Master-chain settings for a bar. Changes between bars are smoothed by the engine (no clicks)."""

    space: float      # 0-1: size of the convolution reverb's impulse response
    reverb: float     # convolution reverb send gain
    room: float       # early-reflection room gain
    vinyl: float      # 0-1: vinyl crackle and hiss (0 = clean)
    wow: float        # 0-1: tape wow and flutter depth (0 = steady pitch, 1 = lo-fi tape)
    tone: int         # master low-pass cutoff in Hz (200-20000)
    drive: float      # final tape saturation drive (the output never exceeds 0.9)
    threshold: float = -20.0   # bus compressor threshold, dB
    ratio: float = 2.5         # bus compressor ratio


class Piece:
    """One piece as a genre sees it: the style, the voices, the audio generator and the scheduling surface."""

    def __init__(self, style: Style, rng: np.random.Generator, inst: Any, ton: int, sr: int, acc: np.ndarray):
        self.style, self.rng, self.inst, self.ton, self.sr = style, rng, inst, ton, sr
        self.center = ton + 19          # mean pitch of the last chord voicing (see `voice`)
        self.state: dict[str, Any] = {}  # the genre's own per-piece choices (patterns, voicing...), set in `start`
        self._acc = acc

    def add(self, sig: np.ndarray, t: float, pan: float = 0.0, g: float = 1.0) -> None:
        """Mix mono `sig` at `t` seconds from the start of the current bar; its tail rings into later bars."""
        i = int(t * self.sr)
        if i < 0 or i >= len(self._acc):
            return
        j = min(len(self._acc), i + len(sig)); s = sig[:j - i] * g
        self._acc[i:j, 0] += s * np.sqrt(0.5 * (1 - pan))
        self._acc[i:j, 1] += s * np.sqrt(0.5 * (1 + pan))

    def voice(self, iv: list[int]) -> list[int]:
        """Upper chord tones (no root) placed closest to the previous voicing: smooth voice leading."""
        out = []
        for x in iv[1:]:
            m = self.ton + 12 + x
            while m - self.center > 6:
                m -= 12
            while self.center - m > 6:
                m += 12
            out.append(m)
        return sorted(set(out))


class Genre:
    """Base class for genres. Subclasses set the class attributes and implement `instruments`, `schedule` and
    `master`; `start` and `chord` are optional hooks."""

    name: str = ""
    description: str = ""                       # one line, shown to the language model
    bpm: tuple[int, int] = (60, 90)             # typical tempo range (inside Style's 50-140)
    modes: tuple[str, ...] = ("minor",)         # modes that suit the genre, most typical first
    voices: dict[str, str] = {}                 # Style.chords/lead name -> what it sounds like in this genre

    def instruments(self, rng: np.random.Generator, sr: int, sampled: SampleInstruments | None) -> Any:
        """The voices for a new piece: any object `schedule` knows how to play. `sampled` is the engine's shared
        FluidR3_GM instrument set (its `play`/`hit` render any GM program or drum kit), or None (procedural)."""
        raise NotImplementedError

    def start(self, piece: Piece) -> None:
        """Per-piece choices (drum pattern, arpeggio shape...) into `piece.state`, drawn from `piece.rng`."""

    def chord(self, chord: Chord, scale: tuple[int, ...]) -> Chord:
        """Harmony hook: reshape each diatonic chord the composer builds (e.g. add extensions). The melody and
        counter-melody snap to the first four tones, so prefer voicing changes in `schedule`. Must not use
        randomness."""
        return chord

    def schedule(self, piece: Piece, plan: BarPlan) -> None:
        """Schedule one bar with `piece.add`: chords, bass, drums (when "drums" in plan.layers), lead (when
        "lead" in plan.layers) and counter (when "counter" in plan.layers). Times are seconds from the bar start;
        a beat is 60 / piece.style.bpm seconds. Melody, counter and bass notes are MIDI numbers that already
        include plan.key_offset; chord tones are semitones above `piece.ton` and do not (add it, then `voice`)."""
        raise NotImplementedError

    def master(self, style: Style) -> Master:
        """Master-chain settings for `style` (called every bar; keep it cheap and deterministic)."""
        raise NotImplementedError
