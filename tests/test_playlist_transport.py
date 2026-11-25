"""Ordered transport without an audio device, model, or daemon subprocess."""
import importlib
import json
import sys
import types

import numpy as np
import pytest

from gentle_stream.composer import FIRST_FORM
from gentle_stream.engine import Engine
from gentle_stream.player import Player
from gentle_stream.style import Style
from tests.test_player import FakeStream, wait_for

SONGS = tuple(Style(name=name, bpm=120) for name in ("Zulu", "Alpha", "Middle"))
FORM_BARS = sum(length for _, length in FIRST_FORM)


class SyntheticEngine(Engine):
    def _schedule_bar(self):
        pass

    def _master(self, audio):
        audio[:] = 0.1
        return audio


@pytest.fixture
def protocol(monkeypatch):
    # On Windows import independent module objects, without caching a fake Unix module
    # in either the package or sys.modules after the fixture ends.
    if sys.platform != "win32":
        from gentle_stream import client, daemon
        return client, daemon
    import gentle_stream

    monkeypatch.setitem(sys.modules, "fcntl", types.ModuleType("fcntl"))
    modules = []
    for name in ("daemon", "client"):
        monkeypatch.delattr(gentle_stream, name, raising=False)
        monkeypatch.delitem(sys.modules, f"gentle_stream.{name}", raising=False)
        spec = importlib.util.find_spec(f"gentle_stream.{name}")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        monkeypatch.setattr(gentle_stream, name, module, raising=False)
        spec.loader.exec_module(module)
        modules.append(module)
    return modules[1], modules[0]


def engine():
    return SyntheticEngine(Style(name="Standalone"), seed=17, sr=16000, samples=False)


@pytest.fixture
def output():
    streams = []

    def factory(**kwargs):
        stream = FakeStream(**kwargs)
        streams.append(stream)
        return stream

    player = Player(engine(), stream_factory=factory, prebuffer=0.25)
    player.start()
    try:
        yield player, streams[0]
    finally:
        player.close()


class Loopback:
    def __init__(self, handler):
        self.handler = handler
        self.commands = []

    def send_bytes(self, raw):
        request = json.loads(raw)
        self.commands.append(request["cmd"])
        self.answer = self.handler(request)

    def recv_bytes(self):
        return json.dumps(self.answer).encode()

    def close(self):
        pass


def test_activation_and_navigation_survive_attach_while_paused(output, protocol):
    player, stream = output
    client, daemon = protocol
    handler = lambda request: daemon.handle(player, request)
    first = client.PlayerClient(Loopback(handler))
    first.pause()
    first.set_playlist(SONGS, start_index=1, name="Ordered", continuous=True)
    assert (first.playlist_name, first.playlist_index, first.continuous) == ("Ordered", 1, True)
    assert first.now.style.name == "Standalone" and first.now.playlist_index is None
    first.detach()

    second = client.PlayerClient(Loopback(handler))
    assert (second.playlist_name, second.playlist_index, second.playing) == ("Ordered", 1, False)
    for control, index in ((second.previous, 0), (second.previous, 2), (second.next, 0), (second.next, 1)):
        control()
        assert second.playlist_index == index
        assert second.style == SONGS[index]
        assert second.now.playlist_index is None and not second.playing
    assert not stream.pull().any() and player.position == 0
    second.play()
    assert stream.pull().any()
    second.spectrum(8)
    assert (second.now.playlist_name, second.now.playlist_index, second.now.style) == ("Ordered", 1, SONGS[1])


def test_previous_without_playlist_does_not_touch_audio_or_standalone_next(output, protocol):
    player, stream = output
    client, daemon = protocol
    local = client.PlayerClient(Loopback(lambda request: daemon.handle(player, request)))
    before = daemon.state(player)
    counters = player.position, player.underruns, player.buffered_seconds
    player.previous()
    local.previous()
    assert daemon.state(player) == before
    assert (player.position, player.underruns, player.buffered_seconds) == counters
    local.next()
    assert stream.pull().any()
    local.spectrum(8)
    assert local.now.piece == before["now"]["piece"] + 1
    assert local.now.style.name == "Standalone" and local.playlist_index is None


