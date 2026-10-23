"""Genre registry: every genre is one module of this package exposing `GENRE`, a `base.Genre` instance.

`lofi` is the default and the fallback for unknown names. Adding or replacing a genre touches only its own module.
"""
from __future__ import annotations

from . import ambient, chiptune, cumbia, dubstep, electro, electrohouse, jazz, lofi, rock, synthwave
from .base import Genre, Master, Piece

DEFAULT = "lofi"
REGISTRY: dict[str, Genre] = {m.GENRE.name: m.GENRE for m in (lofi, ambient, chiptune, synthwave, jazz, rock, electro, electrohouse, cumbia, dubstep)}
NAMES = tuple(REGISTRY)


def get(name: str) -> Genre:
    """The genre called `name`, or lo-fi."""
    return REGISTRY.get(name, REGISTRY[DEFAULT])


__all__ = ["DEFAULT", "NAMES", "REGISTRY", "Genre", "Master", "Piece", "get"]
