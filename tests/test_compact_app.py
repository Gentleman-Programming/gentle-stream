from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

import numpy as np
import pytest
from textual.widgets import Button, Footer, Header, Input, Tooltip

from gentle_stream.appearance import Preferences, PreferenceStore
from gentle_stream.client import PlayerError
from gentle_stream.compact_app import CompactStreamApp
from gentle_stream.visualizer import Spectrum


@pytest.fixture
def compact():
    player = Mock(playing=True, volume=.8)
    player.spectrum.side_effect = lambda n: np.zeros(n)
    player.toggle.side_effect = lambda: setattr(player, 'playing', not player.playing)
    player.set_volume.side_effect = lambda v: setattr(player, 'volume', max(0, min(1, v)))
    store = Mock(error=None)
    store.load.return_value = replace(Preferences(), height=30, show_controls=False, show_visualizer=False)
    return CompactStreamApp(player, preference_store=store), player, store


@pytest.mark.asyncio
@pytest.mark.parametrize('theme,background', [('black', '#000000'), ('charcoal', '#202020'),
                                             ('gentle', '#123456')])
async def test_saved_colors_and_spectrum_survive_compact_without_writes(compact, theme, background):
    _, player, _ = compact
    preferences = replace(Preferences(), theme=theme, background='#123456', accent='#abcdef',
                          style='segmented', palette='mono', bands=16, smoothing=.4, peak_hold=.5,
                          height=30, show_visualizer=False, show_controls=False)
    with TemporaryDirectory(prefix='gentle-compact-preferences-') as scratch:
        path = Path(scratch) / 'appearance.json'
        store = PreferenceStore(path)
        store.save(preferences)
        before = path.read_bytes()
        app = CompactStreamApp(player, preference_store=store)
        async with app.run_test(size=(40, 8)) as pilot:
            await pilot.pause()
            assert app.current_theme.background == background
            assert app.current_theme.primary == preferences.accent
            assert app.current_theme.accent == preferences.accent
            for button in app.query(Button):
                assert button.styles.color.hex.lower() == preferences.accent
            spectrum = app.query_one(Spectrum)
            assert spectrum.preferences == replace(preferences, height=7)
            assert spectrum.display and app.query_one('#controls').display
            assert path.read_bytes() == before
        assert path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('selector,expected', [
    ('#previous', 'Previous playlist song (wraps); select a playlist in full UI first'),
    ('#play', 'Play / pause (Space)'),
    ('#next', 'Next piece (n); next playlist song when continuous'),
    ('#quieter', 'Volume down 5% (-); Down: 1%'),
    ('#louder', 'Volume up 5% (+); Up: 1%'),
    ('#volume', 'Volume 80% · +/-: 5%; Up/Down: 1%'),
])
async def test_delayed_hover_displays_truthful_shortcuts(compact, selector, expected):
    app, player, store = compact
    # Textual headless tests suppress tooltips unless explicitly enabled.
    async with app.run_test(size=(80, 24), tooltips=True) as pilot:
        await pilot.hover('#spectrum')
        await pilot.pause()
        assert not any(tooltip.display for tooltip in app.screen.query(Tooltip))
        await pilot.hover(selector)
        await pilot.pause(1.2)
        visible = [tooltip for tooltip in app.screen.query(Tooltip) if tooltip.display]
        assert len(visible) == 1
        assert str(visible[0].render()) == expected
        assert app.screen.region.contains_region(visible[0].region)
        player.previous.assert_not_called()
        player.toggle.assert_not_called()
        player.next.assert_not_called()
        player.set_volume.assert_not_called()
    store.save.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('size', [(24, 3), (40, 8), (80, 24)])
async def test_compact_layout_and_preferences(compact, size):
    app, p, store = compact
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        assert not app.query(Header) and not app.query(Footer) and not app.query(Input)
        assert [b.id for b in app.query(Button)] == ['previous', 'play', 'next', 'quieter', 'louder']
        spectrum = app.query_one(Spectrum)
        assert spectrum.region.height == size[1] - 1
        assert len(spectrum.render().plain.splitlines()) <= size[1] - 1
        row = app.query_one('#controls').region
        for widget in [*app.query(Button), app.query_one('#volume')]:
            assert row.contains_region(widget.region)
            assert widget.region.height == 1
        assert all(b.active_effect_duration == 0 for b in app.query(Button))
        assert 'Space' in str(app.query_one('#play').tooltip)
        assert '(n)' in str(app.query_one('#next').tooltip)
        assert 'Down' in str(app.query_one('#quieter').tooltip)
        assert 'Up' in str(app.query_one('#louder').tooltip)
        assert 'shortcut' not in str(app.query_one('#previous').tooltip)
        await pilot.press('f2', 'm', 'slash')
        assert not app.query(Input)
    store.save.assert_not_called()
    p.set_style.assert_not_called()


@pytest.mark.asyncio
async def test_controls_repeated_clicks_keys_boundaries_and_rejection(compact):
    app, p, store = compact
    async with app.run_test(size=(32, 6)) as pilot:
        for button, method in [('previous', p.previous), ('play', p.toggle), ('next', p.next)]:
            for _ in range(2):
                assert await pilot.click('#' + button)
            assert method.call_count == 2
        await pilot.press('space', 'n')
        assert p.toggle.call_count == 3 and p.next.call_count == 3
        for key, value in [('plus', .85), ('minus', .8), ('up', .81), ('down', .8)]:
            await pilot.press(key)
            assert p.volume == value
        for initial, button, expected in [(1, 'louder', 1), (0, 'quieter', 0)]:
            p.volume = initial
            await pilot.click('#' + button)
            assert p.volume == expected
        app.notify = Mock(wraps=app.notify)
        p.previous.side_effect = PlayerError('Select a playlist first [missing]')
        before = (p.playing, p.volume, p.next.call_count)
        await pilot.click('#previous')
        await pilot.pause()
        app.notify.assert_called_with('Could not control playback: Select a playlist first [missing]', severity='error')
        assert (p.playing, p.volume, p.next.call_count) == before
        assert app.is_running
    store.save.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('method,key,button', [('toggle', 'space', 'play'), ('next', 'n', 'next'),
                                              ('set_volume', 'minus', 'quieter')])
@pytest.mark.parametrize('via', ['click', 'keyboard'])
async def test_rejected_controls_notify_without_mutation(compact, method, key, button, via):
    app, p, store = compact
    getattr(p, method).side_effect = PlayerError('unavailable [busy]')
    app.notify = Mock(wraps=app.notify)
    async with app.run_test() as pilot:
        if via == 'click':
            assert await pilot.click('#' + button)
        else:
            await pilot.press(key)
        app.notify.assert_called_with('Could not control playback: unavailable [busy]', severity='error')
        assert app.is_running and p.playing and p.volume == .8
        p.close.assert_not_called()
        p.set_style.assert_not_called()
    store.save.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('key,detach', [('b', True), ('q', False), ('disconnect', False)])
async def test_lifecycle(compact, key, detach):
    app, p, store = compact
    async with app.run_test() as pilot:
        if key == 'disconnect':
            p.spectrum.side_effect = ConnectionError('gone')
            await pilot.pause(.3)
        else:
            await pilot.press(key)
        assert not app.is_running
    assert p.detach.call_count == int(detach)
    assert p.close.call_count == int(not detach)
    store.save.assert_not_called()
