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
async def test_groups_back_delete_scope_and_continuous_selected(ui):
    app, p, shell, store, library = ui
    store.save("Shared", DEFAULT)
    library.save("Night", "Zulu", Style(bpm=80))
    library.save("Night", "Alpha", Style(bpm=70))
    library.save("Other", "Zulu", Style(bpm=90))
    before = store.path.read_bytes()
    async with app.run_test() as pilot:
        await pilot.press("p")
        await pilot.pause()
        options = app.screen.query_one("#presets", OptionList)
        assert options.option_count == 3
        assert "Saved prompts" in str(options.get_option_at_index(0).prompt)
        await pilot.press("down", "enter")
        await pilot.pause()
        assert "Night" in shown(app, "#presets-breadcrumb")
        assert "Alpha" in str(options.get_option_at_index(0).prompt)
        await pilot.press("down")
        assert await pilot.click("#playlist-loop")
        await pilot.pause()
        songs = library.list_songs("Night")
        p.set_playlist.assert_called_once_with([s.style for s in songs], start_index=1,
                                              name="Night", continuous=True)
        p.play.assert_called_once()
        p.set_style.assert_not_called()
        p.continuous = True
        await pilot.click("#continuous")
        p.set_continuous.assert_called_once_with(False)
        assert p.play.call_count == 1
        await pilot.press("p")
        await pilot.pause()
        await pilot.press("down", "enter")
        await pilot.pause()
        await pilot.press("down", "d")
        assert library.get("Night", "Zulu") is None
        assert library.get("Other", "Zulu") is not None
        assert store.path.read_bytes() == before
        assert await pilot.click("#playlist-back")
        await pilot.pause()
        assert options is not None
        assert app.screen.query_one("#presets", OptionList).option_count == 3
        shell.interpret_with_usage.assert_not_called()
        shell.list_models.assert_not_called()


