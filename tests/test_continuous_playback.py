"""Playlist controls are runtime snapshots, not a storage or UI polling feature."""
import json
import threading
from dataclasses import replace

import numpy as np
import pytest

from gentle_stream import daemon
from gentle_stream.client import PlayerClient, PlayerError
from gentle_stream.composer import FIRST_FORM
from gentle_stream.engine import Engine
from gentle_stream.player import Player
from gentle_stream.style import Style
from tests.test_audio_diagnostics import Connection, base_state
from tests.test_player import FakeStream, wait_for

FORM_BARS = sum(n for _, n in FIRST_FORM)
SONGS = tuple(Style(name=n, bpm=140, vinyl=0) for n in ("One", "Two", "Three"))


def engine():
    return Engine(SONGS[0], seed=42, sr=16000, samples=False)


def test_selected_song_complete_forms_order_and_wrap():
    e = engine()
    e.set_playlist(SONGS, start_index=1, name="Evening", continuous=True)
    for expected in (1, 2, 0, 1):
        for bar in range(FORM_BARS):
            e.render_bar()
            assert (e.style, e.playlist_index, e.bar) == (SONGS[expected], expected, bar + 1)
        assert e.section == "release"
    assert e.playlist_name == "Evening" and e.continuous


def test_default_off_disable_no_cut_and_manual_style_exits():
    e = engine()
    assert not e.continuous
    e.set_playlist(SONGS, start_index=2, name="Evening")
    for _ in range(FORM_BARS + 1):
        e.render_bar()
    assert e.style == SONGS[2] and e.bar == FORM_BARS + 1
    e.set_continuous(True)
    e.render_bar()
    assert e.playlist_index == 0 and e.bar == 1
    before = (e.piece, e.bar, e.samples_out, e.style)
    e.set_continuous(False)
    assert (e.piece, e.bar, e.samples_out, e.style) == before
    e.render_bar()
    assert e.piece == before[0] and e.bar == before[1] + 1
    e.queue_style(Style(name="Manual"))
    e.render_bar()
    assert not e.continuous and e.playlist_name is None and e.playlist_index is None
    assert e.style.name == "Manual"
    e.queue_next()
    e.render_bar()
    assert e.style.name == "Manual" and e.bar == 1


@pytest.mark.parametrize("styles,index", [((), 0), (SONGS, -1), (SONGS, 3), (SONGS, True),
                                         (SONGS, 0.5), ((object(),), 0),
                                         ((replace(SONGS[0], bpm=float('nan')),), 0),
                                         ((replace(SONGS[0], key="invalid"),), 0)])
def test_rejection_is_atomic(styles, index):
    e = engine()
    e.set_playlist(SONGS, start_index=1, name="Good", continuous=True)
    e.render_bar()
    before = (e.style, e.piece, e.bar, e.samples_out, e.playlist_name, e.playlist_index, e.continuous)
    with pytest.raises((ValueError, TypeError)):
        e.set_playlist(styles, start_index=index, name="Bad", continuous=True)
    assert (e.style, e.piece, e.bar, e.samples_out, e.playlist_name, e.playlist_index, e.continuous) == before
    e.queue_next()
    e.render_bar()
    assert e.style == SONGS[2]


def test_all_styles_validated_before_replacing_queue():
    e = engine()
    e.set_playlist(SONGS, start_index=1, name="Good", continuous=True)
    e.render_bar()
    invalid = replace(SONGS[0], key="invalid")
    p = Player(e)
    before = daemon.state(p)
    counters = (e.samples_out, e.piece, e.bar, p.position)
    with pytest.raises(ValueError, match="playlist style must contain valid, normalized Style fields"):
        p.set_playlist((SONGS[0], invalid), name="Bad", continuous=True)
    assert daemon.state(p) == before
    assert (e.samples_out, e.piece, e.bar, p.position) == counters
    e.queue_next()
    e.render_bar()
    assert e.style == SONGS[2]


