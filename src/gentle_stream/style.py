"""Musical style: the parameters a mood prompt is translated into.

Every field is bounded so that any input (an LLM answer included) yields a playable style.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace

import numpy as np

KEYS = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
MODES = {
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "major": (0, 2, 4, 5, 7, 9, 11),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
}
CHORD_INSTRUMENTS = ("piano", "rhodes", "nylon", "pad")
LEAD_INSTRUMENTS = CHORD_INSTRUMENTS + ("none",)
DRUMS = ("none", "soft", "lofi", "busy")

# Progressions (scale degrees, 1-based) that sound idiomatic in each mode family; used by `variation`.
PROGRESSIONS = {
    "minor": ((1, 6, 3, 7), (1, 4, 7, 3), (1, 5, 6, 4), (6, 7, 1, 1), (1, 3, 7, 4), (1, 4, 6, 5), (6, 4, 1, 5)),
    "major": ((1, 5, 6, 4), (1, 6, 4, 5), (4, 1, 5, 6), (1, 3, 4, 4), (2, 5, 1, 6), (1, 4, 2, 5)),
    "dorian": ((1, 4, 1, 4), (1, 7, 4, 1), (1, 2, 4, 1), (1, 3, 4, 7)),
    "phrygian": ((1, 2, 1, 7), (1, 2, 3, 2), (1, 7, 6, 2)),
    "lydian": ((1, 2, 1, 2), (1, 2, 5, 1), (1, 3, 2, 1)),
    "mixolydian": ((1, 7, 4, 1), (1, 4, 7, 4), (1, 5, 7, 4)),
}


def _clamp(value, lo, hi, default):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(v):
        return default
    return min(hi, max(lo, v))


def _choice(value, allowed, default):
    if isinstance(value, str):
        v = value.strip().lower()
        for a in allowed:
            if a.lower() == v:
                return a
    return default


@dataclass(frozen=True)
class Style:
    name: str = "Midnight Rain"
    mood: str = ""
    key: str = "D"
    mode: str = "minor"
    bpm: float = 72.0
    progression: tuple[int, ...] = (1, 6, 3, 7)
    chords: str = "piano"
    lead: str = "piano"
    drums: str = "lofi"
    energy: float = 0.5        # loudness of drums/re-hits, hat density
    brightness: float = 0.45   # master low-pass: dark tape (0) .. open (1)
    swing: float = 0.1         # off-beat hat delay as a fraction of a beat
    vinyl: float = 0.5         # crackle amount
    space: float = 0.5         # room size/wetness
    lead_density: float = 0.5  # how often the motif plays

    @classmethod
    def from_dict(cls, data: dict, base: Style | None = None) -> Style:
        """Build a valid style from untrusted data; missing or invalid fields fall back to `base`."""
        b = base or cls()
        if not isinstance(data, dict):
            return b
        prog = data.get("progression", b.progression)
        try:
            prog = tuple(int(x) for x in prog if 1 <= int(x) <= 7)[:8]
        except (TypeError, ValueError):
            prog = ()
        if len(prog) < 2:
            prog = b.progression
        name = data.get("name", b.name)
        name = " ".join(str(name).split())[:40] or b.name
        mood = data.get("mood", b.mood)
        return cls(
            name=name,
            mood=" ".join(str(mood).split())[:200],
            key=_choice(data.get("key"), KEYS, b.key),
            mode=_choice(data.get("mode"), tuple(MODES), b.mode),
            bpm=round(_clamp(data.get("bpm"), 50, 140, b.bpm), 1),
            progression=prog,
            chords=_choice(data.get("chords"), CHORD_INSTRUMENTS, b.chords),
            lead=_choice(data.get("lead"), LEAD_INSTRUMENTS, b.lead),
            drums=_choice(data.get("drums"), DRUMS, b.drums),
            energy=_clamp(data.get("energy"), 0, 1, b.energy),
            brightness=_clamp(data.get("brightness"), 0, 1, b.brightness),
            swing=_clamp(data.get("swing"), 0, 0.3, b.swing),
            vinyl=_clamp(data.get("vinyl"), 0, 1, b.vinyl),
            space=_clamp(data.get("space"), 0, 1, b.space),
            lead_density=_clamp(data.get("lead_density"), 0, 1, b.lead_density),
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["progression"] = list(self.progression)
        return d

    def variation(self, rng: np.random.Generator) -> Style:
        """A new piece with the same mood: nearby key, another idiomatic progression, a small tempo drift,
        and sometimes swapped instruments. Mode, energy and colour stay, so the feeling is kept."""
        key = KEYS[(KEYS.index(self.key) + int(rng.choice([0, 2, -2, 5, -5, 3, -3]))) % 12]
        options = [p for p in PROGRESSIONS[self.mode] if p != self.progression] or [self.progression]
        prog = tuple(options[int(rng.integers(len(options)))])
        chords, lead = self.chords, self.lead
        if rng.random() < 0.5:
            chords = CHORD_INSTRUMENTS[int(rng.integers(len(CHORD_INSTRUMENTS)))]
        if lead != "none" and rng.random() < 0.5:
            lead = CHORD_INSTRUMENTS[int(rng.integers(len(CHORD_INSTRUMENTS)))]
        bpm = float(np.clip(self.bpm + rng.integers(-6, 7), 50, 140))
        return replace(self, key=key, progression=prog, chords=chords, lead=lead, bpm=bpm,
                       swing=float(np.clip(self.swing + rng.uniform(-0.03, 0.03), 0, 0.3)))

    def summary(self) -> str:
        return (f"{self.key} {self.mode} · {self.bpm:g} bpm · "
                f"{'-'.join(str(d) for d in self.progression)} · {self.chords}/{self.lead} · drums {self.drums}")


DEFAULT = Style()
FIELD_NAMES = tuple(f.name for f in fields(Style))
