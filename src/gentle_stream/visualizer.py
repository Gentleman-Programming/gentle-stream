"""UI-thread spectrum rendering, not an audio equalizer."""
from __future__ import annotations

import time
import numpy as np
from rich.text import Text
from textual.widgets import Static

from .appearance import Preferences

BLOCKS = ' ▁▂▃▄▅▆▇█'
PALETTES = {'pink': ('#ff76c3', '#f095c8', '#ffb1dd', '#a78e9b', '#76616b'),
            'analyzer': ('#ff4545', '#ffff55', '#64ef64', '#40d440', '#32b432')}


class SpectrumMotion:
    """Display-only smoothing and timed peaks for live and synthetic samples."""

    def __init__(self):
        self.levels = self.peaks = self.deadlines = None

    def advance(self, sample, preferences: Preferences, now: float):
        levels = np.asarray(sample, dtype=float)
        if self.levels is None or levels.shape != self.levels.shape:
            self.levels = levels.copy()
            self.peaks = levels.copy()
            self.deadlines = np.full(levels.shape, now + preferences.peak_hold)
        else:
            smoothing = preferences.smoothing
            self.levels = smoothing * self.levels + (1 - smoothing) * levels
            if preferences.peak_hold:
                reset = (self.levels >= self.peaks) | (now >= self.deadlines)
                self.peaks[reset] = self.levels[reset]
                self.deadlines[reset] = now + preferences.peak_hold
        return self.levels, self.peaks if preferences.peak_hold else None


def render_spectrum(levels, rows: int, preferences: Preferences, peaks=None, columns: int | None = None) -> Text:
    # Stretch display only, without changing audio sampling.
    style = preferences.style
    if columns is not None:
        slots = max(1, (columns + 1) // 2 if style == 'segmented' else columns)
        if style == 'wave':
            bands = np.arange(len(levels))
            positions = np.linspace(0, len(levels) - 1, slots)
            levels = np.interp(positions, bands, levels)
            peaks = np.interp(positions, bands, peaks) if peaks is not None else None
        else:
            indices = np.arange(slots) * len(levels) // slots
            levels = levels[indices]
            peaks = np.asarray(peaks)[indices] if peaks is not None else None
    if style == 'wave' and len(levels) > 1:
        padded = np.pad(levels, 1, mode='edge')
        levels = (padded[:-2] + 6 * padded[1:-1] + padded[2:]) / 8
    colors = PALETTES.get(preferences.palette, (preferences.accent,))
    lines = []
    for i, r in enumerate(range(rows - 1, -1, -1)):
        line = (_mirror_line(levels, peaks, rows, i) if style == 'mirror'
                else _upright_line(levels, peaks, rows, r, style))
        if columns is not None:
            line = line.ljust(columns)
        lines.append(Text(line, style=colors[min(len(colors) - 1, i * len(colors) // rows)]))
    return Text('\n').join(lines)


def _mirror_line(levels, peaks, rows: int, row: int) -> str:
    # The axis is in the center cell for odd heights and between rows otherwise.
    side = 2 * row - rows + 1
    near = max(0, abs(side) - 1)
    glyphs = ' █' if side == 0 else ' ▄█' if side < 0 else ' ▀█'
    capacity = len(glyphs) - 1
    cells = np.clip(levels * rows - near, 0, capacity).astype(int)
    chars = [glyphs[c] for c in cells]
    if peaks is not None:
        depths = np.clip(np.asarray(peaks) * rows, 0, rows).astype(int)
        for j in np.flatnonzero((near < depths) & (depths <= near + capacity)):
            chars[j] = '─'
    return ''.join(chars)


def _upright_line(levels, peaks, rows: int, r: int, style: str) -> str:
    cells = np.clip(levels * rows * 8 - r * 8, 0, 8).astype(int)
    if style in ('gentle', 'wave'):
        line = ''.join(BLOCKS[c] for c in cells)
    elif style == 'segmented':
        line = ' '.join('▪' if c else ' ' for c in cells)
    else:
        line = ''.join('•' if c else ' ' for c in cells)
    if peaks is not None:
        chars = list(line)
        stride = 2 if style == 'segmented' else 1
        for j, peak in enumerate(peaks):
            if peak > 0 and min(rows - 1, int(peak * rows)) == r:
                chars[j * stride] = '─'
        line = ''.join(chars)
    return line


class Spectrum(Static):
    MIN_ROWS = 5

    def __init__(self, player, **kwargs):
        super().__init__(**kwargs)
        self.player = player
        self.preferences = Preferences()
        self.motion = SpectrumMotion()

    def configure(self, preferences: Preferences) -> None:
        self.preferences = preferences
        self.motion = SpectrumMotion()

    def tick(self) -> None:
        p = self.preferences
        width = max(1, self.size.width or 48)
        count = min(p.bands, max(1, (width + 1) // 2 if p.style == 'segmented' else width))
        levels = np.clip(self.player.spectrum(count), 0, 1)
        # Still poll while hidden: background clients refresh their audible metadata here.
        if not self.display:
            return
        levels, peaks = self.motion.advance(levels, p, time.monotonic())
        self.update(render_spectrum(levels, max(p.height, self.size.height or p.height), p, peaks, width))
