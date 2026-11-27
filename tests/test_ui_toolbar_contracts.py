"""Public Textual flows; no audio devices, generators, or live player."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from textual.command import CommandPalette
from textual.widgets import Button, Checkbox, Input

from gentle_stream import main
from gentle_stream.app import ModelPicker, PresetPicker, SaveDialog, StreamApp
from gentle_stream.appearance_ui import SettingsScreen
from gentle_stream.playlist_library import PlaylistLibrary
from gentle_stream.presets import PresetStore
from gentle_stream.style import DEFAULT


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
        assert player.playing is False and (str(button.label), button.tooltip) == ('▸', 'Play (Space)')
        assert not button.styles.text_style.reverse
        assert button.styles.text_style.underline and button.styles.text_style.bold
        assert button.styles.border_left[0] == button.styles.border_right[0] == 'round'
        await pilot.press("enter")
        await pilot.pause(button.active_effect_duration + .05)
        await pilot.wait_for_scheduled_animations()
        assert player.playing is True and (str(button.label), button.tooltip) == ('Ⅱ', 'Pause (Space)')
        assert app.focused is button
        assert not button.styles.text_style.reverse and button.styles.text_style.underline
        assert button.styles.border_left[0] == button.styles.border_right[0] == 'round'
        assert player.toggle.call_count == 2
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_transport_double_clicks_are_not_debounced(ui):
    app, player, shell, store, library = ui
    async with app.run_test(size=(80, 24)) as pilot:
        assert await pilot.click("#play", times=2)
        await pilot.pause()
        assert player.toggle.call_count == 2 and player.playing is True
        assert str(app.query_one("#play", Button).label) == 'Ⅱ'
        assert await pilot.click("#next", times=2)
        await pilot.pause()
        assert player.next.call_count == 2
        assert await pilot.click("#previous", times=2)
        await pilot.pause()
        assert player.previous.call_count == 2
        assert not store.path.exists() and not library.path.exists()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_toolbar_uses_header_surface_with_and_without_keyboard_focus(ui):
    app, player, shell, _, _ = ui
    async with app.run_test(size=(80, 24)) as pilot:
        background = app.query_one("Header").styles.background
        assert app.query_one("#commands").styles.background == background
        surface = app.screen.styles.background
        muted = app.query_one('#details').styles.color
        for button in (b for b in app.query('#commands Button') if b.display):
            if button.id == 'continuous':
                assert button.styles.color == muted
            else:
                assert button.styles.color.hex.lower() == app.preferences.accent
            assert all(edge == ('round', muted) for edge in button.styles.border)
            assert button.styles.background == surface, button.id
            button.focus()
            await pilot.pause()
            assert app.focused is button
            assert button.styles.background == surface, button.id
            assert not button.styles.text_style.reverse and button.styles.text_style.underline
            assert all(edge == ('round', button.styles.color) for edge in button.styles.border)
            app.set_focus(None)
            await pilot.pause()
        player.toggle.assert_not_called()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(60, 20), (80, 24)])
async def test_toolbar_clicks_keyboard_and_geometry(ui, size):
    app, p, shell, store, _ = ui
    async with app.run_test(size=size) as pilot:
        panel, strip, mood = [app.query_one(s) for s in ("#panel", "#commands", "#mood")]
        assert panel.region.bottom <= strip.region.y < strip.region.bottom <= mood.region.y
        assert mood.region.bottom <= size[1]
        if size[1] >= 24:
            assert app.query_one('#spectrum').region.height >= 5
            for selector in ('#state', '#model', '#status'):
                assert app.query_one(selector).region.bottom <= panel.content_region.bottom
        for id, label in {'previous': '←', 'play': 'Ⅱ', 'next': '→', 'save-command': '▣↓',
                          'playlist-command': '☰', 'continuous': '⇄', 'settings-command': '⛭'}.items():
            assert str(app.query_one(f"#{id}", Button).label) == label
        for id in ("previous", "play", "next", "settings-command", "save-command", "playlist-command",
                   "continuous", "quieter", "louder"):
            button = app.query_one(f"#{id}", Button)
            assert button.can_focus and button.tooltip
            await pilot.wait_for_scheduled_animations()  # finish focus scrolling from the previous click
            button.scroll_visible(animate=False, immediate=True)
            await pilot.pause()
            assert await pilot.click(f"#{id}"), id
            await pilot.pause()
            if id == "settings-command":
                assert isinstance(app.screen, SettingsScreen)
                await pilot.press("escape")
                await pilot.press("m")
                assert isinstance(app.screen, ModelPicker)
                await pilot.pause()
                await pilot.press("down", "enter")
                assert shown(app, "#model") == "model: provider/chosen-model"
                assert str(app.query_one("#settings-command", Button).label) == '⛭'
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
        assert not store.path.exists()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(60, 34), (80, 24), (100, 30), (200, 46)])
async def test_volume_meter_lives_between_controls_without_moving_transport(ui, size):
    app, player, shell, store, library = ui
    player.volume = 0
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        strip = app.query_one("#commands")
        center = app.query_one("#transport")
        right = app.query_one("#commands-right")
        meter = app.query_one("#volume-meter")
        assert strip.region.height == (9 if size[0] < 72 else 6 if size[0] < 84 else 3)
        assert strip.region.contains_region(center.region)
        assert abs((center.region.x * 2 + center.region.width) -
                   (strip.region.x * 2 + strip.region.width)) <= 1
        assert [child.id for child in right.children if child.display] == ['quieter', 'volume-meter', 'louder', 'settings-command']
        assert meter.region.width == 4 and meter.region.height == 1
        assert meter.region.right <= right.query_one("#settings-command").region.x
        assert strip.region.contains_region(meter.region)
        assert (at_zero := shown(app, '#volume-meter')) == '0%'
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
        assert player.volume == .05 and shown(app, '#volume-meter') == '5%'
        assert shown(app, '#volume-meter') != at_zero
        await pilot.press('plus', 'minus', 'down', 'up')
        await pilot.pause()
        assert player.volume == .05 and shown(app, '#volume-meter') == '5%'
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
        assert center.region.width == 18 and strip.region.contains_region(center.region)
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
        assert [b.id for b in left.query(Button)] == ["panic", "save-command", "continuous", "playlist-command"]
        assert [b.id for b in center.query(Button)] == ["previous", "play", "next"]
        assert [b.id for b in right.query(Button) if b.display] == ['quieter', 'louder', 'settings-command']
        assert all(left.region.contains_region(b.region) for b in left.query(Button))
        assert all(right.region.contains_region(b.region) for b in right.query(Button) if b.display)
        assert abs((center.region.x * 2 + center.region.width) - (strip.x * 2 + strip.width)) <= 1
        assert left.region.bottom <= center.region.y if size[0] < 84 else left.region.right <= center.region.x
        assert center.region.bottom <= right.region.y if size[0] < 72 else center.region.right <= right.region.x
        assert all(center.region.contains_region(b.region) for b in center.query(Button))
        assert str(app.query_one('#previous', Button).label) == '←'
        assert 'previous' in str(app.query_one('#previous').tooltip).lower()
        assert str(app.query_one('#settings-command', Button).label) == '⛭'
        assert shell.model in str(app.query_one("#model").tooltip)
        await pilot.click("#previous")
        p.previous.assert_not_called()
        assert "Select a song in Playlist" in shown(app, "#status")
        assert app.is_running
        assert not store.path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(60, 20), (80, 24), (120, 30)])
async def test_paused_play_uses_accent_without_shifting_centered_transport(ui, size):
    app, player, shell, store, library = ui
    async with app.run_test(size=size) as pilot:
        surface = app.screen.styles.background
        transport = app.query_one('#transport')
        play = app.query_one('#play', Button)
        before = transport.region
        assert await pilot.click('#play')
        await pilot.hover('Header')
        assert player.playing is False and str(play.label) == '▸'
        assert play.styles.color.hex.lower() == app.preferences.accent
        assert play.styles.background == surface
        assert play.region.width == 6 and transport.region == before
        assert await pilot.hover('#play')
        assert play.styles.color == surface and play.styles.background.hex.lower() == app.preferences.accent
        assert await pilot.click('#play')
        await pilot.hover('Header')
        assert player.playing is True and str(play.label) == 'Ⅱ'
        assert play.styles.background == surface
        assert play.region.width == 6 and transport.region == before
        assert player.toggle.call_count == 2
        assert not store.path.exists() and not library.path.exists()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_repeat_click_and_keyboard_report_state_without_restarting(ui):
    app, player, shell, store, library = ui
    app.apply_preferences(replace(app.preferences, accent='#59cfff'))
    player.set_playlist.side_effect = lambda *args, continuous, **kw: setattr(player, 'continuous', continuous)
    async with app.run_test(size=(110, 24)) as pilot:
        repeat = app.query_one('#continuous', Button)
        assert repeat.tooltip == 'Repeat: Off (c)'
        muted = repeat.styles.color
        assert muted.hex.lower() == '#a78e9b'
        await pilot.press('p')
        await pilot.press('right', 'enter')
        assert app.screen.query_one('#continuous', Button) is repeat
        assert await pilot.click('#continuous')
        await pilot.hover('Header')
        assert player.continuous and repeat.tooltip == 'Repeat: On (c)'
        assert shown(app, '#status').startswith('Continuous from ')
        await pilot.press('tab')
        assert not repeat.has_focus and repeat.styles.color.hex.lower() == '#59cfff'
        assert all(edge == ('round', repeat.styles.color) for edge in repeat.styles.border)
        await pilot.press('c')
        assert not player.continuous and repeat.tooltip == 'Repeat: Off (c)'
        assert repeat.styles.color == muted
        player.set_playlist.assert_called_once()
        player.set_continuous.assert_called_once_with(False)
        player.play.assert_called_once_with()
        player.panic.assert_not_called()
        shell.interpret_with_usage.assert_not_called()
        assert not store.path.exists() and not library.path.exists()


@pytest.mark.asyncio
async def test_named_header_palette_opens_settings_even_with_hidden_chrome(ui):
    app, player, shell, store, library = ui
    app.apply_preferences(replace(app.preferences, show_controls=False, show_input=False, show_footer=False))
    async with app.run_test(size=(80, 24)) as pilot:
        assert shown(app, "HeaderIcon") == "Command palette"
        assert await pilot.click("HeaderIcon", offset=(4, 0))
        assert isinstance(app.screen, CommandPalette)
        await pilot.press(*"settings")
        await pilot.pause(.35)
        await pilot.press("enter", "enter")
        assert isinstance(app.screen, SettingsScreen)
        await pilot.press("escape", "ctrl+p")
        assert isinstance(app.screen, CommandPalette)
        await pilot.press("escape", "f2")
        assert isinstance(app.screen, SettingsScreen)
        await pilot.press("escape", "slash")
        assert app.query_one("#mood", Input).has_focus
        assert not player.toggle.called and not player.set_style.called
        shell.interpret_with_usage.assert_not_called()
        assert not app.preference_store.path.exists() and not store.path.exists() and not library.path.exists()


async def select_palette_command(pilot, app, label):
    await pilot.press("ctrl+p")
    assert isinstance(app.screen, CommandPalette)
    await pilot.press(*(key if key != " " else "space" for key in label.lower()))
    await pilot.pause(.35)
    await pilot.press("enter")
    await pilot.pause()


@pytest.mark.asyncio
async def test_palette_choose_model(ui):
    app, player, shell, store, library = ui
    async with app.run_test(size=(80, 24)) as pilot:
        await select_palette_command(pilot, app, "Choose model")
        assert isinstance(app.screen, ModelPicker)
        await pilot.pause()
        await pilot.press("down", "enter")
        assert shown(app, "#model") == "model: provider/chosen-model"
        shell.list_models.assert_called_once_with()
        shell.interpret_with_usage.assert_not_called()
        player.set_style.assert_not_called()
        assert not store.path.exists() and not library.path.exists()


@pytest.mark.asyncio
async def test_palette_save_current_prompt(ui):
    app, player, shell, store, library = ui
    async with app.run_test(size=(80, 24)) as pilot:
        await select_palette_command(pilot, app, "Save current prompt")
        assert isinstance(app.screen, SaveDialog)
        assert app.screen.query_one("#save-name", Input).value == player.now.style.name
        await pilot.press("escape")
        assert not store.path.exists() and not library.path.exists()
        await select_palette_command(pilot, app, "Save current prompt")
        await pilot.press("enter")
        assert shown(app, "#status") == f"Saved “{player.now.style.name}”"
        assert not library.path.exists()
        player.set_style.assert_not_called()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_palette_still_offers_panic_restart(ui):
    app, player, shell, store, library = ui
    async with app.run_test(size=(80, 24)) as pilot:
        await select_palette_command(pilot, app, "Panic restart")
        player.panic.assert_called_once_with()
        assert shown(app, "#status") == "Current track restarted from the beginning · same settings · no AI call."
        shell.interpret_with_usage.assert_not_called()
        assert not store.path.exists() and not library.path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(60, 20), (80, 24)])
async def test_main_screen_renders_one_playlist_and_no_strip_bindings_in_footer(ui, size):
    app, player, shell, store, library = ui
    async with app.run_test(size=size) as pilot:
        assert not app.query("#playlist")
        assert [button.id for button in app.query(Button) if str(button.label) == '☰'] == ["playlist-command"]
        await pilot.pause()  # Footer builds its key labels after the initial bindings update.
        footer = app.query_one("Footer")
        assert {key.key for key in footer.query("FooterKey")} == {"slash", "m", "b", "q"}
        assert all(footer.region.contains_region(key.region) for key in footer.query("FooterKey"))
        await pilot.press("f4", "s")
        assert isinstance(app.screen, SaveDialog)
        await pilot.press("escape", "p")
        assert isinstance(app.screen, PresetPicker)
        await pilot.press("escape", "c")
        assert shown(app, "#status") == "Select a song in Playlist (p), then enable repeat (c)."
        player.panic.assert_called_once()
        player.set_playlist.assert_not_called()
        shell.interpret_with_usage.assert_not_called()
        assert not store.path.exists() and not library.path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(60, 34), (80, 24), (180, 50)])
async def test_saved_compact_gives_spectrum_more_room_without_hiding_chrome(ui, size):
    app, player, shell, store, library = ui
    async with app.run_test(size=size) as pilot:
        root = app.screen

        def layout():
            panel, spectrum, strip, mood, footer = [root.query_one(s) for s in
                                                    ("#panel", "#spectrum", "#commands", "#mood", "Footer")]
            assert spectrum.region.height >= app.preferences.height
            assert panel.content_region.contains_region(root.query_one("#status").region)
            assert panel.region.bottom <= strip.region.y < strip.region.bottom <= mood.region.y
            assert mood.region.bottom <= footer.region.y < footer.region.bottom <= size[1]
            return spectrum.region

        standard = layout()
        standard_theme = app.theme
        await pilot.press("f2")
        preview = app.screen.query_one('#appearance-preview')
        original_padding = preview.styles.padding
        app.screen.query_one('#appearance-compact', Checkbox).value = True
        await pilot.pause()
        assert preview.styles.padding.left < original_padding.left
        assert layout() == standard and not app.preference_store.path.exists()
        assert await pilot.click('#appearance-save')
        compact = layout()
        assert compact.height > standard.height and compact.width > standard.width
        assert app.theme == standard_theme
        assert app.preference_store.load().compact
        player.toggle.assert_not_called()
        player.set_style.assert_not_called()
        shell.interpret_with_usage.assert_not_called()
        assert not store.path.exists() and not library.path.exists()


def test_cli_help_describes_the_rendered_controls(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    stdout, stderr = capsys.readouterr()
    assert stderr == ""
    assert "Ctrl+P   open the command palette (also clickable in the header)" in stdout
    assert "Settings and volume right" in stdout
    assert "groups wrap on narrow terminals" in stdout
    assert "Compact gives the spectrum more room by trimming spacing, even on short terminals." in stdout
    assert "F2       open Appearance Settings: draft preview, Audio → Output, and Models" in stdout
    assert "Save and Close applies appearance; X Close or Esc confirms discarding edits." in stdout
    assert "gear opens Settings" not in stdout