def test_advance_uses_validated_snapshot_without_repreparing(monkeypatch):
    e = engine()
    e.set_playlist(SONGS[:2], name="Evening", continuous=True)
    monkeypatch.setattr(e, "prepare_playlist", lambda *a, **k: pytest.fail("navigation must reuse snapshot"))
    for _ in range(FORM_BARS + 1):
        e.render_bar()
    assert e.style == SONGS[1] and e.playlist_index == 1
    e.queue_next()
    e.render_bar()
    assert e.style == SONGS[0] and e.playlist_index == 0


class LocalConnection:
    """JSON transport through the public request handler, without a socket/device."""
    def __init__(self, player):
        self.player = player
    def send_bytes(self, raw):
        self.answer = daemon.handle(self.player, json.loads(raw))
    def recv_bytes(self):
        return json.dumps(self.answer).encode()
    def close(self):
        pass


def test_client_detach_reconnect_next_pause_disable_and_audible_state():
    e = engine()
    p = Player(e, prebuffer=0)
    stream = FakeStream(callback=p._callback)
    first = PlayerClient(LocalConnection(p))
    first.pause()
    first.set_playlist(SONGS, start_index=1, name="Evening", continuous=True)
    assert not p.playing and p.continuous
    # Explicit Next is public and renders without a running device or producer.
    first.next()
    assert p.style == SONGS[2]  # selected Two -> Three, exactly once
    assert p.now.style == SONGS[0] and p.now.playlist_index is None
    stream.pull(128)
    assert p.position == 0  # paused, no time/poll driven advance
    first.detach()
    second = PlayerClient(LocalConnection(p))
    assert second.continuous and second.playlist_name == "Evening"
    second.play()
    stream.pull(128)
    second.request("state")
    second.spectrum(8)
    assert second.now.style == SONGS[2] and second.now.playlist_index == 2
    assert second.now.playlist_name == "Evening"
    before = (p.position, p.now, p.buffered_seconds, e.piece, e.bar)
    second.set_continuous(False)
    assert not second.continuous
    assert (p.position, p.now, p.buffered_seconds, e.piece, e.bar) == before
    second.set_style(Style(name="Manual"))
    assert p.playlist_name is None and not p.continuous
    second.detach()


@pytest.mark.parametrize("styles,index", [((), 0), (SONGS, -1), (SONGS, True),
                                         ((replace(SONGS[0], bpm=float('nan')),), 0),
                                         ((object(),), 0)])
def test_client_rejections_leave_active_audio_and_sequence_unchanged(styles, index):
    p = Player(engine())
    client = PlayerClient(LocalConnection(p))
    client.set_playlist(SONGS, start_index=1, name="Good", continuous=True)
    client.pause()
    before = daemon.state(p)
    counters = (p.engine.samples_out, p.engine.piece, p.engine.bar, p.position)
    with pytest.raises((ValueError, PlayerError)):
        client.set_playlist(styles, start_index=index, name="Bad", continuous=True)
    assert daemon.state(p) == before
    assert (p.engine.samples_out, p.engine.piece, p.engine.bar, p.position) == counters
    client.next()
    assert p.style == SONGS[2] and not p.playing
    client.detach()


def test_disable_does_not_change_buffered_pcm_or_restart_song():
    a, b = Player(engine()), Player(engine())
    for p in (a, b):
        p.set_playlist(SONGS, name="Good", continuous=True)
        p.next()
    left, right = FakeStream(callback=a._callback), FakeStream(callback=b._callback)
    np.testing.assert_array_equal(left.pull(512), right.pull(512))
    before = a.now
    a.set_continuous(False)
    for _ in range(5):
        np.testing.assert_array_equal(left.pull(512), right.pull(512))
    assert a.now == before and a.position == b.position and a.engine.piece == b.engine.piece


