"""Built-in generative styles, not recordings or user-owned saved presets."""
from .genres import NAMES, REGISTRY
from .style import DEFAULT, Style


def default_playlist() -> tuple[Style, ...]:
    """One deterministic style per registered genre, including the actual startup style."""
    return tuple(
        DEFAULT if name == DEFAULT.genre else Style.from_dict({
            "name": name, "genre": name, "mood": REGISTRY[name].description,
            "bpm": sum(REGISTRY[name].bpm) / 2, "mode": REGISTRY[name].modes[0],
        })
        for name in NAMES
    )