def test_form_end_wraps_only_in_continuous_mode():
    render = engine()
    render.set_playlist(SONGS, start_index=2, continuous=True)
    for _ in range(FORM_BARS):
        render.render_bar()
    assert (render.style, render.bar) == (SONGS[2], FORM_BARS)
    render.render_bar()
    assert (render.style, render.bar, render.playlist_index) == (SONGS[0], 1, 0)

    render.set_playlist(SONGS, start_index=2, continuous=False)
    for _ in range(FORM_BARS + 1):
        render.render_bar()
    assert (render.style, render.bar, render.playlist_index) == (SONGS[2], FORM_BARS + 1, 2)
    render.queue_next()
    render.render_bar()
    assert (render.style.name, render.bar, render.playlist_index) == (SONGS[2].name, 1, 2)


@pytest.mark.parametrize("direction, expected", [("previous", 2), ("next", 1)])
def test_navigation_uses_consumed_track_not_producer_lookahead(direction, expected):
    streams = []

    def factory(**kwargs):
        streams.append(FakeStream(**kwargs))
        return streams[-1]

    player = Player(engine(), stream_factory=factory, prebuffer=2 * (FORM_BARS + 1))
    player.set_playlist(SONGS, continuous=True)
    player.start()
    try:
        wait_for(lambda: player.buffered_seconds >= 2 * (FORM_BARS + 1))
        assert player.playlist_index == 1
        streams[0].pull()
        assert player.now.playlist_index == 0
        getattr(player, direction)()
        streams[0].pull()
        assert player.now.playlist_index == expected and player.now.style == SONGS[expected]
    finally:
        player.close()


@pytest.mark.parametrize("fields,error", [
    ({"styles": {}}, "playlist styles must be a list"),
    ({"styles": []}, "playlist must contain at least one Style"),
    ({"styles": [{"bpm": 150}]}, "playlist style must contain valid, normalized Style fields"),
    ({"start_index": True}, "playlist start_index must be an integer within the playlist"),
    ({"start_index": 3}, "playlist start_index must be an integer within the playlist"),
    ({"continuous": 1}, "continuous must be a boolean"),
    ({"name": 1}, "playlist name must be a string"),
])
def test_rejected_playlist_is_atomic(output, protocol, fields, error):
    player, _ = output
    _, daemon = protocol
    player.set_playlist(SONGS, start_index=1, continuous=True)
    player.pause()
    before = daemon.state(player)
    counters = player.position, player.underruns, player.buffered_seconds
    request = {"cmd": "set_playlist", "styles": [style.to_dict() for style in SONGS], **fields}
    assert daemon.handle(player, request) == {"ok": False, "error": f"bad request: {error}"}
    assert daemon.state(player) == before
    assert (player.position, player.underruns, player.buffered_seconds) == counters


def test_mood_clears_playlist_and_still_wins_over_queued_next(output, protocol):
    player, stream = output
    _, daemon = protocol
    player.set_playlist(SONGS, start_index=1, continuous=True)
    mood = Style(name="New Mood")
    player.set_style(mood)
    assert player.playlist_index is None
    player.previous()
    player.next()
    stream.pull()
    assert player.now.style == mood and player.now.playlist_index is None
    assert daemon.state(player)["continuous"] is False


def test_existing_daemon_toggle_and_volume_survive_the_playback_snapshot(output, protocol):
    player, stream = output
    client, daemon = protocol
    remote = client.PlayerClient(Loopback(lambda request: daemon.handle(player, request)))
    remote.set_volume(0.3)
    assert remote.volume == player.volume == 0.3
    remote.toggle()
    assert not remote.playing and not stream.pull().any()
    remote.toggle()
    assert remote.playing and stream.pull().any()
    assert remote.now.style.name == "Standalone" and remote.now.piece == 1


def test_older_daemon_can_attach_and_continue_using_standalone_controls(output, protocol):
    player, stream = output
    client, daemon = protocol

    def legacy(request):
        assert request["cmd"] in {"state", "next"}
        response = daemon.handle(player, request)
        state = response["state"]
        for key in ("playlist_name", "playlist_index", "continuous"):
            state.pop(key, None)
            state["now"].pop(key, None)
        return response

    remote = client.PlayerClient(Loopback(legacy))
    assert remote.playlist_name is remote.playlist_index is remote.now.playlist_index is None
    assert not remote.continuous
    remote.previous()
    remote.next()
    stream.pull()
    remote.spectrum(8)
    assert remote.now.piece == 2
