"""The contract every registered genre must meet, on both backends, plus the shared synth helpers genres use."""
import importlib
import time

import numpy as np
import pytest

from gentle_stream import genres, samples, synth
from gentle_stream.engine import SR, Engine
from gentle_stream.genres.base import Genre, Master
from gentle_stream.style import CHORD_INSTRUMENTS, MODES, Style

REAL = samples.soundfont_path()
HAS_SAMPLES = REAL is not None and samples.load_renderer(REAL) is not None
BACKENDS = ["procedural", pytest.param("samples", marks=pytest.mark.skipif(
    not HAS_SAMPLES, reason="FluidR3_GM or FluidSynth not installed (gentle-stream setup-samples)"))]
BARS = 8


def _engine(genre: str, backend: str, seed: int, **style) -> Engine:
    s = Style(genre=genre, **({"drums": "busy", "energy": 0.9, "lead_density": 0.9} | style))
    e = Engine(s, seed=seed, samples=REAL if backend == "samples" else False)
    assert e.backend == backend and e.genre.name == genre
    return e


def _seamless(bars) -> None:
    """Same criterion as tests/test_engine.py: no step across a bar boundary is larger than the music's own."""
    limit = np.max(np.concatenate([np.abs(np.diff(b, axis=0)) for b in bars]))
    for a, b in zip(bars, bars[1:]):
        assert np.all(np.abs(b[0] - a[-1]) <= limit)


# ---------------------------------------------------------------- registry
def test_registry_reserves_every_genre_in_its_own_module():
    assert genres.NAMES == ("lofi", "ambient", "chiptune", "synthwave", "jazz", "rock")
    for name in genres.NAMES:
        g = genres.get(name)
        assert isinstance(g, Genre) and g.name == name
        assert type(g).__module__ == f"gentle_stream.genres.{name}"
        assert importlib.import_module(f"gentle_stream.genres.{name}").GENRE is g
    assert genres.get("polka") is genres.get("lofi")


@pytest.mark.parametrize("name", genres.NAMES)
def test_genre_prompt_hints_are_valid(name):
    g = genres.get(name)
    assert g.description and "\n" not in g.description
    lo, hi = g.bpm
    assert 50 <= lo < hi <= 140
    assert g.modes and set(g.modes) <= set(MODES)
    assert set(g.voices) == set(CHORD_INSTRUMENTS) and all(g.voices.values())


@pytest.mark.parametrize("name", genres.NAMES)
def test_genre_master_settings_are_bounded(name):
    for s in (Style(genre=name), Style(genre=name, space=1, vinyl=1, brightness=1)):
        m = genres.get(name).master(s)
        assert isinstance(m, Master)
        assert 0 <= m.space <= 1 and 0 <= m.vinyl <= 1 and 0 <= m.wow <= 1
        assert m.reverb >= 0 and m.room >= 0 and 200 <= m.tone <= 20000 and m.drive > 0


# ---------------------------------------------------------------- rendering contract
@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("name", genres.NAMES)
def test_genre_renders_cleanly_deterministically_and_fast(name, backend):
    e = _engine(name, backend, seed=31)
    t0 = time.perf_counter()
    bars = [e.render_bar() for _ in range(BARS)]
    elapsed = time.perf_counter() - t0
    x = np.concatenate(bars)
    assert np.all(np.isfinite(x)) and np.max(np.abs(x)) < 1.0 and np.max(np.abs(x)) > 0.01
    _seamless(bars)
    assert len(x) / SR / elapsed > 10, f"{name}/{backend}: only {len(x) / SR / elapsed:.1f}x real time"
    again = _engine(name, backend, seed=31)
    assert np.array_equal(x, np.concatenate([again.render_bar() for _ in range(BARS)]))


@pytest.mark.parametrize("name", genres.NAMES)
def test_genre_renders_quiet_styles_too(name):
    e = _engine(name, "procedural", seed=32, drums="none", lead="none", energy=0.1, vinyl=0, space=0)
    x = np.concatenate([e.render_bar() for _ in range(4)])
    assert np.all(np.isfinite(x)) and np.max(np.abs(x)) < 1.0


@pytest.mark.parametrize("name", genres.NAMES)
def test_switching_genres_joins_seamlessly(name):
    e = _engine("lofi", "procedural", seed=33, drums="soft")
    bars = [e.render_bar() for _ in range(3)]
    e.queue_style(Style(genre=name, drums="soft"))
    bars += [e.render_bar() for _ in range(3)]
    assert e.genre.name == name
    e.queue_style(Style(drums="soft"))
    bars += [e.render_bar() for _ in range(2)]
    assert e.genre.name == "lofi"
    _seamless(bars)


class _CleanGenre(genres.lofi.LofiGenre):
    """A genre with a very different master chain, to check that the engine glides between master settings."""
    name = "clean-test"

    def master(self, s):
        return Master(space=0.1, reverb=0.4, room=0.0, vinyl=0.0, wow=0.0, tone=16000, drive=3.0,
                      threshold=-12.0, ratio=4.0)


