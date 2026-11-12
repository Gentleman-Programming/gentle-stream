"""Display-only segmented volume gauge for the Textual command strip."""

from rich.text import Text


_GAUGE_CELLS = 8


def render_volume_meter(percent: int, accent: str) -> Text:
    """Render eight segments and the exact percentage, without changing audio."""
    percent = max(0, min(100, round(percent)))
    filled = round(percent * _GAUGE_CELLS / 100)
    meter = Text("▰" * filled + "▱" * (_GAUGE_CELLS - filled) + f" {percent}%",
                 style=accent, no_wrap=True)
    meter.stylize("dim", filled, _GAUGE_CELLS)
    meter.stylize("white", _GAUGE_CELLS + 1, len(meter.plain))
    return meter


def render_compact_volume_meter(percent: int, accent: str) -> Text:
    """Use the same single-row gauge on compact screens."""
    return render_volume_meter(percent, accent)
