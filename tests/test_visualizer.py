from dataclasses import replace
from unittest.mock import Mock

import numpy as np
import pytest
from textual.app import App

from gentle_stream.appearance import Preferences
from gentle_stream.visualizer import Spectrum, render_spectrum


def test_styles_and_palettes_really_change_rendering():
    levels = np.array([.2, .5, 1.])
    variants = [render_spectrum(levels, 5, replace(Preferences(), style=s))
                for s in ('gentle', 'segmented', 'dots')]
    assert len({v.plain for v in variants}) == 3
    palettes = [render_spectrum(levels, 5, replace(Preferences(), palette=p))
                for p in ('pink', 'analyzer', 'mono')]
    assert len({str(v.spans) for v in palettes}) == 3
    assert all(len(v.plain.splitlines()) == 5 for v in variants)


@pytest.mark.asyncio
async def test_spectrum_cache_preserves_previous_render_and_one_poll():
    levels = np.linspace(.2, 1., 48)
    player = Mock()
    player.spectrum.return_value = levels

    class SpectrumApp(App):
        def compose(self):
            yield Spectrum(player)

    app = SpectrumApp()
    async with app.run_test(size=(48, 5)):
        spectrum = app.query_one(Spectrum)
        spectrum.tick()
        player.spectrum.assert_called_once_with(48)
        assert str(spectrum.render()) == str(render_spectrum(levels, 5, Preferences(), columns=48))
        cached = spectrum.cached_sample
        assert cached is not None
        cached[:] = 0
        np.testing.assert_array_equal(spectrum.cached_sample, levels)
        spectrum.display = False
        spectrum.tick()
        assert player.spectrum.call_count == 2
        np.testing.assert_array_equal(spectrum.cached_sample, levels)