@pytest.mark.asyncio
async def test_normal_selection_remembers_full_list_for_toolbar_and_errors(ui):
    app, p, shell, store, library = ui
    library.save("[bold]", "[x]", DEFAULT)
    library.save("[bold]", "Z", Style(bpm=90))
    async with app.run_test() as pilot:
        await pilot.press("p")
        await pilot.pause()
        await pilot.press("down", "enter")
        await pilot.pause()
        await pilot.press("down", "enter")
        await pilot.pause()
        p.set_style.assert_called_once_with(library.get("[bold]", "Z").style)
        p.set_playlist.side_effect = PlayerError("old daemon: unknown command")
        await pilot.click("#continuous")
        await pilot.pause()
        assert "old daemon: unknown command" in shown(app, "#status")
        p.play.assert_not_called()
        assert "off" in str(app.query_one("#continuous", Button).label)
        assert "Future" not in shown(app, "#state")
        assert "Audible" in shown(app, "#state")
        p.set_playlist.side_effect = None
        await pilot.press("c")
        p.set_playlist.assert_called_with([s.style for s in library.list_songs("[bold]")],
                                        start_index=1, name="[bold]", continuous=True)
        p.play.assert_called_once()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_default_continuous_starts_highlighted_and_is_read_only(ui):
    app, p, shell, store, library = ui
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        await pilot.press("right", "down", "d")
        assert await pilot.click("#playlist-loop")
        await pilot.pause()
        from gentle_stream.playlists import default_playlist
        styles = sorted(default_playlist(), key=lambda s: s.name.casefold())
        p.set_playlist.assert_called_once_with(styles, start_index=1, name="Default playlist", continuous=True)
        p.play.assert_called_once()
        assert not store.path.exists() and not library.path.exists()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_flower_marks_existing_and_new_songs_in_all_playlist_views(ui):
    app, player, shell, store, library = ui
    store.save("Legacy", DEFAULT)
    library.save("Test Group", "Before", DEFAULT)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("p", "enter")
        await pilot.pause()
        options = app.screen.query_one("#presets", OptionList)
        assert str(options.get_option_at_index(0).prompt).startswith("🌸  Legacy")
        await pilot.press("backspace", "down", "enter")
        await pilot.pause()
        assert str(options.get_option_at_index(0).prompt).startswith("🌸  Before")
        await pilot.press("escape")
        await pilot.pause()  # Wait for the first modal to dismiss before reopening it.
        library.save("Test Group", "After", Style(bpm=90))
        stored = (store.path.read_bytes(), library.path.read_bytes())
        await pilot.press("p", "down", "enter")
        await pilot.pause()
        options = app.screen.query_one("#presets", OptionList)  # New modal, not the dismissed one.
        assert options.option_count == 2
        assert all(str(options.get_option_at_index(i).prompt).startswith("🌸  ") for i in range(2))
        assert [song.name for song in library.list_songs("Test Group")] == ["After", "Before"]
        await pilot.press("right")
        await pilot.pause()
        assert options.option_count > 2
        assert all(str(options.get_option_at_index(i).prompt).startswith("🌸  ")
                   for i in range(options.option_count))
        assert (store.path.read_bytes(), library.path.read_bytes()) == stored
        player.set_style.assert_not_called()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_wrapped_named_songs_leave_a_blank_row_between_options(ui):
    app, player, shell, store, library = ui
    mood = "Synthetic energetic rhythm with warm keys and many descriptive words that wrap on a narrow list"
    library.save("Test Group", "First", Style(mood=mood))
    library.save("Test Group", "Second", Style(mood=mood))
    stored = library.path.read_bytes()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.press("p", "down", "enter")
        await pilot.pause()
        options = app.screen.query_one("#presets", OptionList)
        rows = [options.render_line(y).text for y in range(options.size.height)]
        first = next(i for i, row in enumerate(rows) if "First" in row)
        second = next(i for i, row in enumerate(rows) if "Second" in row)
        assert second > first + 2  # description spans multiple rows before the next song
        assert any(not row.strip() for row in rows[first + 1:second]), "No visible blank row between songs"
        assert options.option_count == 2
        await pilot.press("down", "enter")
        player.set_style.assert_called_once_with(library.list_songs("Test Group")[1].style)
        assert library.path.read_bytes() == stored and not store.path.exists()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_default_playlist_songs_have_one_visual_gap_without_changing_selection(ui):
    app, player, shell, store, library = ui
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("p", "right")
        await pilot.pause()
        options = app.screen.query_one("#presets", OptionList)
        from gentle_stream.playlists import default_playlist
        styles = sorted(default_playlist(), key=lambda style: style.name.casefold())
        assert options.option_count == len(styles)
        lines = [options.render_line(y).text for y in range(options.size.height)]
        blank_rows = [line for line in lines if not line.strip()]
        assert blank_rows, "The visible song list has no blank row between adjacent options"
        assert all(options.get_option_at_index(index).id == f"song-{index}"
                   for index in range(options.option_count))
        await pilot.press("down")
        assert options.highlighted == 1
        await pilot.press("enter")
        player.set_style.assert_called_once_with(styles[1])
        assert not store.path.exists() and not library.path.exists()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_empty_group_hint_and_mood_markup_are_literal(ui):
    app, p, shell, store, library = ui
    library.save("Empty", "Gone", DEFAULT)
    library.delete("Empty", "Gone")
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        await pilot.press("down", "enter")
        await pilot.pause()
        assert app.screen.query_one("#playlist-loop", Button).disabled
        await pilot.press("c")
        assert "Select a song" in shown(app, "#presets-status")
        p.set_playlist.assert_not_called()
        assert library.list_songs("Empty") == [] and len(library.list()) == 1
        await pilot.press("escape")
        # Keep interpretation pending so the status is observable before any result.
        import threading
        release = threading.Event()
        def interpret(*args):
            release.wait(5)
            return DEFAULT, None
        shell.interpret_with_usage.side_effect = interpret
        await pilot.press("slash")
        app.query_one("#mood", Input).value = "[bold]fog[/bold]"
        await pilot.press("enter")
        try:
            assert "[bold]fog[/bold]" in shown(app, "#status")
        finally:
            release.set()
            await app.workers.wait_for_complete()


@pytest.mark.asyncio
async def test_previous_click_ordinary_picker_real_audio_and_pause(ui, monkeypatch):
    from gentle_stream.player import Player
    from tests.test_continuous_playback import SONGS, engine
    from tests.test_player import FakeStream

    app, _, shell, store, library = ui
    p = Player(engine(), volume=.37)
    monkeypatch.setattr(p, "start", lambda: None)  # real queue, explicit fake callback; no device/producer
    app.player = p
    for s in SONGS:
        library.save("Evening", s.name, s)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        await pilot.press("down", "enter")
        await pilot.pause()
        await pilot.press("enter")  # ordinary One, no eager full-snapshot preparation
        await pilot.pause()
        assert not p.continuous and p.playlist_index is None
        await pilot.click("#previous")
        await pilot.pause()
        # Displayed alphabetical order is One, Three, Two: previous from One is Two.
        assert p.style.name == "Two" and p.playlist_index == 2
        stream = FakeStream(callback=p._callback)
        stream.pull(128)
        app._tick()
        assert "Two" in shown(app, "#title") and "Evening #3" in shown(app, "#state")
        await pilot.press("escape", "space")
        stream.pull(1024)  # finish ordinary sounddevice fade
        held = p.position
        assert await pilot.click("#previous", times=2)
        await pilot.pause()
        assert p.style.name == "One" and p.playlist_index == 0
        assert not p.playing and not p.continuous and p.volume == .37 and p.position == held
        await pilot.press("escape", "space")
        stream.pull(128)
        app._tick()
        assert p.now.style.name == "One" and "One" in shown(app, "#title")
        shell.interpret_with_usage.assert_not_called()
        shell.list_models.assert_not_called()

