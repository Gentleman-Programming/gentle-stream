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


def render_spectrum(levels, rows: int, preferences: Preferences, peaks=None, columns: int | None = None) -> Text:
    # Bands control the audio sample count; stretch their display across wide terminals.
    if columns is not None:
        slots = max(1, (columns + 1) // 2 if preferences.style == 'segmented' else columns)
        indices = np.arange(slots) * len(levels) // slots
        levels = levels[indices]
        peaks = np.asarray(peaks)[indices] if peaks is not None else None
    colors = PALETTES.get(preferences.palette, (preferences.accent,))
    lines = []
    for i, r in enumerate(range(rows - 1, -1, -1)):
        cells = np.clip(levels * rows * 8 - r * 8, 0, 8).astype(int)
        if preferences.style == 'gentle':
            line = ''.join(BLOCKS[c] for c in cells)
        elif preferences.style == 'segmented':
            line = ' '.join('▪' if c else ' ' for c in cells)
        else:
            line = ''.join('•' if c else ' ' for c in cells)
        if peaks is not None:
            chars = list(line)
            stride = 2 if preferences.style == 'segmented' else 1
            for j, peak in enumerate(peaks):
                if peak > 0 and min(rows - 1, int(peak * rows)) == r:
                    chars[j * stride] = '─'
            line = ''.join(chars)
        if columns is not None:
            line = line.ljust(columns)
        lines.append(Text(line, style=colors[min(len(colors) - 1, i * len(colors) // rows)]))
    return Text('\n').join(lines)


class Spectrum(Static):
    MIN_ROWS = 5

    def __init__(self, player, **kwargs):
        super().__init__(**kwargs)
        self.player = player
        self.preferences = Preferences()
        self._levels = self._peaks = self._deadlines = None

    def configure(self, preferences: Preferences) -> None:
        self.preferences = preferences
        self._levels = self._peaks = self._deadlines = None

    def tick(self) -> None:
        p = self.preferences
        width = max(1, self.size.width or 48)
        count = min(p.bands, max(1, (width + 1) // 2 if p.style == 'segmented' else width))
        levels = np.clip(self.player.spectrum(count), 0, 1)
        # Still poll while hidden: background clients refresh their audible metadata here.
        if not self.display:
            return
        if self._levels is None or len(self._levels) != count:
            self._levels = levels.copy()
            self._peaks = levels.copy()
            self._deadlines = np.zeros(count)
        self._levels = p.smoothing * self._levels + (1 - p.smoothing) * levels
        now = time.monotonic()
        reset = (self._levels >= self._peaks) | (now >= self._deadlines)
        self._peaks[reset] = self._levels[reset]
        self._deadlines[reset] = now + p.peak_hold
        self.update(render_spectrum(self._levels, max(p.height, self.size.height or p.height), p,
                                    self._peaks if p.peak_hold else None, width))
