"""Panic uses fake outputs only; no device, provider or saved-library access."""
import threading

import numpy as np
import pytest

from gentle_stream.client import PlayerClient, PlayerError
from gentle_stream.engine import Engine
from gentle_stream.player import Player
from gentle_stream.style import Style
from tests.test_continuous_playback import LocalConnection, SONGS
from tests.test_player import FakeStream, wait_for


class Output(FakeStream):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.aborted = threading.Event()
        self.closed = False

    def abort(self):
        self.aborted.set()
        self.active = False

    def stop(self):
        pytest.fail("Panic must abort, not drain buffered output")

    def close(self):
        self.closed = True
        self.active = False


@pytest.fixture
def session():
    streams = []
    def factory(**kw):
        streams.append(Output(**kw))
        return streams[-1]
    p = Player(Engine(SONGS[0], seed=42, sr=16000, samples=False),
               stream_factory=factory, prebuffer=.01, volume=.37)
    p.set_playlist(SONGS, start_index=1, name="Evening", continuous=True)
    p.start()
    yield p, streams
    # Ordinary shutdown is not part of the panic abort assertion.
    for stream in streams:
        stream.stop = lambda: None
    p.close()


class ObservedLock:
    """Report an acquisition attempt, without changing the underlying lock ordering."""
    def __init__(self, lock, attempted):
        self.lock, self.attempted = lock, attempted

    def __enter__(self):
        if threading.current_thread().name == "state-reader":
            self.attempted.set()
        self.lock.acquire()
        return self

    def __exit__(self, *args):
        self.lock.release()


def test_state_poll_cannot_delay_panic_cut_during_render(session, monkeypatch):
    p, streams = session
    old = streams[0]
    old.pull(512)
    audible = p.now
    p.pause()
    entered, release, polling = threading.Event(), threading.Event(), threading.Event()
    render = p.engine.render_bar
    def blocked():
        entered.set()
        assert release.wait(5), "render gate was not released"
        return render()
    monkeypatch.setattr(p.engine, "render_bar", blocked)
    p._render_lock = ObservedLock(p._render_lock, polling)
    # Consume the remainder while playing, then hold paused state across restart.
    p.play()
    old.pull(int(round(p.engine.bar_seconds * p.engine.sr)))
    assert entered.wait(5)
    p.pause()
    counters = (p.underruns, p.output_underflows)
    errors, states = [], []
    def run(action):
        try:
            action()
        except Exception as error:
            errors.append(error)
    reader = threading.Thread(target=run, args=(lambda: states.append(p.playback_state()),), name="state-reader")
    panic = threading.Thread(target=run, args=(p.panic,))
    reader.start()
    try:
        assert polling.wait(2), "reader never attempted the occupied render lock"
        panic.start()
        assert old.aborted.wait(1), "state polling must not hold up the immediate owned output cut"
        assert not release.is_set() and not np.any(old.pull(128))
    finally:
        release.set()
        reader.join(5)
        if panic.ident is not None:
            panic.join(5)
    assert not reader.is_alive() and not panic.is_alive() and not errors
    assert states and len(streams) == 2 and old.closed
    assert not p.playing and p.volume == .37 and p.position == 0
    assert (p.underruns, p.output_underflows) == counters
    assert p.audio_output_error is None
    assert p.now.style == audible.style and p.engine.seed == audible.track.seed
    assert p.now.piece == audible.piece and p.now.bar == 0
    assert p.continuous and p.playlist_name == "Evening" and p.playlist_index == 1
    assert not np.any(streams[-1].pull(128))
    p.next()
    assert p.playlist_index == 2  # original playlist remains usable


