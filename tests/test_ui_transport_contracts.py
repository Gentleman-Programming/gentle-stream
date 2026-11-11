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
@pytest.mark.parametrize("method,key,button", [("toggle", "space", "play"), ("next", "n", "next"),
                                             ("set_volume", "minus", "quieter"), ("panic", "f4", "panic")])
@pytest.mark.parametrize("via", ["keyboard", "click"])
async def test_transport_rejections_stay_actionable_without_crashing(ui, method, key, button, via):
    app, p, shell, store, library = ui
    getattr(p, method).side_effect = PlayerError("transport cut unavailable")
    async with app.run_test() as pilot:
        if via == "click":
            app.query_one(f"#{button}").scroll_visible(immediate=True)
            await pilot.pause()
            assert await pilot.click(f"#{button}")
        else:
            await pilot.press(key)
        await pilot.pause()
        assert "transport cut unavailable" in shown(app, "#status")
        assert app.is_running and p.playing and p.volume == .8
        p.close.assert_not_called()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(180, 50), (140, 40), (80, 24)])
async def test_panic_button_shortcut_and_reopen_error_are_honest(ui, size):
    app, p, shell, store, library = ui
    async with app.run_test(size=size) as pilot:
        panic = app.query_one("#panic", Button)
        assert str(panic.label) == "Panic" and "F4" in str(panic.tooltip)
        assert app.query_one("#commands-left").region.contains_region(panic.region)
        assert await pilot.click("#panic")
        p.panic.assert_called_once()
        assert "Current track restarted" in shown(app, "#status")
        await pilot.press("slash", "f4")  # available even while the mood input owns focus
        assert p.panic.call_count == 2 and app.query_one("#mood", Input).value == ""
        def fail():
            p.playing = False
            raise PlayerError("Panic restart failed: device [busy]")
        p.panic.side_effect = fail
        await pilot.press("f4")
        assert "device [busy]" in shown(app, "#status")
        assert "Paused" in shown(app, "#state") and app.is_running
        assert str(app.query_one("#play", Button).label) == "▶"
        assert not store.path.exists() and not library.path.exists()
        shell.interpret_with_usage.assert_not_called()
        shell.list_models.assert_not_called()
        p.next.assert_not_called()
        p.set_style.assert_not_called()


@pytest.mark.asyncio
async def test_late_mood_application_cannot_cross_panic(ui, monkeypatch):
    import asyncio
    import threading

    app, p, shell, store, library = ui
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    ui_thread = threading.get_ident()
    late = Style(name="Late cancelled mood")
    shell.interpret_with_usage.return_value = (late, None)
    p.style = DEFAULT
    p.continuous = True
    selection = ([DEFAULT], 0, "Retained")
    app._selection = selection
    def gate():
        if not entered.is_set():
            entered.set()
            assert release.wait(5), "application gate was not released"
    def apply(style):
        if threading.get_ident() != ui_thread:
            gate()  # old worker has passed its cancellation check but not applied
        p.style = style
        p.continuous = False
    p.set_style.side_effect = apply
    dispatch = app.call_from_thread
    def gated_dispatch(callback, *args, **kwargs):
        if callback.__name__ in ("_interpreted", "_apply_interpretation"):
            gate()  # corrected worker has queued its atomic UI-thread application
            try:
                return dispatch(callback, *args, **kwargs)
            finally:
                finished.set()
        return dispatch(callback, *args, **kwargs)
    monkeypatch.setattr(app, "call_from_thread", gated_dispatch)
    async with app.run_test() as pilot:
        await pilot.press("slash")
        app.query_one("#mood", Input).value = "a different mood"
        await pilot.press("enter")
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            await pilot.press("f4")
            p.panic.assert_called_once()
            assert "Current track restarted" in shown(app, "#status")
        finally:
            release.set()
        assert await asyncio.to_thread(finished.wait, 3)
        await pilot.pause()
        assert p.style == DEFAULT and p.continuous is True
        assert p.volume == .8 and p.playing and p.now.style == DEFAULT
        assert app._selection == selection and app.session_usage is None
        assert "Current track restarted" in shown(app, "#status")
        p.set_style.assert_not_called()
        shell.interpret_with_usage.assert_called_once()
        assert not store.path.exists() and not library.path.exists()


