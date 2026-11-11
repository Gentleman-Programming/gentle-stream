"""Previous is playlist navigation, including when rotation is disabled."""
import pytest

from gentle_stream import daemon
from gentle_stream.client import PlayerClient, PlayerError
from gentle_stream.player import Player
from tests.test_continuous_playback import FORM_BARS, SONGS, LocalConnection, engine
from tests.test_player import FakeStream, wait_for


@pytest.mark.parametrize("continuous", [False, True])
@pytest.mark.parametrize("paused", [False, True])
def test_previous_public_action_wrap_and_repeated_selection(continuous, paused):
    p = Player(engine(), volume=.37)
    client = PlayerClient(LocalConnection(p))
    client.set_playlist(SONGS, start_index=0, name="Evening", continuous=continuous)
    if paused:
        client.pause()
    stream = FakeStream(callback=p._callback)
    for index in (2, 1, 0, 2):
        before_position = p.position
        client.previous()
        assert (client.style, client.playlist_index, client.playlist_name) == (SONGS[index], index, "Evening")
        assert (client.playing, client.continuous, client.volume) == (not paused, continuous, .37)
        assert p.position == before_position and p.engine.bar == 1
    if paused:
        stream.pull(128)
        assert p.position == 0 and p.now.playlist_index is None
        client.play()
    stream.pull(128)
    client.spectrum(8)
    assert client.now.style == SONGS[2] and client.now.playlist_index == 2
    assert client.now.playlist_name == "Evening" and client.now.bar == 0
    assert p.position == 128


def test_previous_missing_playlist_rejected_without_state_or_counter_changes():
    p = Player(engine())
    client = PlayerClient(LocalConnection(p))
    before = daemon.state(p)
    counters = (p.engine.samples_out, p.engine.piece, p.engine.bar, p.position)
    with pytest.raises(PlayerError, match="Select a song in Playlist"):
        client.previous()
    assert daemon.state(p) == before
    assert (p.engine.samples_out, p.engine.piece, p.engine.bar, p.position) == counters


@pytest.mark.parametrize("styles,index", [((), 0), (SONGS, -1), (SONGS, True), ((object(),), 0)])
def test_previous_invalid_lazy_snapshot_is_atomic(styles, index):
    p = Player(engine())
    client = PlayerClient(LocalConnection(p))
    client.pause()
    before = daemon.state(p)
    counters = (p.engine.samples_out, p.engine.piece, p.engine.bar, p.position)
    with pytest.raises((ValueError, PlayerError)):
        client.previous(styles, start_index=index, name="Bad")
    assert daemon.state(p) == before
    assert (p.engine.samples_out, p.engine.piece, p.engine.bar, p.position) == counters


def test_previous_lazy_snapshot_validation_failure_and_resident_navigation(monkeypatch):
    from dataclasses import replace

    invalid = replace(SONGS[1], bpm=999)
    p = Player(engine())
    client = PlayerClient(LocalConnection(p))
    before = daemon.state(p)
    counters = (p.engine.samples_out, p.engine.piece, p.engine.bar, p.position)
    with pytest.raises(PlayerError) as rejected:
        client.previous((SONGS[0], invalid), name="Bad")
    assert str(rejected.value) == "bad request: playlist style must contain valid, normalized Style fields"
    assert daemon.state(p) == before
    assert (p.engine.samples_out, p.engine.piece, p.engine.bar, p.position) == counters
    client.set_playlist(SONGS[:2], name="Evening")
    monkeypatch.setattr(p.engine, "prepare_playlist", lambda *a, **k: pytest.fail("navigation must reuse snapshot"))
    client.previous()
    assert client.style == SONGS[1] and client.playlist_index == 1 and not client.continuous
    client.previous()
    assert client.style == SONGS[0] and client.playlist_index == 0


def test_previous_at_producer_lookahead_boundary_and_mixed_navigation():
    e = engine()
    streams = []
    p = Player(e, stream_factory=lambda **kw: streams.append(FakeStream(**kw)) or streams[-1], prebuffer=.01)
    p.set_playlist(SONGS, name="Evening", continuous=True)
    p.start()
    frames = int(round(e.bar_seconds * e.sr))
    try:
        stream = streams[0]
        for _ in range(FORM_BARS - 1):
            wait_for(lambda: p.buffered_seconds >= frames / e.sr)
            stream.pull(frames)
        wait_for(lambda: p.buffered_seconds >= frames / e.sr)
        stream.pull(frames - 80)
        wait_for(lambda: p.playlist_index == 1)
        assert p.now.playlist_index == 0 and p.style == SONGS[1]
        p.previous()
        assert p.playlist_index == 2 and p.style == SONGS[2]
        p.previous()
        assert p.playlist_index == 1 and p.style == SONGS[1]
        p.next()
        assert p.playlist_index == 2 and p.style == SONGS[2]
        stream.pull(128)
        assert p.now.style == SONGS[2] and p.now.playlist_index == 2 and p.now.bar == 0
    finally:
        p.close()
    assert not p._producer.is_alive()


def test_previous_serializes_with_inflight_producer(monkeypatch):
    import threading

    e = engine()
    streams = []
    p = Player(e, stream_factory=lambda **kw: streams.append(FakeStream(**kw)) or streams[-1], prebuffer=.01)
    p.set_playlist(SONGS, start_index=1, name="Evening", continuous=True)
    p.start()
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    render = e.render_bar
    def gated_render():
        entered.set()
        assert release.wait(5)
        return render()
    monkeypatch.setattr(e, "render_bar", gated_render)
    errors = []
    def previous():
        try:
            p.previous()
        except Exception as error:
            errors.append(error)
        finally:
            done.set()
    worker = threading.Thread(target=previous)
    try:
        streams[0].pull(int(round(e.bar_seconds * e.sr)))
        assert entered.wait(5)
        worker.start()
        assert not done.wait(.05)
        release.set()
        assert done.wait(5)
        worker.join(5)
        assert not errors
        streams[0].pull(128)
        assert (p.now.playlist_name, p.now.playlist_index, p.now.bar) == ("Evening", 0, 0)
    finally:
        release.set()
        if worker.ident is not None:
            worker.join(5)
        p.close()


def test_previous_additive_wire_and_old_defaults():
    import json
    from tests.test_audio_diagnostics import Connection, base_state

    p = Player(engine())
    class Recording(LocalConnection):
        def send_bytes(self, raw):
            self.request = json.loads(raw)
            super().send_bytes(raw)
    conn = Recording(p)
    client = PlayerClient(conn)
    client.previous(SONGS, start_index=1, name="Evening")
    assert conn.request == {"cmd": "previous", "styles": [s.to_dict() for s in SONGS], "start_index": 1, "name": "Evening"}
    assert client.style == SONGS[0] and client.playlist_index == 0 and not client.continuous
    client.previous()
    assert conn.request == {"cmd": "previous"} and client.playlist_index == 2
    old = PlayerClient(Connection(base_state()))
    assert old.continuous is False and old.playlist_name is None and old.playlist_index is None
    assert old.now.playlist_name is None and old.now.playlist_index is None
    for request in ({"cmd": "previous", "styles": {}}, {"cmd": "previous", "styles": [{"bpm": "bad"}]}):
        before = daemon.state(p)
        result = daemon.handle(p, request)
        assert result["ok"] is False and result["error"].startswith("bad request:")
        assert daemon.state(p) == before
