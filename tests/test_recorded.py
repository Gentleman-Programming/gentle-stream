import hashlib
import json

import numpy as np
import pytest
import soundfile
from scipy.io import wavfile

from gentle_stream import recorded, samples
from gentle_stream.engine import Engine
from gentle_stream.style import Style

SR = 44100
SOURCES = {"test": {"name": "Test Library", "url": "https://example.org/test", "license": "CC0-1.0",
                    "license_url": "https://creativecommons.org/publicdomain/zero/1.0/", "ref": "v1"}}


def mtof(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def tone(m, seconds=2.0, sr=SR, third=0.0, amp=0.5, channels=1):
    """A decaying sine at MIDI note `m`; `third` adds a 3rd harmonic that marks one file apart from another."""
    t = np.arange(int(seconds * sr)) / sr
    f = mtof(m)
    x = amp * np.exp(-t) * (np.sin(2 * np.pi * f * t) + third * np.sin(2 * np.pi * 3 * f * t)) / (1 + third)
    return np.repeat(x[:, None], channels, 1) if channels > 1 else x


def write(path, x, sr=SR, fmt="WAV"):
    soundfile.write(str(path), x, sr, format=fmt, subtype="PCM_16")
    data = path.read_bytes()
    return {"url": path.as_uri(), "sha256": hashlib.sha256(data).hexdigest(), "source": "test"}


def zone(info, root=60, lokey=0, hikey=127, lovel=1, hivel=127, take=0):
    return {**info, "root": root, "lokey": lokey, "hikey": hikey, "lovel": lovel, "hivel": hivel, "take": take}


@pytest.fixture
def files(tmp_path):
    d = tmp_path / "upstream"
    d.mkdir()
    return {
        "soft": write(d / "piano-c4-soft.wav", tone(60, sr=48000, channels=2), sr=48000),       # stereo 48 kHz, converted
        "loud": write(d / "piano-c4-loud.flac", tone(60, third=1.0), fmt="FLAC"),
        "take1": write(d / "piano-c4-loud-2.wav", tone(60, third=0.3)),
        "kick": write(d / "kick.wav", np.exp(-np.arange(int(0.3 * SR)) / 2000) * 0.8),
    }


@pytest.fixture
def manifest(files):
    return {"sources": {k: dict(v) for k, v in SOURCES.items()},
            "instruments": [{"name": "piano", "gm": [[0, 0], [0, 1]], "tail": 0.3, "zones": [
                zone(files["soft"], hivel=63), zone(files["loud"], lovel=64),
                zone(files["take1"], lovel=64, take=1)]}],
            "drums": [{"name": "kick", "gm": [["*", 36]], "zones": [zone(files["kick"], root=36)]}]}


class Fake:
    """A stand-in for `samples.Renderer`: a plain sine per note; records every call."""

    DRUMS = 9

    def __init__(self, sr=SR):
        self.sr, self.calls = sr, []

    def note(self, chan, bank, program, key, vel, hold, tail):
        self.calls.append((chan, bank, program, key))
        t = np.arange(int((hold + tail) * self.sr)) / self.sr
        return 0.3 * vel / 127 * np.sin(2 * np.pi * mtof(key) * t) * np.exp(-3 * t)


def peak_hz(x, sr=SR):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.argmax(spec) * sr / len(x)


def third_ratio(x, root_hz, sr=SR):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    at = lambda f: spec[int(round(f * len(x) / sr))]
    return at(3 * root_hz) / at(root_hz)


@pytest.fixture
def library(tmp_path, manifest):
    recorded.install(tmp_path / "cache", manifest)
    return recorded.load_library(tmp_path / "cache", SR)


# ---------------------------------------------------------------- manifest
def test_shipped_manifest_is_valid_and_only_lists_cc0_or_public_domain_sources():
    m = recorded.load_manifest()
    assert set(m) >= {"sources", "instruments", "drums"}
    assert m["sources"] and all(s["license"] in recorded.LICENSES for s in m["sources"].values())
    assert recorded.LICENSES == {"CC0-1.0", "public-domain"}


@pytest.mark.parametrize("breakage", [
    lambda m: m["sources"]["test"].update(license="CC-BY-4.0"),
    lambda m: m["sources"]["test"].update(license="CC-BY-NC-4.0"),
    lambda m: m["instruments"][0]["zones"][0].update(source="unknown"),
    lambda m: m["instruments"][0]["zones"][0].update(sha256="abc"),
    lambda m: m["instruments"][0]["zones"][0].update(lokey=70, hikey=60),
    lambda m: m["instruments"][0]["zones"][0].update(lovel=0),
    lambda m: m["instruments"][0].update(name="../escape"),
    lambda m: m["instruments"][0].update(gm=[[0, 128]]),
    lambda m: m["drums"][0].update(gm=[["x", 36]]),
    lambda m: m["instruments"][0].update(zones=[]),
    lambda m: m["sources"]["test"].update(ref=""),
    lambda m: m["drums"].append({**m["drums"][0], "name": "kick2"}),       # (kit, key) covered twice
])
def test_invalid_manifest_is_rejected_before_anything_is_downloaded(tmp_path, manifest, breakage):
    breakage(manifest)
    with pytest.raises(recorded.ManifestError):
        recorded.install(tmp_path / "cache", manifest, fetch=lambda *a: pytest.fail("downloaded"))
    assert not (tmp_path / "cache").exists()


# ---------------------------------------------------------------- install
def test_install_verifies_converts_and_writes_sources_and_index(tmp_path, manifest, files):
    seen = []
    root = recorded.install(tmp_path / "cache", manifest, progress=lambda done, total: seen.append((done, total)))
    assert root == tmp_path / "cache" / "recorded"
    wavs = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.wav"))
    assert len(wavs) == 4 and all(w.startswith(("piano/", "kick/")) for w in wavs)
    for w in wavs:
        rate, data = wavfile.read(root / w)
        assert rate == SR and data.dtype == np.int16 and data.ndim == 1
    rate, soft = wavfile.read(root / next(w for w in wavs if "soft" in w))
    assert abs(len(soft) - 2 * SR) <= 2                                       # 48 kHz resampled to 44.1 kHz
    lines = (root / "SOURCES.txt").read_text().splitlines()
    for w in wavs:
        line = next(l for l in lines if l.startswith(w + "\t"))
        info = next(f for f in files.values() if f["sha256"] in line)
        for needle in (info["url"], info["sha256"], "Test Library", "CC0-1.0", SOURCES["test"]["license_url"]):
            assert needle in line
    index = root / "index.json"
    assert json.loads(index.read_text())["instruments"][0]["name"] == "piano"
    assert all(index.stat().st_mtime_ns >= p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file())
    assert seen and seen[-1] == (4, 4)
    assert sorted(p.name for p in (tmp_path / "cache").iterdir()) == ["recorded"]   # no partial left behind


def test_checksum_mismatch_leaves_nothing_and_keeps_the_rest_of_the_cache(tmp_path, manifest):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "FluidR3_GM.sf2").write_bytes(b"sf2")
    manifest["drums"][0]["zones"][0]["sha256"] = "0" * 64
    with pytest.raises(samples.ChecksumError):
        recorded.install(cache, manifest)
    assert sorted(p.name for p in cache.iterdir()) == ["FluidR3_GM.sf2"]
    assert recorded.load_library(cache, SR) is None


