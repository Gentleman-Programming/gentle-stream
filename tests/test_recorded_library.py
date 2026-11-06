"""The real recorded library over the real FluidR3_GM (skipped unless `setup-samples` installed both)."""
import time

import numpy as np
import pytest

from gentle_stream import genres, recorded, samples
from gentle_stream.engine import SR, Engine
from gentle_stream.style import Style

REAL = samples.soundfont_path()
FLUID = samples.load_renderer(REAL) if REAL is not None else None
LIBRARY = recorded.load_library(sr=SR) if recorded.installed(samples.cache_dir(), recorded.load_manifest()) else None
pytestmark = pytest.mark.skipif(FLUID is None or LIBRARY is None,
                                reason="FluidR3_GM, FluidSynth or the recorded library not installed (setup-samples)")
MANIFEST = recorded.load_manifest()
BARS = 8


def _engine(genre: str, seed: int) -> Engine:
    s = Style(genre=genre, drums="busy", energy=0.9, lead_density=0.9)
    return Engine(s, seed=seed, samples=REAL)


@pytest.mark.parametrize("name", genres.NAMES)
def test_every_genre_renders_cleanly_deterministically_and_fast_with_the_recorded_library(name):
    e = _engine(name, 31)
    assert isinstance(e._sampled.r, recorded.Layered)
    t0 = time.perf_counter()
    bars = [e.render_bar() for _ in range(BARS)]
    elapsed = time.perf_counter() - t0
    x = np.concatenate(bars)
    assert np.all(np.isfinite(x)) and np.max(np.abs(x)) < 1.0
    assert 20 * np.log10(np.sqrt(np.mean(x ** 2))) > -40                      # audible
    assert len(x) / SR / elapsed > 10, f"{name}: only {len(x) / SR / elapsed:.1f}x real time"
    again = _engine(name, 31)
    assert np.array_equal(x, np.concatenate([again.render_bar() for _ in range(BARS)]))


def _layered():
    return recorded.Layered(FLUID, LIBRARY)


@pytest.mark.parametrize("group", MANIFEST["instruments"], ids=lambda g: g["name"])
def test_every_recorded_instrument_resolves_and_sits_near_its_fluidr3_level(group):
    lay = _layered()
    rec = samples.SampleInstruments(lay, np.random.default_rng(0))
    fluid = samples.SampleInstruments(FLUID, np.random.default_rng(0))
    for bank, program in group["gm"]:
        sound = LIBRARY.sound(0, bank, program, 60)
        assert sound is not None and sound.name == group["name"]
        key = lay.reference_key(0, bank, program)
        p = samples.Program(program, bank, tail=0.5, rms=0.2)
        for vel in (0.5, 0.8, 1.0):
            a, b = rec.play(p, key, 1.0, vel), fluid.play(p, key, 1.0, vel)
            assert np.all(np.isfinite(a)) and len(a) > 0.2 * SR
            assert np.max(np.abs(a)) < 2 * np.max(np.abs(b)) + 0.5             # level-matched, never wild
            ratio = samples._rms(a, SR) / samples._rms(b, SR)
            assert 0.35 < ratio < 2.8, f"{group['name']} GM {program} vel {vel}: {ratio:.2f}x FluidR3"
        roots = sorted({z["root"] for z in group["zones"]})
        for k in (roots[0], roots[-1]):                                         # the whole recorded range sounds
            x = rec.play(p, k, 0.5, 0.8)
            assert np.all(np.isfinite(x)) and samples._rms(x, SR) > 0.01


@pytest.mark.parametrize("group", MANIFEST["drums"], ids=lambda g: g["name"])
def test_every_recorded_drum_resolves_and_sits_near_its_fluidr3_level(group):
    rec = samples.SampleInstruments(_layered(), np.random.default_rng(0))
    fluid = samples.SampleInstruments(FLUID, np.random.default_rng(0))
    for kit, key in group["gm"]:
        k = 0 if kit == "*" else kit
        assert LIBRARY.sound(samples.Renderer.DRUMS, 128, k, key).name == group["name"]
        h = samples.Hit(k, key, 100, 0.5, rms=0.2)
        for g in (0.6, 1.0):
            a, b = rec.hit(h, g), fluid.hit(h, g)
            assert np.all(np.isfinite(a)) and np.max(np.abs(a)) < 3 * np.max(np.abs(b)) + 1   # clicks peak high
            ratio = samples._rms(a, SR) / samples._rms(b, SR)
            assert 0.35 < ratio < 2.8, f"{group['name']} {kit}:{key} g {g}: {ratio:.2f}x FluidR3"


def test_takes_vary_a_repeated_recorded_note_and_stay_deterministic():
    rec = samples.SampleInstruments(_layered(), np.random.default_rng(0))
    h = samples.Hit(16, 38, 110, 0.4, rms=0.2)
    hits = [rec.hit(h, g) for g in np.linspace(0.9, 1.0, 12)]
    assert len({x.tobytes() for x in hits}) > len({round(g * 20) for g in np.linspace(0.9, 1.0, 12)})
    again = samples.SampleInstruments(_layered(), np.random.default_rng(1))
    assert all(np.array_equal(x, again.hit(h, g)) for x, g in zip(hits, np.linspace(0.9, 1.0, 12)))


SUSTAINED = [g for g in MANIFEST["instruments"] if g.get("sustain")]


def test_pads_strings_organs_accordion_and_winds_are_marked_sustained():
    names = {g["name"] for g in SUSTAINED}
    assert {"drawbar-organ", "church-organ", "accordion", "strings", "violin", "cello", "contrabass", "tuba",
            "brass-section", "tenor-sax"} <= names
    assert not names & {"grand-piano", "nylon-guitar", "harp", "vibraphone", "timpani", "upright-bass"}


@pytest.mark.parametrize("group", SUSTAINED, ids=lambda g: g["name"])
def test_a_held_sustained_note_outlasts_its_recording(group):
    lay = _layered()
    bank, program = group["gm"][0]
    key = lay.reference_key(0, bank, program)
    x = lay.note(0, bank, program, key, 100, 16.0, 0.3)                       # longer than any recording
    assert len(x) >= int(16.0 * SR) and np.all(np.isfinite(x))
    early = samples._rms(x[int(0.5 * SR):int(2.5 * SR)], SR)
    for a in (6.0, 10.0, 14.0):
        assert samples._rms(x[int(a * SR):int((a + 1.5) * SR)], SR) > 0.3 * early, f"{group['name']} at {a} s"
