"""Pure spectrum renderer ported from the desktop visualizer (no player or widget)."""
from __future__ import annotations

import numpy as np
from rich.text import Text

from .appearance import Preferences

BLOCKS = ' ▁▂▃▄▅▆▇█'
PALETTES = {'pink': ('#ff76c3', '#f095c8', '#ffb1dd', '#a78e9b', '#76616b'),
            'analyzer': ('#ff4545', '#ffff55', '#64ef64', '#40d440', '#32b432')}


def render_spectrum(levels, rows: int, preferences: Preferences, peaks=None, columns: int | None = None) -> Text:
    # Bands control the audio sample count; stretch their display across wide terminals.
    style = preferences.style
    if columns is not None:
        slots = max(1, (columns + 1) // 2 if style == 'segmented' else columns)
        if style == 'wave':
            # Interpolate display columns only, never the audio or its sampling rate.
            bands = np.arange(len(levels))
            positions = np.linspace(0, len(levels) - 1, slots)
            levels = np.interp(positions, bands, levels)
            peaks = np.interp(positions, bands, peaks) if peaks is not None else None
        else:
            indices = np.arange(slots) * len(levels) // slots
            levels = levels[indices]
            peaks = np.asarray(peaks)[indices] if peaks is not None else None
    if style == 'wave' and len(levels) > 1:
        # Soften the silhouette even in a narrow view with one band per column.
        # Edge replication also preserves flat input, including all-zero silence.
        padded = np.pad(levels, 1, mode='edge')
        levels = (padded[:-2] + 6 * padded[1:-1] + padded[2:]) / 8
    colors = PALETTES.get(preferences.palette, (preferences.accent,))
    lines = []
    for i, r in enumerate(range(rows - 1, -1, -1)):
        if style == 'mirror':
            line = _mirror_line(levels, peaks, rows, i)
        else:
            line = _upright_line(levels, peaks, rows, r, style)
        if columns is not None:
            line = line.ljust(columns)
        lines.append(Text(line, style=colors[min(len(colors) - 1, i * len(colors) // rows)]))
    return Text('\n').join(lines)


def _mirror_line(levels, peaks, rows: int, row: int) -> str:
    # Measure the near edge in half-cells from the axis. An odd height has a
    # shared center cell; otherwise the axis is between two rows.
    side = 2 * row - rows + 1
    near = max(0, abs(side) - 1)
    if side == 0:
        glyphs = ' █'
    elif side < 0:
        glyphs = ' ▄█'
    else:
        glyphs = ' ▀█'
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