def test_a_file_that_cannot_be_decoded_is_a_setup_error_and_leaves_nothing(tmp_path, manifest):
    bad = tmp_path / "upstream" / "noise.bin"
    bad.write_bytes(b"not audio at all" * 100)
    manifest["drums"][0]["zones"][0].update(url=bad.as_uri(), sha256=hashlib.sha256(bad.read_bytes()).hexdigest())
    with pytest.raises(samples.SetupError):
        recorded.install(tmp_path / "cache", manifest)
    assert not (tmp_path / "cache").exists()


def test_install_is_idempotent_and_reinstalls_only_when_the_manifest_changes(tmp_path, manifest, files):
    cache = tmp_path / "cache"
    first = recorded.install(cache, manifest)
    before = (first / "index.json").read_bytes()
    assert recorded.install(cache, manifest, fetch=lambda *a: pytest.fail("downloaded twice")) == first
    assert (first / "index.json").read_bytes() == before
    manifest["drums"] = []
    recorded.install(cache, manifest)
    assert not list((first / "kick").glob("*.wav")) and recorded.load_library(cache, SR).sound(9, 128, 0, 36) is None


def test_setup_recorded_uses_the_user_cache_and_the_shipped_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    root = samples.setup_recorded(fetch=lambda *a: pytest.fail("the shipped manifest lists no files yet"))
    assert root == tmp_path / "gentle-stream" / "recorded" and (root / "index.json").is_file()
    assert recorded.load_library(sr=SR) is None                                # nothing covered: FluidR3 as today


