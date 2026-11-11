import numpy as np
from dataclasses import replace
from gentle_stream.appearance import Preferences
from gentle_stream.visualizer import render_spectrum


def test_styles_and_palettes_really_change_rendering():
    levels = np.array([.2, .5, 1.])
    variants = [render_spectrum(levels, 5, replace(Preferences(), style=s))
                for s in ('gentle', 'segmented', 'dots')]
    assert len({v.plain for v in variants}) == 3
    palettes = [render_spectrum(levels, 5, replace(Preferences(), palette=p))
                for p in ('pink', 'analyzer', 'mono')]
    assert len({str(v.spans) for v in palettes}) == 3
    assert all(len(v.plain.splitlines()) == 5 for v in variants)
