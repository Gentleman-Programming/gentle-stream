import hashlib
import io
import sys
import tarfile
import time

import numpy as np
import pytest

from gentle_stream import samples
from gentle_stream.engine import SR, Engine, Instruments
from gentle_stream.style import Style

COPYRIGHT = """Upstream Author:

    Frank Wen <getfrank@gmail.com>

Copyright:

    Copyright (c) 2000-2002, 2008 Frank Wen
    Copyright (c) 2008 Toby Smithe

License:

 Permission is hereby granted, free of charge, to any person
 obtaining a copy of this software and associated documentation
 files (the "Software"), to deal in the Software without
 restriction.
"""
SF2 = b"RIFF fake soundfont bytes " * 1000


def _tar_xz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _ar(members: dict[str, bytes]) -> bytes:
    out = b"!<arch>\n"
    for name, data in members.items():
        out += f"{name:<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(data):<10}`\n".encode() + data
        if len(data) % 2:
            out += b"\n"
    return out


def make_deb(path, sf2: bytes | None = SF2, copyright: str = COPYRIGHT):
    """A minimal fluid-soundfont-gm-shaped .deb; returns (file:// url, sha256)."""
    files = {"./usr/share/doc/fluid-soundfont-gm/copyright": copyright.encode()}
    if sf2 is not None:
        files["./usr/share/sounds/sf2/FluidR3_GM.sf2"] = sf2
    data = _ar({"debian-binary": b"2.0\n", "control.tar.xz": _tar_xz({"./control": b"Package: x\n"}),
                "data.tar.xz": _tar_xz(files)})
    path.write_bytes(data)
    return path.as_uri(), hashlib.sha256(data).hexdigest()


@pytest.fixture
def deb(tmp_path):
    return make_deb(tmp_path / "fluid-soundfont-gm_test_all.deb")


def test_setup_downloads_verifies_and_extracts_the_soundfont(tmp_path, deb):
    url, sha = deb
    cache = tmp_path / "cache"
    seen = []
    sf2 = samples.setup_samples(cache, url=url, sha256=sha, progress=lambda done, total: seen.append((done, total)))
    assert sf2 == cache / "FluidR3_GM.sf2" and sf2.read_bytes() == SF2
    assert samples.soundfont_path(cache) == sf2
    assert seen and seen[-1][0] == seen[-1][1]
    assert sorted(p.name for p in cache.iterdir()) == ["FluidR3_GM.sf2", "LICENSES.txt"]


def test_licenses_file_records_source_checksum_holders_and_mit_text(tmp_path, deb):
    url, sha = deb
    cache = tmp_path / "cache"
    samples.setup_samples(cache, url=url, sha256=sha)
    text = (cache / "LICENSES.txt").read_text()
    for needle in (url, sha, "MIT", "Frank Wen", "Toby Smithe", "Permission is hereby granted, free of charge",
                   "FluidR3_GM.sf2"):
        assert needle in text


def test_checksum_mismatch_is_rejected_and_leaves_nothing_behind(tmp_path, deb):
    url, _ = deb
    cache = tmp_path / "cache"
    with pytest.raises(samples.ChecksumError):
        samples.setup_samples(cache, url=url, sha256="0" * 64)
    assert not cache.exists() or not any(cache.iterdir())
    assert samples.soundfont_path(cache) is None


def test_package_without_the_soundfont_is_rejected_and_leaves_nothing_behind(tmp_path):
    url, sha = make_deb(tmp_path / "empty.deb", sf2=None)
    cache = tmp_path / "cache"
    with pytest.raises(samples.SetupError):
        samples.setup_samples(cache, url=url, sha256=sha)
    assert not cache.exists() or not any(cache.iterdir())


def test_setup_is_idempotent_and_never_downloads_twice(tmp_path, deb):
    url, sha = deb
    cache = tmp_path / "cache"
    first = samples.setup_samples(cache, url=url, sha256=sha)
    again = samples.setup_samples(cache, url=(tmp_path / "missing.deb").as_uri(), sha256=sha)
    assert again == first and first.read_bytes() == SF2


def test_default_cache_honours_xdg_and_nothing_is_installed_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert samples.cache_dir() == tmp_path / "gentle-stream"
    assert samples.soundfont_path() is None


def test_pinned_source_is_the_official_debian_package():
    assert samples.DEB_URL.startswith("https://deb.debian.org/debian/pool/main/f/fluid-soundfont/fluid-soundfont-gm_")
    assert len(samples.DEB_SHA256) == 64