# ---------------------------------------------------------------- runtime
def test_layered_plays_the_recording_for_a_covered_program_and_fluidr3_otherwise(library):
    fake = Fake()
    lay = recorded.Layered(fake, library)
    assert lay.sr == SR
    rec = lay.note(0, 0, 1, 60, 100, 0.5, 1.0)
    assert fake.calls == [] and len(rec) == int(0.8 * SR)                     # hold + the manifest's 0.3 s tail
    other = lay.note(0, 0, 24, 60, 100, 0.5, 1.0)
    assert fake.calls == [(0, 0, 24, 60)] and np.array_equal(other, Fake().note(0, 0, 24, 60, 100, 0.5, 1.0))
    lay.note(0, 8, 0, 60, 100, 0.5, 1.0)                                       # another bank is not covered
    assert fake.calls[-1] == (0, 8, 0, 60)


def test_drums_match_any_kit_play_to_the_end_and_fall_back_for_other_keys(library):
    fake = Fake()
    lay = recorded.Layered(fake, library)
    for kit in (0, 40):
        hit = lay.note(9, 128, kit, 36, 100, 0.05, 2.0)
        assert len(hit) == int(0.3 * SR)                                        # the whole one-shot, not hold + tail
    assert fake.calls == []
    lay.note(9, 128, 0, 38, 100, 0.05, 0.5)
    assert fake.calls == [(9, 128, 0, 38)]


@pytest.mark.parametrize("key", [55, 67, 72])
def test_pitch_follows_the_resampling_ratio(library, key):
    x = recorded.Layered(Fake(), library).note(0, 0, 0, key, 40, 1.0, 0.0)
    assert peak_hz(x) == pytest.approx(mtof(60) * 2 ** ((key - 60) / 12), rel=0.01)


def test_velocity_layers_pick_the_zone_whose_range_holds_the_velocity(library):
    lay = recorded.Layered(Fake(), library)
    soft, loud = lay.note(0, 0, 0, 60, 40, 1.0, 0.0), lay.note(0, 0, 0, 60, 100, 1.0, 0.0, take=0)
    assert third_ratio(soft, mtof(60)) < 0.05 < 0.5 < third_ratio(loud, mtof(60))
    assert lay.takes(0, 0, 0, 60, 40) == 1 and lay.takes(0, 0, 0, 60, 100) == 2 and lay.takes(0, 0, 24, 60, 100) == 0


def test_takes_rotate_and_the_choice_is_deterministic_and_history_independent(library):
    lay = recorded.Layered(Fake(), library)
    t0, t1 = lay.note(0, 0, 0, 60, 100, 0.5, 0.2, take=0), lay.note(0, 0, 0, 60, 100, 0.5, 0.2, take=1)
    assert not np.array_equal(t0, t1)
    assert np.array_equal(lay.note(0, 0, 0, 60, 100, 0.5, 0.2, take=2), t0)
    p = samples.Program(1, velocity=(64, 120))
    a = samples.SampleInstruments(lay, np.random.default_rng(0))
    b = samples.SampleInstruments(lay, np.random.default_rng(0))
    for v in (0.9, 0.95, 0.99):                                                 # b has a different history
        b.play(p, 62, 0.5, v); b.hit(samples.Hit(0, 36), v)
    vels = np.linspace(0.6, 1.0, 40)
    first = [a.play(p, 60, 0.5, v) for v in vels]
    assert all(np.array_equal(x, b.play(p, 60, 0.5, v)) for x, v in zip(first, vels))
    assert len({x.tobytes() for x in first}) > len({round(v * 8) for v in vels})   # takes split the buckets