def test_old_daemon_defaults_and_strict_wire_rejection():
    old = PlayerClient(Connection(base_state()))
    assert not old.continuous and old.playlist_name is None and old.playlist_index is None
    assert old.now.playlist_name is None and old.now.playlist_index is None
    p = Player(engine())
    for request in ({"cmd": "set_playlist", "styles": [], "start_index": 0},
                    {"cmd": "set_playlist", "styles": [SONGS[0].to_dict()], "start_index": -1},
                    {"cmd": "set_playlist", "styles": [{"bpm": "bad"}]},
                    {"cmd": "set_continuous", "enabled": "yes"}):
        before = daemon.state(p)
        response = daemon.handle(p, request)
        assert response["ok"] is False and response["error"].startswith("bad request:")
        assert daemon.state(p) == before


def test_off_mode_seeded_audio_and_original_controls_identical():
    a, b = engine(), engine()
    b.set_continuous(False)
    for control in (None, "next", "style"):
        if control == "next":
            a.queue_next(); b.queue_next()
        if control == "style":
            a.queue_style(SONGS[1]); b.queue_style(SONGS[1])
        np.testing.assert_array_equal(a.render_bar(), b.render_bar())
        assert (a.piece, a.bar, a.style) == (b.piece, b.bar, b.style)


def test_background_producer_follows_consumed_audio_not_polling():
    e = engine()
    streams = []
    p = Player(e, stream_factory=lambda **kw: streams.append(FakeStream(**kw)) or streams[-1], prebuffer=.01)
    p.set_playlist(SONGS, start_index=1, name="Evening", continuous=True)
    p.start()
    try:
        stream = streams[0]
        bar_frames = int(round(e.bar_seconds * e.sr))
        for index in (1, 2, 0):
            for bar in range(FORM_BARS):
                wait_for(lambda: p.buffered_seconds >= bar_frames / e.sr)
                stream.pull(bar_frames)
                assert (p.now.playlist_index, p.now.bar) == (index, bar)
        p.pause()
        stream.pull(1024)  # finish the normal gain fade
        held = (p.position, p.now)
        for _ in range(5):
            stream.pull(1024)
        assert (p.position, p.now) == held
    finally:
        p.close()
    assert not p._producer.is_alive()


def test_next_at_prebuffered_form_boundary_does_not_skip_and_presses_count():
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
        wait_for(lambda: e.playlist_index == 1)
        state = daemon.state(p)
        assert state["now"]["playlist_index"] == 0 and state["playlist_index"] == 1
        assert state["now"]["style"]["name"] == "One" and state["style"]["name"] == "Two"
        p.next()
        assert p.style == SONGS[1]  # do not skip the already rendered next entry
        p.next()
        assert p.style == SONGS[2]
        p.next()
        assert p.style == SONGS[0]
        stream.pull(128)
        assert p.now.playlist_index == 0 and p.now.bar == 0
    finally:
        p.close()


def test_playlist_controls_serialize_with_inflight_producer(monkeypatch):
    e = engine()
    streams = []
    p = Player(e, stream_factory=lambda **kw: streams.append(FakeStream(**kw)) or streams[-1], prebuffer=.01)
    p.set_playlist(SONGS, name="Before", continuous=True)
    p.start()
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    render = e.render_bar
    def gated_render():
        entered.set()
        assert release.wait(5)
        return render()
    monkeypatch.setattr(e, "render_bar", gated_render)
    worker_errors = []
    def controls():
        try:
            p.set_playlist(SONGS, start_index=1, name="After", continuous=True)
            p.next()
        except Exception as error:
            worker_errors.append(error)
        finally:
            done.set()
    worker = threading.Thread(target=controls)
    try:
        streams[0].pull(int(round(e.bar_seconds * e.sr)))
        assert entered.wait(5)
        worker.start()
        assert not done.wait(.05)
        assert p.playlist_name == "Before"
        release.set()
        assert done.wait(5)
        worker.join(5)
        assert not worker_errors
        streams[0].pull(128)
        assert (p.now.playlist_name, p.now.playlist_index, p.now.bar) == ("After", 2, 0)
    finally:
        release.set()
        if worker.ident is not None:
            worker.join(5)
        p.close()
