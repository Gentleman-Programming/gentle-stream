import numpy as np
from dataclasses import replace
from gentle_stream.appearance import Preferences
import pytest

from gentle_stream.visualizer import SpectrumMotion, render_spectrum


def test_styles_and_palettes_really_change_rendering():
    levels = np.array([.2, .5, 1.])
    variants = [render_spectrum(levels, 5, replace(Preferences(), style=s))
                for s in ('gentle', 'segmented', 'dots')]
    assert len({v.plain for v in variants}) == 3
    palettes = [render_spectrum(levels, 5, replace(Preferences(), palette=p))
                for p in ('pink', 'analyzer', 'mono')]
    assert len({str(v.spans) for v in palettes}) == 3
    assert all(len(v.plain.splitlines()) == 5 for v in variants)


@pytest.mark.parametrize(('style', 'expected'), [('wave', '  ▁▇\n  ██\n ▆██'), ('mirror', '  ▄█\n  ██\n  ▀█')])
def test_wave_and_mirror_match_approved_silhouettes(style, expected):
    levels = np.array([0., .25, .75, 1.])
    assert render_spectrum(levels, 3, Preferences(style=style)).plain == expected
    assert levels.tolist() == [0., .25, .75, 1.]


def test_motion_retains_peaks_until_the_deadline_without_changing_the_sample():
    motion = SpectrumMotion()
    p = Preferences(smoothing=.5, peak_hold=1)
    sample = np.array([1., .5])
    motion.advance(sample, p, 0)
    levels, peaks = motion.advance(np.zeros(2), p, .5)
    assert levels.tolist() == [.5, .25] and peaks.tolist() == [1., .5]
    levels, peaks = motion.advance(np.zeros(2), p, 1)
    assert levels.tolist() == peaks.tolist() == [.25, .125]
    assert sample.tolist() == [1., .5]
