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
async def test_save_new_existing_and_legacy_snapshot_replacement(ui):
    app, p, shell, store, library = ui
    async with app.run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()
        app.screen.query_one("#save-name", Input).value = "Same"
        await pilot.press("enter")
        legacy = store.path.read_bytes()
        await pilot.press("s")
        await pilot.pause()
        app.screen.query_one("#save-destination", Select).value = "new"
        await pilot.pause()
        app.screen.query_one("#save-playlist", Input).value = "[rose] Night"
        app.screen.query_one("#save-name", Input).value = "Same"
        p.now.style = Style(name="Future")  # save uses snapshot from opening dialog
        await pilot.click("#save-submit")
        await pilot.pause()
        assert library.get("[rose] Night", "Same").style == store.get("Same").style
        assert store.path.read_bytes() == legacy
        await pilot.press("s")
        await pilot.pause()
        await pilot.click("#save-destination")  # SelectCurrent is the actual mouse target
        await pilot.press("end", "enter")
        await pilot.pause()
        assert not app.screen.query_one("#save-playlist").display
        app.screen.query_one("#save-name", Input).value = "same"
        await pilot.click("#save-submit")
        await pilot.pause()
        assert len(library.list()) == 1
        assert library.get("[rose] Night", "same").style.name == "same"
        assert library.get("[rose] Night", "same").style.bpm == p.now.style.bpm
        assert "Replaced" in shown(app, "#status")
        assert store.path.read_bytes() == legacy
        shell.interpret_with_usage.assert_not_called()
        shell.list_models.assert_not_called()


@pytest.mark.asyncio
async def test_enter_after_selecting_existing_playlist_saves_the_song(ui):
    app, player, shell, store, library = ui
    library.save("Night", "Existing", DEFAULT)
    before = library.path.read_bytes()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("s")
        await pilot.pause()
        app.screen.query_one("#save-name", Input).value = "Fresh"
        await pilot.click("#save-destination")  # SelectCurrent receives the pointer event
        await pilot.press("end", "enter")  # select the existing group using the public picker
        await pilot.pause()
        assert app.screen.query_one("#save-destination", Select).value == "group-0"
        await pilot.press("enter")  # dialog says Enter saves, even after a Select choice
        await pilot.pause()
        assert library.get("Night", "Fresh") is not None
        assert library.get("Night", "Existing") is not None
        assert library.path.read_bytes() != before
        assert not store.path.exists()
        assert "Saved" in shown(app, "#status")
        player.toggle.assert_not_called()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_keyboard_only_existing_playlist_selection_and_enter_save(ui):
    app, player, shell, store, library = ui
    library.save("Night", "Existing", DEFAULT)
    before = library.path.read_bytes()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("s")
        await pilot.pause()
        app.screen.query_one("#save-name", Input).value = "Keyboard"
        await pilot.press("tab")
        destination = app.screen.query_one("#save-destination", Select)
        assert destination.has_focus_within
        assert "Enter opens" in shown(app, "#save-status")
        assert "Enter again saves" in shown(app, "#save-status")
        await pilot.press("enter", "down", "down", "enter")
        await pilot.pause()
        assert destination.value == "group-0"
        await pilot.press("enter")
        await pilot.pause()
        assert library.get("Night", "Keyboard") is not None
        assert library.get("Night", "Existing") is not None
        assert library.path.read_bytes() != before
        assert not store.path.exists()
        assert "Saved" in shown(app, "#status")
        player.toggle.assert_not_called()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_save_corrupt_and_validation_errors_keep_fields(ui):
    app, p, shell, store, library = ui
    library.path.write_text("{oops")
    async with app.run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()
        app.screen.query_one("#save-destination", Select).value = "new"
        await pilot.pause()
        app.screen.query_one("#save-name", Input).value = "Keep me"
        app.screen.query_one("#save-playlist", Input).value = "   "
        await pilot.click("#save-submit")
        assert "empty" in shown(app, "#save-status").lower()
        app.screen.query_one("#save-playlist", Input).value = "Night"
        app.screen.query_one("#save-playlist", Input).focus()
        await pilot.press("enter")
        assert "playlist-library.json" in shown(app, "#save-status")
        assert app.screen.query_one("#save-name", Input).value == "Keep me"
        assert app.screen.query_one("#save-playlist", Input).value == "Night"
        assert library.path.read_text() == "{oops" and not store.path.exists()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_unwritable_library_keeps_save_and_long_model_is_elided(ui):
    app, p, shell, store, library = ui
    shell.model = "provider/" + "model-" * 20
    library.path.mkdir()
    async with app.run_test(size=(60, 20)) as pilot:
        label = str(app.query_one("#model-command", Button).label)
        assert len(label) <= 26 and "…" in label
        assert shell.model in str(app.query_one("#model-command").tooltip)
        await pilot.press("s")
        await pilot.pause()
        await pilot.click("#save-destination")
        await pilot.press("down", "enter")
        await pilot.pause()
        app.screen.query_one("#save-playlist", Input).value = "Night"
        app.screen.query_one("#save-playlist", Input).focus()
        await pilot.press("enter")
        assert isinstance(app.screen, SaveDialog)
        assert "Could not save" in shown(app, "#save-status")
        assert app.screen.query_one("#save-playlist", Input).value == "Night"
        assert library.path.is_dir() and not store.path.exists()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_playlist_and_save_menus_keep_a_clear_keyboard_hierarchy(ui):
    app, player, shell, store, library = ui
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        picker = app.screen
        assert isinstance(picker, PresetPicker)
        assert "Playlists" in shown(app, ".heading")
        assert "Generative styles" in shown(app, "#picker-description")
        assert picker.query_one("#presets-status").region.y > picker.query_one("#presets").region.y
        assert picker.query_one("#presets", OptionList).has_focus
        await pilot.press("escape", "s")
        await pilot.pause()
        assert isinstance(app.screen, SaveDialog)
        assert "Song name" in shown(app, "#save-name-label")
        assert "Destination" in shown(app, "#save-destination-label")
        assert not app.screen.query_one("#save-playlist-label").display
        app.screen.query_one("#save-destination", Select).value = "new"
        await pilot.pause()
        assert app.screen.query_one("#save-playlist-label").display
        assert app.screen.query_one("#save-playlist", Input).display
        await pilot.press("escape")
        assert not store.path.exists() and not library.path.exists()
        player.toggle.assert_not_called()
        shell.interpret_with_usage.assert_not_called()