@pytest.mark.asyncio
async def test_ordinary_mood_still_applies_and_reports_failures(ui):
    app, p, shell, store, library = ui
    shell.interpret_with_usage.return_value = (Style(name="Fresh mood"), None)
    async with app.run_test() as pilot:
        for rejected in (False, True):
            p.set_style.side_effect = PlayerError("style unavailable") if rejected else None
            await pilot.press("slash")
            app.query_one("#mood", Input).value = "fresh"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert ("Could not apply style" if rejected else "Fresh mood") in shown(app, "#status")
        assert p.set_style.call_count == 2 and shell.interpret_with_usage.call_count == 2
        p.panic.assert_not_called()
        assert not store.path.exists() and not library.path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("key", ["f4", "q"])
async def test_slow_preparation_keeps_input_and_polling_responsive(ui, monkeypatch, remote, key):
    import asyncio
    import threading
    from multiprocessing import Pipe
    from gentle_stream import client, daemon
    from gentle_stream.player import Player
    from tests.test_continuous_playback import engine

    app, _, shell, store, library = ui
    p = Player(engine(), volume=.37)
    monkeypatch.setattr(p, "start", lambda: None)
    threads, connections = [], []
    def connect():
        left, right = Pipe()
        thread = threading.Thread(target=daemon.Server(p, None).serve, args=(right,))
        threads.append(thread)
        thread.start()
        connection = client.PlayerClient(left)
        connections.append(connection)
        return connection
    monkeypatch.setattr(client, "connect", connect)
    app.player = connect() if remote else p
    if remote:
        app.player.close = app.player.detach  # no daemon main loop in this synthetic server
        before = app.player.request("state")
        for command, error in (({"cmd": "commit_style"}, "no staged style"),
                               ({"cmd": "prepare_style", "style": []}, "style must be an object")):
            with pytest.raises(PlayerError) as rejected:
                app.player.request(command.pop("cmd"), **command)
            assert str(rejected.value) == f"bad request: {error}"
        assert app.player.request("state") == before
        with app.player.stage_style(p.style) as commit:
            assert commit() is True
        with app.player.stage_style(p.style) as commit:
            app.player.panic()
            before = app.player.request("state")
            assert commit() is None
            assert app.player.request("state") == before
    entered, release = threading.Event(), threading.Event()
    late = Style(name="Obsolete prepared mood")
    shell.interpret_with_usage.return_value = (late, None)
    prepare = p.engine.prepare_playlist
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return prepare(*args, **kwargs)
    monkeypatch.setattr(p.engine, "prepare_playlist", slow)
    watchdog = threading.Timer(3, release.set)
    watchdog.start()
    try:
        async with app.run_test() as pilot:
            await pilot.press("slash")
            app.query_one("#mood", Input).value = "new mood"
            await pilot.press("enter")
            assert await asyncio.to_thread(entered.wait, 2)
            app.player.spectrum(8)  # actual UI polling connection remains available
            await asyncio.wait_for(pilot.press(key), 1)
            assert not release.is_set(), "input waited for preparation"
            if key == "f4":
                assert "Current track restarted" in shown(app, "#status")
            else:
                assert not app.is_running
            release.set()
            await pilot.pause()
        for connection in connections:
            connection.detach()
        p.next()  # public render control exposes any stale pending mood
        assert p.style.name != late.name and p.volume == .37
        assert app.session_usage is None and not store.path.exists() and not library.path.exists()
    finally:
        release.set()
        watchdog.cancel()
        watchdog.join()
        for connection in connections:
            connection.detach()
        for thread in threads:
            await asyncio.to_thread(thread.join, 3)
            assert not thread.is_alive()
        p.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["previous", "panic"])
async def test_navigation_old_daemon_error_visible_ui_alive(ui, command):
    import json
    from gentle_stream.client import PlayerClient
    from tests.test_continuous_playback import LocalConnection, engine
    from gentle_stream.player import Player

    app, _, shell, store, library = ui
    p = Player(engine())
    p.set_playlist((DEFAULT,), name="Old")
    class OldConnection(LocalConnection):
        def send_bytes(self, raw):
            if json.loads(raw)["cmd"] == command:
                self.answer = {"ok": False, "error": f"bad request: unknown command '{command}'"}
            else:
                super().send_bytes(raw)
    client = PlayerClient(OldConnection(p))
    client.close = lambda: None  # no socket lifecycle in this in-memory transport
    app.player = client
    async with app.run_test() as pilot:
        before = p.playback_state()
        await pilot.click(f"#{command}")
        await pilot.pause()
        assert f"unknown command '{command}'" in shown(app, "#status")
        assert app.is_running and p.playback_state() == before
        await pilot.press("escape", "space")
        assert not p.playing
        shell.interpret_with_usage.assert_not_called()

