"""Genre registry: every genre is one module of this package exposing `GENRE`, a `base.Genre` instance.

`lofi` is the default and the fallback for unknown names. New plugins are appended to the registry below.
"""
from __future__ import annotations

from . import ambient, chiptune, cumbia, dubstep, electro, electrohouse, jazz, lofi, reggaeton, rock, synthwave
from . import chill_electronic, electro_trap, hiphop, lofi_trap, trap
from .base import Genre, Master, Piece

DEFAULT = "lofi"
REGISTRY: dict[str, Genre] = {
    m.GENRE.name: m.GENRE for m in (
        lofi, ambient, chiptune, synthwave, jazz, rock, electro, electrohouse, cumbia, dubstep,
        hiphop, trap, chill_electronic, lofi_trap, electro_trap, reggaeton,
    )
}
NAMES = tuple(REGISTRY)


def get(name: str) -> Genre:
    """The genre called `name`, or lo-fi."""
    return REGISTRY.get(name, REGISTRY[DEFAULT])


__all__ = ["DEFAULT", "NAMES", "REGISTRY", "Genre", "Master", "Piece", "get"]
