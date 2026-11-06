import threading
import time

import numpy as np
import pytest

from gentle_stream.engine import SR, Engine
from gentle_stream.player import Player
from gentle_stream.style import Style


class FakeStream:
    """Stands in for sounddevice.OutputStream: the test pulls audio by calling the callback."""

    def __init__(self, callback, **kw):
        self.callback, self.kw, self.active = callback, kw, False

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def close(self):
        pass

    def pull(self, frames=1024):
        out = np.full((frames, 2), np.nan, dtype=np.float32)
        self.callback(out, frames, None, None)
        return out


def wait_for(cond, timeout=10.0):
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > timeout:
            raise AssertionError("condition not reached")
        time.sleep(0.01)


@pytest.fixture
def player():
    streams = []

    def factory(**kw):
        streams.append(FakeStream(**kw))
        return streams[-1]

    p = Player(Engine(Style(name="First"), seed=1), stream_factory=factory, prebuffer=0.5)
    p.start()
    wait_for(lambda: p.buffered_seconds >= 0.5)
    yield p, streams[0]
    p.close()


def pull_seconds(stream, seconds):
    return np.concatenate([stream.pull() for _ in range(int(seconds * SR / 1024) + 1)])


def test_opens_a_stereo_float_stream_and_plays_audio(player):
    p, stream = player
    assert stream.active and stream.kw["channels"] == 2 and stream.kw["samplerate"] == SR
    x = pull_seconds(stream, 1.0)
    assert np.all(np.isfinite(x)) and np.max(np.abs(x)) > 0.01
    assert p.playing and p.now.style.name == "First"


def test_keeps_rendering_ahead_of_playback(player):
    p, stream = player
    pull_seconds(stream, 3.0)
    wait_for(lambda: p.buffered_seconds >= 0.5)
    assert p.underruns == 0


def test_pause_fades_to_silence_and_holds_position(player):
    p, stream = player
    pull_seconds(stream, 0.5)
    p.pause()
    pull_seconds(stream, 0.1)                     # the fade-out
    held = p.position
    silent = pull_seconds(stream, 1.0)
    assert not p.playing and np.all(silent == 0) and p.position == held
    p.play()
    assert np.max(np.abs(pull_seconds(stream, 0.5))) > 0.01


def test_toggle_switches_between_play_and_pause(player):
    p, _ = player
    p.toggle()
    assert not p.playing
    p.toggle()
    assert p.playing


def test_next_replaces_pending_audio_with_the_new_piece_right_away(player):
    p, stream = player
    pull_seconds(stream, 0.3)
    p.next()
    assert p._chunks[0].meta.piece == 2 and len(p._chunks) == 1   # old pending bars are gone
    stream.pull()
    assert p.now.piece == 2
    assert p.now.style.name == "First"            # same mood, new piece


def test_next_leaves_no_gap_and_no_jump(player):
    p, stream = player
    pull_seconds(stream, 0.5)
    before = p.underruns
    p.next()
    x = pull_seconds(stream, 0.3)                 # pulled at once, faster than the producer could render
    assert p.underruns == before
    steps = np.abs(np.diff(x, axis=0)).max(axis=1)
    assert steps.max() < 0.05


def test_next_is_click_free(player):
    p, stream = player
    pull_seconds(stream, 0.5)
    before = stream.pull()
    p.next()
    after = stream.pull()
    assert abs(float(after[0, 0]) - float(before[-1, 0])) < 0.05


def test_a_new_style_is_heard_after_the_buffered_audio(player):
    p, stream = player
    p.set_style(Style(name="Second", bpm=110))
    assert p.now.style.name == "First"
    heard = 0.0
    while p.now.style.name == "First":
        assert heard < 10, "the new style was never heard"
        pull_seconds(stream, 0.2)
        heard += 0.2
        time.sleep(0.01)
    assert heard >= 0.5                           # the buffered audio of the old style played first
    assert p.now.style.name == "Second" and p.now.piece == 2