def test_panic_aborts_before_waiting_for_render_and_rejects_old_callbacks(session, monkeypatch):
    p, streams = session
    old = streams[0]
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    render = p.engine.render_bar
    def blocked():
        entered.set()
        assert release.wait(5)
        return render()
    monkeypatch.setattr(p.engine, "render_bar", blocked)
    old.pull(int(round(p.engine.bar_seconds * p.engine.sr)))
    assert entered.wait(5)
    errors = []
    def panic():
        try:
            p.panic()
        except Exception as error:
            errors.append(error)
        finally:
            done.set()
    worker = threading.Thread(target=panic)
    worker.start()
    try:
        assert old.aborted.wait(1), "output must cut before the in-flight bar finishes"
        assert not done.is_set()
        assert not np.any(old.pull(128))
        release.set()
        assert done.wait(5)
        assert not errors
        assert old.closed and len(streams) == 2 and streams[1].active
        before = (p.position, p.now, p.buffered_seconds, p.underruns, p.output_underflows)
        assert not np.any(old.pull(512))
        assert (p.position, p.now, p.buffered_seconds, p.underruns, p.output_underflows) == before
        assert p.position == 0 and p.now.bar == 0 and p.level == 0
        assert np.any(streams[1].pull(512))
        assert p.now.bar == 0 and p.underruns == 0
    finally:
        release.set()
        worker.join(5)


@pytest.mark.parametrize("paused,volume", [(False, .37), (True, 0)])
def test_panic_replays_current_track_not_lookahead_and_keeps_playlist_without_ai(session, paused, volume, monkeypatch):
    p, streams = session
    stream = streams[0]
    frames = int(round(p.engine.bar_seconds * p.engine.sr))
    first = stream.pull(512)
    audible = p.now
    # The producer has crossed into the next track; the audible track is still Two.
    from gentle_stream.composer import FIRST_FORM
    stream.pull(frames - 512)
    for _ in range(sum(n for _, n in FIRST_FORM) - 2):
        wait_for(lambda: p.buffered_seconds >= frames / p.engine.sr)
        stream.pull(frames)
    wait_for(lambda: p.buffered_seconds >= frames / p.engine.sr)
    stream.pull(frames - 80)
    wait_for(lambda: p.engine.playlist_index == 2)
    assert p.now.playlist_index == 1 and p.playlist_index == 2
    if paused:
        p.pause()
    p.set_volume(volume)
    monkeypatch.setattr(p.engine, "prepare_playlist", lambda *a, **k: pytest.fail("restart must reuse snapshot"))
    client = PlayerClient(LocalConnection(p))
    client.panic()
    assert client.playing is not paused and client.volume == volume
    assert client.now.style == SONGS[1] and client.now.piece == audible.piece and client.now.bar == 0
    assert client.continuous and client.playlist_name == "Evening" and client.playlist_index == 1
    if paused:
        held = p.position
        assert not np.any(streams[-1].pull(512)) and p.position == held
        client.play()
    client.set_volume(.37)
    np.testing.assert_array_equal(streams[-1].pull(512), first)
    # The resident queue survives and Next still advances exactly once.
    client.next()
    streams[-1].pull(128)
    client.spectrum(8)
    assert client.now.style == SONGS[2] and client.now.playlist_index == 2
    client.detach()


@pytest.mark.parametrize("stage", ["open", "start"])
def test_reopen_failure_is_visible_stopped_and_can_retry(session, stage):
    p, streams = session
    streams[0].pull(512)
    client = PlayerClient(LocalConnection(p))
    factory = p._factory
    def failed(**kw):
        if stage == "open":
            raise OSError("device [busy]")
        stream = factory(**kw)
        def fail_start():
            raise OSError("device [busy]")
        stream.start = fail_start
        return stream
    p._factory = failed
    with pytest.raises(PlayerError, match=r"Panic restart failed: device \[busy\]"):
        client.panic()
    assert not client.playing and not p.playing and all(not s.active for s in streams)
    assert client.now.style == SONGS[1] and client.playlist_index == 1 and client.volume == .37
    with pytest.raises(PlayerError, match="use Panic to retry"):
        client.play()
    p._factory = factory
    client.panic()
    assert not client.playing  # recovery does not silently resume a stopped session
    client.play()
    assert np.any(streams[-1].pull(512))
    client.detach()


def test_panic_uses_owned_sink_pause_when_abort_is_unavailable(session):
    p, streams = session
    streams[0].pull(512)
    old = streams[0]
    old.abort = None
    def cut():
        old.aborted.set()
        old.active = False
    old.pause = cut
    p.panic()
    assert old.aborted.is_set() and old.closed and not old.active
    assert streams[-1].active and p.now.style == SONGS[1] and p.now.bar == 0
    assert not np.any(old.pull(128))


