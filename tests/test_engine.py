import time

import numpy as np
import pytest

from gentle_stream.engine import SR, Engine
from gentle_stream.style import Style


def render(engine, bars):
    return [engine.render_bar() for _ in range(bars)]


def test_bar_shape_and_length_follow_the_tempo():
    e = Engine(Style(bpm=80), seed=1)
    bar = e.render_bar()
    assert bar.dtype == np.float32 and bar.shape == (int(round(3.0 * SR)), 2)


def test_output_is_audible_and_never_clips():
    x = np.concatenate(render(Engine(Style(drums="busy", energy=1.0, brightness=1.0), seed=2), 10))
    assert np.max(np.abs(x)) < 1.0
    rms_db = 20 * np.log10(np.sqrt(np.mean(x[SR:] ** 2)))
    assert -30 < rms_db < -6


def test_same_seed_is_deterministic():
    a = np.concatenate(render(Engine(seed=7), 3))
    b = np.concatenate(render(Engine(seed=7), 3))
    assert np.array_equal(a, b)


def test_bars_join_without_clicks():
    """The step across each bar boundary is no larger than the steps inside the music itself."""
    bars = render(Engine(Style(drums="soft"), seed=3), 8)
    inner = np.concatenate([np.abs(np.diff(b, axis=0)) for b in bars])
    limit = np.max(inner)
    for a, b in zip(bars, bars[1:]):
        assert np.all(np.abs(b[0] - a[-1]) <= limit)


def test_renders_much_faster_than_real_time():
    e = Engine(Style(chords="pad", lead="nylon", drums="busy"), seed=4)
    t0 = time.perf_counter()
    bars = render(e, 6)
    elapsed = time.perf_counter() - t0
    assert elapsed < sum(len(b) for b in bars) / SR / 4


def test_queued_style_starts_a_new_piece_at_the_next_bar():
    e = Engine(Style(name="A", bpm=72), seed=5)
    e.render_bar()
    e.queue_style(Style(name="B", bpm=120))
    assert e.style.name == "A"            # nothing changes mid-bar
    bar = e.render_bar()
    assert e.style.name == "B" and e.piece == 2 and len(bar) == int(round(2.0 * SR))


def test_next_keeps_the_mood_and_changes_the_piece():
    base = Style(name="Rain", mood="rainy night", mode="dorian", energy=0.3, brightness=0.2)
    e = Engine(base, seed=6)
    e.render_bar()
    first_seed = e.seed
    e.queue_next()
    e.render_bar()
    assert e.piece == 2 and e.seed != first_seed
    assert (e.style.mood, e.style.mode, e.style.energy, e.style.brightness) == ("rainy night", "dorian", 0.3, 0.2)


def test_a_queued_style_wins_over_a_queued_next():
    e = Engine(seed=8)
    e.queue_style(Style(name="Chosen"))
    e.queue_next()
    e.render_bar()
    assert e.style.name == "Chosen" and e.piece == 2


@pytest.mark.parametrize("mode", ["minor", "major", "dorian", "phrygian", "lydian", "mixolydian"])
def test_every_mode_and_instrument_renders(mode):
    for chords, lead in (("piano", "rhodes"), ("nylon", "pad"), ("pad", "none")):
        x = np.concatenate(render(Engine(Style(mode=mode, chords=chords, lead=lead), seed=9), 3))
        assert np.all(np.isfinite(x)) and np.max(np.abs(x)) > 0.01