def test_volume_is_clamped_and_scales_the_output(player):
    p, stream = player
    p.set_volume(5)
    assert p.volume == 1.0
    p.set_volume(-1)
    assert p.volume == 0.0
    pull_seconds(stream, 0.1)
    assert np.all(pull_seconds(stream, 0.3) == 0)


def test_level_and_spectrum_follow_the_output(player):
    p, stream = player
    pull_seconds(stream, 1.0)
    assert p.level > 0
    bands = p.spectrum(24)
    assert bands.shape == (24,) and np.all((bands >= 0) & (bands <= 1)) and bands.max() > 0


def test_close_stops_the_stream_and_the_producer(player):
    p, stream = player
    p.close()
    assert not stream.active and not p._producer.is_alive()


def noise_player(color, seed=7):
    """A player whose engine renders broadband noise instead of music, pulled through the fake stream."""
    rng = np.random.default_rng(seed)
    engine = Engine(Style(name="Noise"), seed=1)

    def bar():
        n = SR // 2
        x = rng.standard_normal(n)
        if color == "pink":
            spec = np.fft.rfft(x)
            f = np.fft.rfftfreq(n)
            spec[0] = 0
            spec[1:] /= np.sqrt(f[1:])
            x = np.fft.irfft(spec, n)
        x = 0.1 * x / np.std(x)
        return np.repeat(x[:, None], 2, axis=1).astype(np.float32)

    engine.render_bar = bar
    streams = []
    p = Player(engine, stream_factory=lambda **kw: streams.append(FakeStream(**kw)) or streams[-1], prebuffer=0.5)
    p.start()
    wait_for(lambda: p.buffered_seconds >= 0.5)
    return p, streams[0]


@pytest.mark.parametrize("color", ["white", "pink"])
def test_every_spectrum_band_carries_data_for_broadband_audio(color):
    p, stream = noise_player(color)
    try:
        pull_seconds(stream, 0.5)
        bands = p.spectrum(48)
    finally:
        p.close()
    assert bands.shape == (48,) and np.all(bands <= 1)
    assert np.all(bands > 0.05), f"empty bands: {np.flatnonzero(bands <= 0.05).tolist()}"


def test_spectrum_bands_are_balanced_from_left_to_right():
    p, stream = noise_player("pink")
    try:
        pull_seconds(stream, 0.5)
        bands = p.spectrum(48)
    finally:
        p.close()
    left, right = bands[:24].mean(), bands[24:].mean()
    assert abs(left - right) < 0.08, (left, right)    # pink noise is the reference for music: an even bar
    assert np.abs(np.diff(bands)).mean() < 0.05       # neighbouring columns are similar, no holes or spikes


# ------------------------------------------------------------ output device
@pytest.fixture
def switchable():
    """A started player whose factory records every stream and refuses the device "broken"."""
    streams = []

    def factory(device=None, **kw):
        if device == "broken":
            raise OSError("Device unavailable")
        streams.append(FakeStream(device=device, **kw))
        return streams[-1]

    p = Player(Engine(Style(name="First"), seed=1), stream_factory=factory, prebuffer=0.5)
    p.start()
    wait_for(lambda: p.buffered_seconds >= 0.5)
    yield p, streams
    p.close()


def test_with_no_device_chosen_the_stream_opens_on_the_system_default(player):
    p, stream = player
    assert p.device is None and stream.kw["device"] is None


def test_a_device_chosen_before_start_is_the_one_opened():
    streams = []
    p = Player(Engine(Style(name="First"), seed=1), stream_factory=lambda **kw: streams.append(FakeStream(**kw))
               or streams[-1], prebuffer=0.3, device=4)
    p.set_device(5)                                # not started yet: only remembered
    assert streams == []
    p.start()
    try:
        assert len(streams) == 1 and streams[0].kw["device"] == 5 and p.device == 5
    finally:
        p.close()


