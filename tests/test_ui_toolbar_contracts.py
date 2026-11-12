"""Public Textual flows; no audio devices, generators, or live player."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from textual.widgets import Button, Input, OptionList, Select

from gentle_stream.app import ModelPicker, PresetPicker, SaveDialog, StreamApp
from gentle_stream.appearance_ui import SettingsScreen
from gentle_stream.client import PlayerError
from gentle_stream.playlist_library import PlaylistLibrary
from gentle_stream.presets import PresetStore
from gentle_stream.style import DEFAULT, Style


@pytest.fixture
def ui(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    store = PresetStore(tmp_path / "presets.json")
    library = PlaylistLibrary(tmp_path / "playlist-library.json")
    player = Mock(now=SimpleNamespace(style=DEFAULT, piece=1, bar=0,
                                     playlist_name="Audible", playlist_index=1),
                  playing=True, volume=.8, continuous=False,
                  playlist_name="Future", playlist_index=9)
    player.spectrum.side_effect = lambda width: np.zeros(width)
    player.set_volume.side_effect = lambda value: setattr(player, "volume", value)
    player.toggle.side_effect = lambda: setattr(player, "playing", not player.playing)
    player.set_continuous.side_effect = lambda value: setattr(player, "continuous", value)
    @contextmanager
    def stage(style):
        def commit():
            player.set_style(style)
            return True
        yield commit
    player.stage_style.side_effect = stage
    shell = Mock(model=None)
    shell.list_models.return_value = ["provider/chosen-model"]
    app = StreamApp(player, shell, presets=store)
    app.library = library
    return app, player, shell, store, library


def shown(app, selector):
    return str(app.screen.query_one(selector).render())


@pytest.mark.asyncio
async def test_pause_resume_focus_does_not_invert_button_background(ui):
    app, player, shell, _, _ = ui
    async with app.run_test(size=(80, 24)) as pilot:
        button = app.query_one("#play", Button)
        assert await pilot.click("#play")
        await pilot.hover("#spectrum")
        await pilot.pause(button.active_effect_duration + .05)
        await pilot.wait_for_scheduled_animations()
        assert app.focused is button and button.can_focus
        assert player.playing is False and str(button.label) == "▶"
        assert not button.styles.text_style.reverse
        assert not button.styles.text_style.underline
        assert button.styles.text_style.bold
        assert button.styles.border_left[0] in ("", "none")
        assert button.styles.border_right[0] in ("", "none")
        await pilot.press("enter")
        await pilot.pause(button.active_effect_duration + .05)
        await pilot.wait_for_scheduled_animations()
        assert player.playing is True and str(button.label) == "⏸"
        assert app.focused is button
        assert not button.styles.text_style.reverse
        assert not button.styles.text_style.underline
        assert button.styles.border_left[0] in ("", "none")
        assert button.styles.border_right[0] in ("", "none")
        assert player.toggle.call_count == 2
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_transport_double_clicks_are_not_debounced(ui):
    app, player, shell, store, library = ui
    async with app.run_test(size=(80, 24)) as pilot:
        assert await pilot.click("#play", times=2)
        await pilot.pause()
        assert player.toggle.call_count == 2 and player.playing is True
        assert str(app.query_one("#play", Button).label) == "⏸"
        assert await pilot.click("#next", times=2)
        await pilot.pause()
        assert player.next.call_count == 2
        assert await pilot.click("#previous", times=2)
        await pilot.pause()
        assert player.previous.call_count == 2
        assert not store.path.exists() and not library.path.exists()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_toolbar_background_is_black_with_and_without_keyboard_focus(ui):
    app, player, shell, _, _ = ui
    async with app.run_test(size=(80, 24)) as pilot:
        for button in app.query("#commands Button"):
            assert button.styles.background.hex == "#000000", button.id
            button.focus()
            await pilot.pause()
            assert app.focused is button
            assert button.styles.background.hex == "#000000", button.id
            assert not button.styles.text_style.reverse
            assert not button.styles.text_style.underline
            assert button.styles.border_left[0] in ("", "none")
            assert button.styles.border_right[0] in ("", "none")
        player.toggle.assert_not_called()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(60, 20), (80, 24)])
async def test_toolbar_clicks_keyboard_and_geometry(ui, size):
    app, p, shell, store, _ = ui
    async with app.run_test(size=size) as pilot:
        panel, strip, mood = [app.query_one(s) for s in ("#panel", "#commands", "#mood")]
        assert panel.region.bottom <= strip.region.y < strip.region.bottom <= mood.region.y
        assert app.query_one("#spectrum").region.height >= 5
        for selector in ("#state", "#model", "#status"):
            assert app.query_one(selector).region.bottom <= panel.content_region.bottom
        for id, icon in {"previous": "⏮", "play": "⏸", "next": "⏭", "mood-command": "✎",
                         "save-command": "💾", "playlist-command": "☷", "continuous": "↻  off"}.items():
            assert str(app.query_one(f"#{id}", Button).label) == icon
        for id in ("previous", "play", "next", "mood-command", "model-command", "save-command", "playlist-command",
                   "continuous", "quieter", "louder"):
            button = app.query_one(f"#{id}", Button)
            assert button.can_focus and button.tooltip
            await pilot.wait_for_scheduled_animations()  # finish focus scrolling from the previous click
            button.scroll_visible(animate=False, immediate=True)
            await pilot.pause()
            assert await pilot.click(f"#{id}", offset=(button.size.width // 2, 0)), id
            await pilot.pause()
            if id == "model-command":
                assert isinstance(app.screen, SettingsScreen)
                await app.screen.submit('model')
                assert isinstance(app.screen, ModelPicker)
                await pilot.pause()
                await pilot.press("down", "enter")
                await pilot.pause()
                await pilot.press("escape")
                await pilot.pause()
                assert "chosen-model" in str(app.query_one("#model-command", Button).label)
                model_button = app.query_one("#model-command", Button)
                assert model_button.content_region.width >= len(str(model_button.label))
                assert "provider/chosen-model" in str(app.query_one("#model-command").tooltip)
            elif id == "save-command":
                assert isinstance(app.screen, SaveDialog)
                await pilot.press("escape")
            elif id == "playlist-command":
                assert isinstance(app.screen, PresetPicker)
                await pilot.press("escape")
        p.toggle.assert_called_once()
        p.next.assert_called_once()
        p.previous.assert_called_once()
        assert p.set_volume.call_count == 2 and p.volume == .8
        p.set_playlist.assert_not_called()
        assert "Select a song" in shown(app, "#status")
        await pilot.press("escape", "space", "n", "minus", "plus")
        assert p.toggle.call_count == 2 and p.next.call_count == 2
        await pilot.press("escape", "tab")
        assert isinstance(app.focused, Button)
        (store.path.parent / f"toolbar-{size[0]}x{size[1]}.svg").write_text(app.export_screenshot(), encoding="utf-8")
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(60, 20), (80, 24), (100, 30), (200, 46)])
async def test_volume_meter_lives_between_controls_without_moving_transport(ui, size):
    app, player, shell, store, library = ui
    player.volume = 0
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        strip = app.query_one("#commands")
        center = app.query_one("#transport")
        right = app.query_one("#commands-right")
        meter = app.query_one("#volume-meter")
        assert strip.styles.background.hex == "#000000"
        assert strip.region.height == 1
        assert strip.region.contains_region(center.region)
        assert abs((center.region.x * 2 + center.region.width) -
                   (strip.region.x * 2 + strip.region.width)) <= 1
        assert [child.id for child in right.children] == ["quieter", "volume-meter", "louder", "model-command"]
        assert meter.region.width == 13 and meter.region.height == 1
        model = app.query_one('#model-command', Button)
        assert meter.region.right <= model.region.x
        if size[0] >= 100:
            assert model.region.width <= 14  # Default label must not push the gauge left.
        assert strip.region.contains_region(meter.region)
        assert meter.render().plain == "▱" * 8 + " 0%"
        at_zero = meter.render().plain
        state = shown(app, "#state")
        assert "Playing" in state and "piece 1" in state and "bar 1" in state
        assert "Audible #2" in state and "vol " not in state
        assert "Gentle Shell default" in shown(app, "#model")
        for selector in ("#details", "#mood-line"):
            assert str(app.query_one(selector).tooltip) == shown(app, selector)
        assert app.query_one("#spectrum").region.height >= 5
        for selector in ("#state", "#model", "#status"):
            assert app.query_one(selector).region.bottom <= app.query_one("#panel").content_region.bottom
        louder = app.query_one("#louder", Button)
        louder.scroll_visible(animate=False, immediate=True)
        await pilot.pause()
        assert await pilot.click("#louder")
        await pilot.pause()
        assert player.volume == .05 and meter.render().plain == "▱" * 8 + " 5%"
        assert meter.render().plain != at_zero
        await pilot.press("plus", "minus", "down", "up")
        await pilot.pause()
        assert player.volume == .05 and meter.render().plain == "▱" * 8 + " 5%"
        assert player.set_volume.call_count == 5
        player.toggle.assert_not_called()
        player.next.assert_not_called()
        player.previous.assert_not_called()
        shell.interpret_with_usage.assert_not_called()
        assert not store.path.exists() and not library.path.exists()


@pytest.mark.asyncio
async def test_wide_window_keeps_transport_visible_and_fills_spectrum(ui):
    app, player, shell, _, _ = ui
    player.spectrum.side_effect = lambda count: np.ones(count)
    async with app.run_test(size=(200, 46)) as pilot:
        await pilot.pause()
        strip = app.query_one("#commands")
        center = app.query_one("#transport")
        spectrum = app.query_one("#spectrum")
        assert 0 <= strip.region.y < strip.region.bottom <= app.screen.size.height
        assert center.region.width == 15 and strip.region.contains_region(center.region)
        assert abs((center.region.x * 2 + center.region.width) - (strip.region.x * 2 + strip.region.width)) <= 1
        assert spectrum.region.width > 100
        assert all(len(line) == spectrum.region.width for line in spectrum.render().plain.splitlines())
        assert not app.query_one("#mood").region.y >= app.screen.size.height
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(60, 20), (80, 24), (120, 30)])
async def test_centered_transport_and_independent_side_groups(ui, size):
    app, p, shell, store, _ = ui
    shell.model = "provider/" + "long-model-" * 20
    p.playlist_index = None
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        strip = app.query_one("#commands").content_region
        left = app.query_one("#commands-left")
        center = app.query_one("#transport")
        right = app.query_one("#commands-right")
        assert [b.id for b in left.query(Button)] == ["panic", "save-command", "continuous", "mood-command", "playlist-command"]
        assert [b.id for b in center.query(Button)] == ["previous", "play", "next"]
        assert [b.id for b in right.query(Button)] == ["quieter", "louder", "model-command"]
        assert abs((center.region.x * 2 + center.region.width) - (strip.x * 2 + strip.width)) <= 1
        assert left.region.right <= center.region.x < center.region.right <= right.region.x
        assert all(center.region.contains_region(b.region) for b in center.query(Button))
        assert str(app.query_one("#previous", Button).label) == "⏮"
        assert "previous" in str(app.query_one("#previous").tooltip).lower()
        assert "long-model" in str(app.query_one("#model-command", Button).label)
        assert shell.model in str(app.query_one("#model-command").tooltip)
        await pilot.click("#previous")
        p.previous.assert_not_called()
        assert "Select a song in Playlist" in shown(app, "#status")
        assert app.is_running
        if size[0] in (80, 120):
            (store.path.parent / f"centered-{size[0]}.svg").write_text(app.export_screenshot(), encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(60, 20), (80, 24), (120, 30)])
async def test_paused_play_is_blue_without_shifting_centered_transport(ui, size):
    app, player, shell, store, library = ui
    async with app.run_test(size=size) as pilot:
        strip = app.query_one("#commands")
        transport = app.query_one("#transport")
        play = app.query_one("#play", Button)
        before = transport.region
        assert await pilot.click("#play")
        await pilot.pause()
        assert player.playing is False and str(play.label) == "▶"
        assert play.styles.color.hex.lower() == "#00a6ed"
        assert play.styles.background.hex == strip.styles.background.hex == "#000000"
        assert play.region.width == 5 and transport.region == before
        play.focus()
        await pilot.pause()
        assert play.styles.color.hex.lower() == "#00a6ed"
        assert await pilot.click("#play")
        await pilot.pause()
        assert player.playing is True and str(play.label) == "⏸"
        assert play.styles.background.hex == "#000000"
        assert play.region.width == 5 and transport.region == before
        assert player.toggle.call_count == 2
        assert not store.path.exists() and not library.path.exists()
        shell.interpret_with_usage.assert_not_called()