# ---------------------------------------------------------------- backend selection
def test_engine_falls_back_to_procedural_without_a_soundfont(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    e = Engine(seed=1)
    assert e.backend == "procedural" and type(e.inst) is Instruments
    assert Engine(seed=1, samples=tmp_path / "nope.sf2").backend == "procedural"
    assert Engine(seed=1, samples=False).backend == "procedural"


def test_engine_falls_back_to_procedural_without_the_fluidsynth_binding(tmp_path, monkeypatch, deb):
    url, sha = deb
    sf2 = samples.setup_samples(tmp_path / "cache", url=url, sha256=sha)
    monkeypatch.setitem(sys.modules, "fluidsynth", None)  # import fluidsynth -> ImportError
    samples.load_renderer.cache_clear()
    try:
        assert samples.load_renderer(sf2) is None
        e = Engine(seed=1, samples=sf2)
        assert e.backend == "procedural"
        assert np.max(np.abs(e.render_bar())) > 0
    finally:
        samples.load_renderer.cache_clear()


# ---------------------------------------------------------------- real soundfont (skipped when not installed)
REAL = samples.soundfont_path()
needs_sf2 = pytest.mark.skipif(REAL is None or samples.load_renderer(REAL) is None,
                               reason="FluidR3_GM or FluidSynth not installed (gentle-stream setup-samples)")


@needs_sf2
def test_engine_uses_samples_when_installed():
    assert Engine(seed=1).backend == "samples"


@needs_sf2
@pytest.mark.parametrize("name", ["piano", "rhodes", "nylon", "pad"])
def test_sampled_tonal_instruments_respect_duration_and_velocity(name):
    inst = samples.SampleInstruments(samples.load_renderer(REAL), np.random.default_rng(0))
    fn = getattr(inst, name)
    loud, soft, long = fn(60, 0.5, 1.0), fn(60, 0.5, 0.4), fn(60, 1.5, 1.0)
    for x in (loud, soft, long):
        assert x.ndim == 1 and x.dtype == np.float64 and np.all(np.isfinite(x))
    assert len(loud) >= 0.5 * SR and len(long) > len(loud)
    assert np.max(np.abs(loud)) > np.max(np.abs(soft)) > 0.001
    assert abs(loud[-1]) < 1e-3                       # ends faded, no click
    assert fn(60, 0.5, 1.0) is loud                   # cached


@needs_sf2
def test_sampled_drums_and_sub_render():
    inst = samples.SampleInstruments(samples.load_renderer(REAL), np.random.default_rng(0))
    for x in (inst.kick(), inst.brush(), inst.rim(), inst.hat(), inst.hat(0.6), inst.sub(36, 0.8)):
        assert x.ndim == 1 and np.all(np.isfinite(x)) and np.max(np.abs(x)) > 0.01


def _engine(**kw):
    return Engine(samples=REAL, **kw)


@needs_sf2
def test_samples_backend_never_clips_and_is_audible():
    e = _engine(style=Style(drums="busy", energy=1.0, brightness=1.0), seed=2)
    x = np.concatenate([e.render_bar() for _ in range(10)])
    assert np.max(np.abs(x)) < 1.0
    assert -30 < 20 * np.log10(np.sqrt(np.mean(x[SR:] ** 2))) < -6


@needs_sf2
def test_samples_backend_is_deterministic_and_seamless():
    e1, e2 = _engine(style=Style(drums="soft"), seed=3), _engine(style=Style(drums="soft"), seed=3)
    bars = [e1.render_bar() for _ in range(8)]
    assert np.array_equal(np.concatenate(bars), np.concatenate([e2.render_bar() for _ in range(8)]))
    limit = np.max(np.concatenate([np.abs(np.diff(b, axis=0)) for b in bars]))
    for x, y in zip(bars, bars[1:]):
        assert np.all(np.abs(y[0] - x[-1]) <= limit)


@needs_sf2
def test_samples_backend_renders_more_than_ten_times_real_time():
    e = _engine(style=Style(chords="piano", lead="rhodes", drums="busy", energy=0.8), seed=4)
    e.render_bar()                                    # soundfont already loaded; warm the note cache a little
    t0 = time.perf_counter()
    bars = [e.render_bar() for _ in range(30)]
    elapsed = time.perf_counter() - t0
    assert sum(len(b) for b in bars) / SR / elapsed > 10


@needs_sf2
@pytest.mark.parametrize("program", [89, 80, 81, 90, 38])
def test_a_note_renders_the_same_whatever_was_rendered_before(program):
    """FluidSynth carries voice state between notes for some presets; every note must be history-free."""
    r = samples.load_renderer(REAL)
    first = r.note(0, 0, program, 64, 90, 0.4, 0.3)
    for other in (81, 0, 38, 90):
        r.note(0, 0, other, 50, 100, 0.3, 0.2)
        r.note(9, 128, 0, 36, 100, 0.05, 0.2)
        again = r.note(0, 0, program, 64, 90, 0.4, 0.3)
        assert np.array_equal(again, first)