def test_render_controls_and_snapshot_preserve_transport(session):
    p, streams = session
    streams[0].pull(512)
    p.pause()
    for action in (lambda: p.set_playlist(SONGS, start_index=1, name="Evening", continuous=True),
                   lambda: p.set_continuous(False), p.next, p.previous,
                   lambda: p.set_style(SONGS[0])):
        transport = (p.playing, p.volume, p.position, p.underruns, p.output_underflows)
        action()
        state = p.playback_state()
        assert state["style"] == p.style.to_dict()
        assert state["now"]["style"] == p.now.style.to_dict()
        assert state["continuous"] == p.continuous
        assert state["playlist_name"] == p.playlist_name
        assert state["playlist_index"] == p.playlist_index
        assert (p.playing, p.volume, p.position, p.underruns, p.output_underflows) == transport
    assert p.playlist_name is None and not p.continuous


def test_staged_style_cannot_commit_across_busy_render_or_panic(session):
    p, _ = session
    with p.stage_style(SONGS[0]) as commit:
        with p._render_lock:
            assert commit() is False
        p.panic()
        before = p.playback_state()
        counters = (p.position, p.underruns, p.output_underflows)
        assert commit() is None
        assert p.playback_state() == before
        assert (p.position, p.underruns, p.output_underflows) == counters


def test_existing_shared_controls_keep_their_behavior(session):
    p, streams = session
    p.start()  # idempotent, not another producer/output
    assert len(streams) == 1
    p.pause()
    p.play()
    p.toggle()
    assert not p.playing
    p.toggle()
    assert p.playing
    p.set_volume(2)
    assert p.volume == 1
    p.set_playlist(SONGS, start_index=0, name="Preserved", continuous=True)
    p.next()
    streams[0].pull(128)
    assert p.now.style == SONGS[1]
    p.previous()
    streams[0].pull(128)
    assert p.now.style == SONGS[0]
    p.set_continuous(False)
    assert not p.continuous and p.playlist_name == "Preserved"
    p.set_style(SONGS[2])
    p.next()
    streams[0].pull(128)
    assert p.now.style == SONGS[2] and p.playlist_index is None


def test_duplicate_panic_and_volume_serialize_without_leaving_old_streams(session):
    p, streams = session
    streams[0].pull(512)
    entered, release = threading.Event(), threading.Event()
    abort = streams[0].abort
    def gated_abort():
        abort()
        entered.set()
        assert release.wait(5)
    streams[0].abort = gated_abort
    errors = []
    def run(action):
        try:
            action()
        except Exception as error:
            errors.append(error)
    first = threading.Thread(target=run, args=(p.panic,))
    second = threading.Thread(target=run, args=(p.panic,))
    volume = threading.Thread(target=run, args=(lambda: p.set_volume(.51),))
    first.start()
    try:
        assert entered.wait(2)
        second.start()
        volume.start()
        assert p.volume == .37 and len(streams) == 1
    finally:
        release.set()
        for worker in (first, second, volume):
            if worker.ident is not None:
                worker.join(5)
                assert not worker.is_alive()
    assert not errors and len(streams) == 3
    assert [s.active for s in streams] == [False, False, True]
    assert p.volume == .51 and p.now.style == SONGS[1] and p.playlist_index == 1
    before = (p.now, p.position, p.buffered_seconds)
    assert all(not np.any(s.pull(128)) for s in streams[:-1])
    assert (p.now, p.position, p.buffered_seconds) == before


def test_unlisted_current_style_replays_muted_instruments_without_preparation(monkeypatch):
    style = Style(name="Muted lead", lead="none", drums="none")
    streams = []
    p = Player(Engine(style, seed=9, sr=16000, samples=False), prebuffer=.01,
               stream_factory=lambda **kw: streams.append(Output(**kw)) or streams[-1])
    p.start()
    try:
        before = streams[0].pull(512)
        monkeypatch.setattr(p.engine, "prepare_playlist", lambda *a, **k: pytest.fail("restart must reuse track"))
        p.panic()
        assert p.now.style == style and p.playlist_index is None and not p.continuous
        np.testing.assert_array_equal(streams[-1].pull(512), before)
        p.next()  # prior same-mood variation remains available
        streams[-1].pull(128)
        assert p.now.piece == 2 and p.now.style.name == style.name
    finally:
        for stream in streams:
            stream.stop = lambda: None
        p.close()
