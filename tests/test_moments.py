"""Moments: finite pieces with an arc and an exact duration, rendered offline to a WAV (epic announcement,
suspense / reveal), on any genre and both backends."""
import hashlib

import numpy as np
import pytest
from scipy.io import wavfile

from gentle_stream import genres, moments, samples
from gentle_stream.engine import SR, Engine
from gentle_stream.style import Style

REAL = samples.soundfont_path()
HAS_SAMPLES = REAL is not None and samples.load_renderer(REAL) is not None
BACKENDS = ["procedural", pytest.param("samples", marks=pytest.mark.skipif(
    not HAS_SAMPLES, reason="FluidR3_GM or FluidSynth not installed (gentle-stream setup-samples)"))]
PEAK = 10 ** (-1 / 20)


@pytest.fixture(autouse=True)
def procedural_backend(tmp_path, monkeypatch):
    """An empty cache: `samples=True` finds no soundfont, so the default backend is procedural."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))


def rms(x):
    return float(np.sqrt(np.mean(np.asarray(x, dtype=float) ** 2)))


def loudest_window(x, sr=SR, seconds=0.5, step=0.05):
    """Start time (seconds) of the loudest `seconds` window, scanned every `step` seconds."""
    w, s = int(seconds * sr), int(step * sr)
    power = np.mean(x.astype(float) ** 2, axis=1)
    csum = np.concatenate([[0.0], np.cumsum(power)])
    starts = np.arange(0, len(x) - w, s)
    return float(starts[int(np.argmax(csum[starts + w] - csum[starts]))] / sr)


def at(x, t0, t1, sr=SR):
    return x[int(t0 * sr):int(t1 * sr)]


# ---------------------------------------------------------------- duration, determinism, level
@pytest.mark.parametrize("kind", moments.KINDS)
@pytest.mark.parametrize("seconds", [10, 30, 47.5])
def test_a_moment_lasts_exactly_the_requested_duration(kind, seconds):
    m = moments.render(kind, seconds, seed=1)
    assert m.audio.shape == (int(round(seconds * SR)), 2) and m.sr == SR
    assert m.backend == "procedural" and m.style.genre == "orchestral"


@pytest.mark.parametrize("kind", moments.KINDS)
def test_a_moment_is_deterministic_per_seed(kind):
    a = moments.render(kind, 12, seed=5).audio
    assert np.array_equal(a, moments.render(kind, 12, seed=5).audio)
    assert not np.array_equal(a, moments.render(kind, 12, seed=6).audio)


@pytest.mark.parametrize("kind", moments.KINDS)
def test_a_moment_peaks_at_minus_one_dbfs_and_ends_in_silence(kind):
    x = moments.render(kind, 20, seed=2).audio
    assert np.all(np.isfinite(x))
    assert np.max(np.abs(x)) == pytest.approx(PEAK, abs=1e-6)       # normalised, never clipped
    assert rms(x[-int(0.05 * SR):]) < 1e-3 and np.all(x[-1] == 0)    # faded to silence


# ---------------------------------------------------------------- plan
def test_the_plan_fits_the_bars_by_tempo_within_the_genre_bounds():
    for seconds in (5, 10, 30, 47.5, 300):
        for kind in moments.KINDS:
            style = moments.default_style(kind)
            p = moments.plan(kind, seconds, style)
            lo, hi = genres.get(style.genre).bpm
            assert lo <= p.bpm <= hi or seconds == 5
            assert sum(s.bars for s in p.sections) == p.end_bar + 1
            assert 0 < p.hit_bar <= p.end_bar
            assert p.end_bar * p.bar_samples < seconds * SR       # the final chord rings inside the duration


def test_the_epic_hit_lands_at_about_two_thirds_of_the_duration():
    p = moments.plan("epic", 30, moments.default_style("epic"))
    assert 0.55 <= p.hit_seconds / 30 <= 0.72
    assert [s.name for s in p.sections][-1] == "release"
    assert p.gap is None


def test_the_suspense_plan_leaves_a_short_gap_right_before_the_reveal():
    p = moments.plan("suspense", 30, moments.default_style("suspense"))
    start, end = p.gap
    assert end == pytest.approx(p.hit_seconds) and 0.3 <= end - start <= 1.0
    assert p.hit_bar == p.end_bar                                  # the reveal is the final chord


def test_default_styles_suit_each_kind():
    epic, suspense = moments.default_style("epic"), moments.default_style("suspense")
    assert epic.genre == suspense.genre == "orchestral"
    assert epic.mode == suspense.mode == "minor"
    assert 110 <= epic.bpm <= 130 and epic.energy >= 0.8 and epic.brightness >= 0.6
    assert suspense.bpm <= 80 and suspense.brightness <= 0.35 and suspense.energy <= 0.4


def test_an_unknown_kind_or_a_duration_out_of_range_is_rejected():
    with pytest.raises(ValueError, match="kind"):
        moments.plan("intro", 30, moments.default_style("epic"))
    for seconds in (4.9, 300.1, float("nan")):
        with pytest.raises(ValueError, match="between 5 and 300 seconds"):
            moments.plan("epic", seconds, moments.default_style("epic"))


# ---------------------------------------------------------------- the arcs
def test_epic_builds_hits_and_rings_out():
    m = moments.render("epic", 30, seed=3)
    x, p = m.audio, m.plan
    bar, hit = p.bar_samples / SR, p.hit_seconds
    early, late = rms(at(x, 1.0, 1.0 + hit / 3)), rms(at(x, hit - hit / 3, hit))
    assert late > 1.5 * early                                      # the build rises
    assert abs(loudest_window(x) - hit) <= bar                     # the hit is the loudest moment
    assert rms(at(x, 30 - 0.3, 30)) < 0.05 * rms(at(x, hit, hit + bar))


def test_suspense_holds_tension_falls_silent_then_reveals():
    m = moments.render("suspense", 30, seed=4)
    x, p = m.audio, m.plan
    bar, reveal = p.bar_samples / SR, p.hit_seconds
    start, end = p.gap
    tension, revealed = rms(at(x, 1.0, start)), rms(at(x, reveal, reveal + bar))
    assert tension < 0.5 * revealed                                # low energy before the reveal
    assert rms(at(x, start + 0.1, end - 0.01)) < 0.02 * revealed   # a near-silent gap right before it
    assert abs(loudest_window(x) - reveal) <= bar                  # the reveal is the loudest moment


# ---------------------------------------------------------------- every genre, both backends
@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("kind", moments.KINDS)
@pytest.mark.parametrize("name", genres.NAMES)
def test_every_genre_renders_both_kinds(name, kind, backend):
    style = moments.default_style(kind, genre=name)
    m = moments.render(kind, 12, style=style, seed=8, samples=REAL if backend == "samples" else False)
    x = m.audio
    assert m.backend == backend and m.style.genre == name
    assert x.shape == (12 * SR, 2) and np.all(np.isfinite(x))
    assert np.max(np.abs(x)) == pytest.approx(PEAK, abs=1e-6)
    assert np.all(x[-1] == 0)
    hit, bar = m.plan.hit_seconds, m.plan.bar_samples / SR
    assert abs(loudest_window(x) - hit) <= bar, f"{name}/{kind}: loudest at {loudest_window(x):.2f}, hit {hit:.2f}"


# ---------------------------------------------------------------- WAV export
def test_write_wav_writes_16_bit_stereo_at_the_engine_rate(tmp_path):
    m = moments.render("epic", 8, seed=9)
    path = moments.write_wav(tmp_path / "out.wav", m)
    sr, data = wavfile.read(path)
    assert sr == SR and data.dtype == np.int16 and data.shape == (8 * SR, 2)
    assert np.max(np.abs(data)) == int(round(PEAK * 32767))
    assert np.allclose(data / 32767, m.audio, atol=1 / 32767)


# ---------------------------------------------------------------- the endless stream is unchanged
# sha256 (first 16 hex digits) of 20 bars, seed 7, procedural, computed with the code before moments existed
# (boombap, bossanova and tango: computed from their modules as committed, which came after moments)
ENDLESS = {
    "lofi": "6c912cdd0ccedc73",
    "ambient": "94c6781f3e5d2a29",
    "chiptune": "b513d57532238a7e",
    "synthwave": "bfa6306b96fd30cf",
    "jazz": "b6266465bd685aef",
    "rock": "37d91c09ea3476cb",
    "electro": "6aaee52c11eec07e",
    "electrohouse": "92d7f6be0d73fb9a",
    "cumbia": "8d55f26f22e30fef",
    "dubstep": "c49caa0e48aae621",
    "orchestral": "500a8a3a9901f31d",
    "boombap": "80c26434f92e3e1d",
    "bossanova": "28bd932e965dad65",
    "tango": "03c1985d4b071c2d",
}


@pytest.mark.parametrize("name", ENDLESS)
def test_the_endless_stream_is_byte_identical_to_before(name):
    e = Engine(Style(genre=name, drums="busy", energy=0.8, lead_density=0.9, bpm=130), seed=7, samples=False)
    h = hashlib.sha256()
    for _ in range(20):
        h.update(e.render_bar().tobytes())
    assert h.hexdigest()[:16] == ENDLESS[name]