def test_set_device_moves_the_music_to_a_new_stream_without_restarting_the_piece(switchable):
    p, streams = switchable
    old = streams[0]
    pull_seconds(old, 0.5)
    now, position = p.now, p.position
    p.set_device(2)
    assert len(streams) == 2 and streams[1].kw["device"] == 2 and p.device == 2
    assert streams[1].active and not old.active
    assert p.buffered_seconds > 0                  # the queued audio was kept
    x = pull_seconds(streams[1], 0.3)
    assert np.max(np.abs(x)) > 0.01 and p.now.piece == now.piece and p.position > position
    assert p.underruns == 0


def test_a_device_that_cannot_be_opened_leaves_the_old_stream_playing(switchable):
    p, streams = switchable
    with pytest.raises(OSError, match="Device unavailable"):
        p.set_device("broken")
    assert len(streams) == 1 and streams[0].active and p.device is None
    assert np.max(np.abs(pull_seconds(streams[0], 0.5))) > 0.01


def test_a_new_stream_that_fails_to_start_brings_the_old_device_back(switchable):
    p, streams = switchable
    real = FakeStream.start

    def start(self):
        if self.kw.get("device") == 9:
            raise OSError("cannot start")
        real(self)

    FakeStream.start = start
    try:
        with pytest.raises(OSError, match="cannot start"):
            p.set_device(9)
    finally:
        FakeStream.start = real
    assert p.device is None and streams[-1].kw["device"] is None and streams[-1].active
    assert np.max(np.abs(pull_seconds(streams[-1], 0.3))) > 0.01


def test_switching_while_closing_never_leaves_a_stream_open(switchable):
    p, streams = switchable
    t = threading.Thread(target=lambda: [p.set_device(i % 3) for i in range(20)])
    t.start()
    p.close()
    t.join(5)
    assert not any(s.active for s in streams)


def test_a_switch_after_the_stream_was_lost_opens_the_new_device(switchable):
    p, streams = switchable
    real = FakeStream.start

    def start(self):
        if self.kw.get("device") == 9:
            raise OSError("cannot start")          # the new device fails to start...
        real(self)

    factory = p._factory

    def gone(device=None, **kw):
        if device is None:
            raise OSError("Device unavailable")    # ...and the old one was unplugged meanwhile
        return factory(device=device, **kw)

    FakeStream.start, p._factory = start, gone
    try:
        with pytest.raises(OSError, match="cannot start"):
            p.set_device(9)
    finally:
        FakeStream.start = real
    assert not any(s.active for s in streams)      # silent now: the error was raised, not swallowed
    p.set_device(2)
    assert streams[-1].kw["device"] == 2 and streams[-1].active and p.device == 2
    assert np.max(np.abs(pull_seconds(streams[-1], 0.3))) > 0.01


def test_concurrent_switches_keep_one_stream_and_exact_audio():
    lock, state = threading.Lock(), {"active": 0, "max": 0}
    pulled, rendered = [], []

    class Threaded(FakeStream):
        def start(self):
            with lock:
                state["active"] += 1
                state["max"] = max(state["max"], state["active"])
            self.active, self._halt = True, threading.Event()

            def run():
                while not self._halt.is_set():
                    self.pull()
                    time.sleep(0.005)

            self._t = threading.Thread(target=run, daemon=True)
            self._t.start()

        def stop(self):
            if self.active:
                self._halt.set()
                self._t.join()
                self.active = False
                with lock:
                    state["active"] -= 1

    streams = []
    p = Player(Engine(Style(name="First"), seed=1), stream_factory=lambda **kw: streams.append(Threaded(**kw))
               or streams[-1], prebuffer=0.5)
    pull, render = p._pull, p.engine.render_bar
    p._pull = lambda n: pulled.append(pull(n)) or pulled[-1]
    p.engine.render_bar = lambda: rendered.append(render()) or rendered[-1]
    p.start()
    ts = [threading.Thread(target=lambda: [p.set_device(i % 3) for i in range(40)]) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    p.close()
    out = np.concatenate(pulled)
    assert state["max"] == 1 and not any(s.active for s in streams)
    assert p.underruns == 0 and np.array_equal(out, np.concatenate(rendered)[:len(out)])