def test_master_changes_glide_across_the_switch(monkeypatch):
    monkeypatch.setitem(genres.REGISTRY, "clean-test", _CleanGenre())
    e = _engine("lofi", "procedural", seed=34, drums="soft")
    bars = [e.render_bar() for _ in range(3)]
    e.queue_style(Style(genre="clean-test", drums="soft"))   # bypasses from_dict validation on purpose
    bars += [e.render_bar() for _ in range(3)]
    assert e.genre.name == "clean-test"
    e.queue_style(Style(drums="soft"))
    bars += [e.render_bar() for _ in range(2)]
    _seamless(bars)
    assert np.max(np.abs(np.concatenate(bars))) < 1.0
    # stricter at the two switches: no larger than the music's ordinary (99th percentile) steps
    typical = np.percentile(np.concatenate([np.abs(np.diff(b, axis=0)) for b in bars]), 99)
    for i in (3, 6):
        assert np.max(np.abs(bars[i][0] - bars[i - 1][-1])) < typical


def test_chord_hook_reshapes_the_composers_harmony():
    from gentle_stream.composer import Chord, Composer

    def triads(chord, scale):
        assert len(scale) == 7
        return Chord(chord.degree, chord.tones[:3])

    plain, hooked = Composer(Style(), 1, 50), Composer(Style(), 1, 50, triads)
    for bar in range(12):
        a, b = plain.plan(bar), hooked.plan(bar)
        if a.chord.degree:
            assert b.chord.tones == a.chord.tones[:3]
        assert (a.section, a.energy) == (b.section, b.energy)   # form untouched


@pytest.mark.skipif(not HAS_SAMPLES, reason="FluidR3_GM or FluidSynth not installed (gentle-stream setup-samples)")
def test_sampled_programs_and_hits_for_genres():
    inst = samples.SampleInstruments(samples.load_renderer(REAL), np.random.default_rng(0))
    guitar = samples.Program(30, tail=0.4, rms=0.3)            # GM 30: Distortion Guitar
    loud, soft = inst.play(guitar, 52, 0.5, 1.0), inst.play(guitar, 52, 0.5, 0.3)
    assert np.all(np.isfinite(loud)) and len(loud) >= 0.5 * SR and abs(loud[-1]) < 1e-3
    assert np.max(np.abs(loud)) > np.max(np.abs(soft)) > 0.001
    assert inst.play(guitar, 52, 0.5, 1.0) is loud            # cached
    ref = inst.play(guitar, 60, 1.0, 0.8)
    assert float(np.sqrt(np.mean(ref[:SR // 2] ** 2))) == pytest.approx(0.3, rel=0.25)
    snare = inst.hit(samples.Hit(16, 38, rms=0.2))             # Power kit snare
    assert np.all(np.isfinite(snare)) and np.max(np.abs(snare)) > 0.01
    assert inst.hit(samples.Hit(16, 38, rms=0.2)) is snare


# ---------------------------------------------------------------- shared synth helpers
@pytest.mark.parametrize("shape", ["sine", "saw", "square", "pulse", "triangle"])
def test_oscillator_is_bounded_periodic_and_band_limited(shape):
    f, n = 220.0, SR // 2
    x = synth.osc(shape, f, n, SR, duty=0.25)
    assert x.shape == (n,) and np.all(np.isfinite(x)) and np.max(np.abs(x)) <= 1.6
    assert abs(np.mean(x)) < 0.05                    # no DC, the pulse included
    spec = np.abs(np.fft.rfft(x * np.hanning(n)))
    freqs = np.fft.rfftfreq(n, 1 / SR)
    assert freqs[np.argmax(spec)] == pytest.approx(f, abs=3)
    # polyBLEP keeps aliasing low: little energy above 18 kHz relative to the fundamental
    assert np.max(spec[freqs > 18000]) < 0.05 * np.max(spec)


def test_oscillator_follows_a_frequency_array():
    x = synth.osc("square", np.linspace(100, 400, SR), SR, SR)
    assert np.all(np.isfinite(x)) and len(x) == SR


def test_adsr_shape():
    env = synth.adsr(SR, SR, attack=0.1, decay=0.1, sustain=0.5, release=0.2, hold=0.6)
    t = lambda s: int(s * SR)
    assert env[0] == 0 and env[t(0.1)] == pytest.approx(1, abs=0.01)
    assert env[t(0.4)] == pytest.approx(0.5, abs=0.01)
    assert env[t(0.7)] < 0.5 and env[-1] == pytest.approx(0, abs=1e-3)
    assert np.all((env >= 0) & (env <= 1))


def test_distortion_is_bounded_and_monotonic():
    x = np.linspace(-3, 3, 1001)
    for drive in (1, 5, 30):
        y = synth.distort(x, drive)
        assert np.max(np.abs(y)) <= 1.0 and np.all(np.diff(y) >= 0)
    assert np.max(np.abs(synth.distort(0.1 * np.sin(np.linspace(0, 6, 100)), 20))) > 0.5   # drive adds gain
