"""Synthetic sample for the appearance editor; no music or player access."""
import numpy as np
from rich.text import Text

from .appearance import Preferences
from .visualizer import SpectrumMotion, render_spectrum

PREVIEW_LEVELS = np.array([.12, .37, .81, .95, .58, .25, .35, .72, .58, .32, .2, .1])


def appearance_frame(p: Preferences, columns: int) -> Text:
    frame = Text('♪ Example track', style=p.accent)
    if p.show_details:
        frame.append('\nlofi · D minor · 72 bpm')
    if p.show_visualizer:
        rows = min(p.height, 5 if p.compact else 8)
        bands = np.interp(np.linspace(0, 1, p.bands), np.linspace(0, 1, len(PREVIEW_LEVELS)), PREVIEW_LEVELS)
        motion = SpectrumMotion()
        motion.advance(bands, p, 0)
        levels, peaks = motion.advance(bands * .8, p, 1 / p.refresh_hz)
        frame.append('\n').append_text(render_spectrum(levels, rows, p, peaks, columns))
    if p.show_model:
        frame.append('\nmodel: example only')
    if p.show_footer:
        frame.append('\n▶ Playing · piece 1 · bar 4')
    if p.show_input:
        frame.append('\nDescribe a feeling…', style='italic')
    if p.show_controls:
        gap = ' ' * p.icon_gap
        frame.append('\n' + gap.join(('☰', '←', 'Ⅱ', '→', '− 65% +')), style=p.accent)
        actions = ('⇄', '!↺', '▣↓', '⛭') if p.show_input else ('⇄', '!↺', '▣↓', '✎', '⛭')
        frame.append('\n' + gap.join(actions), style=p.accent)
    return frame