def test_level_matching_and_caching_still_apply_to_recorded_voices(library):
    lay = recorded.Layered(Fake(), library)
    inst = samples.SampleInstruments(lay, np.random.default_rng(0))
    p = samples.Program(0, rms=0.3)
    ref = inst.play(p, 60, 1.0, 0.8)
    assert samples._rms(ref, SR) == pytest.approx(0.3, rel=0.15)
    assert inst.play(p, 60, 1.0, 0.8) is ref
    assert samples._rms(inst.hit(samples.Hit(0, 36, rms=0.25)), SR) == pytest.approx(0.25, rel=0.01)
    assert samples._rms(inst.piano(60, 1.0, 0.8), SR) == pytest.approx(samples.reference_rms(SR)["piano"], rel=0.15)
    assert recorded.Layered(lay.renderer, library) == lay and hash(recorded.Layered(lay.renderer, library)) == hash(lay)


def test_library_is_resampled_to_the_engine_rate(tmp_path, manifest):
    recorded.install(tmp_path / "cache", manifest)
    lib = recorded.load_library(tmp_path / "cache", 22050)
    x = recorded.Layered(Fake(22050), lib).note(9, 128, 0, 36, 127, 0.05, 0.5)
    assert abs(len(x) - int(0.3 * 22050)) <= 2


def test_no_library_or_an_empty_one_is_none(tmp_path, manifest):
    assert recorded.load_library(tmp_path / "nothing", SR) is None
    recorded.install(tmp_path / "cache", {**manifest, "instruments": [], "drums": []})
    assert recorded.load_library(tmp_path / "cache", SR) is None


# ---------------------------------------------------------------- engine
@pytest.fixture
def fake_backend(tmp_path, monkeypatch):
    """The engine on the `Fake` renderer (no FluidSynth needed), with an XDG cache in `tmp_path`."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    fake = Fake()
    monkeypatch.setattr(samples, "load_renderer", lambda sf2, sr=SR: fake)
    sf2 = tmp_path / "fake.sf2"
    sf2.write_bytes(b"sf2")
    return fake, sf2


def bars(sf2, n=6, **kw):
    e = Engine(style=Style(chords="piano", lead="rhodes", drums="busy"), seed=5, samples=sf2, **kw)
    return e, np.concatenate([e.render_bar() for _ in range(n)])


def test_engine_without_a_library_keeps_the_plain_renderer(fake_backend):
    fake, sf2 = fake_backend
    e, without = bars(sf2)
    assert e._sampled.r is fake and e.backend == "samples"


def test_engine_layers_the_installed_library_and_stays_deterministic(fake_backend, manifest):
    fake, sf2 = fake_backend
    _, without = bars(sf2)
    recorded.install(samples.cache_dir(), manifest)
    e, with_lib = bars(sf2)
    assert isinstance(e._sampled.r, recorded.Layered) and e._sampled.r.renderer is fake
    assert not np.array_equal(with_lib, without)                              # the lo-fi piano (GM 0) is recorded
    assert np.array_equal(bars(sf2)[1], with_lib) and np.max(np.abs(with_lib)) < 1.0


def test_engine_with_a_library_that_covers_nothing_it_plays_renders_exactly_as_before(fake_backend, manifest):
    fake, sf2 = fake_backend
    _, without = bars(sf2)
    for group in manifest["instruments"]:
        group["gm"] = [[8, 120]]
    manifest["drums"] = []
    recorded.install(samples.cache_dir(), manifest)
    e, with_lib = bars(sf2)
    assert isinstance(e._sampled.r, recorded.Layered)
    assert np.array_equal(with_lib, without)
