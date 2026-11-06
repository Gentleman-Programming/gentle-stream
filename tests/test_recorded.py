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
    lambda m: m["instruments"][0]["zones"][0].update(loop=[500, 100]),
    lambda m: m["instruments"][0]["zones"][0].update(loop=[0]),
    lambda m: m["instruments"][0]["zones"][0].update(offset=-1),
    lambda m: m["instruments"][0]["zones"][0].update(tune=5000),
    lambda m: m["instruments"][0]["zones"][0].update(volume="loud"),
    lambda m: m["instruments"][0]["zones"][0].update(member=7),
    lambda m: m["instruments"][0].update(sustain="yes"),
    lambda m: m["drums"][0].update(sustain=True),                          # a one-shot never sustains
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
    seen = []
    monkeypatch.setattr(recorded, "install", lambda cache, manifest, **kw: seen.append((cache, manifest)) or cache)
    samples.setup_recorded()
    assert seen == [(tmp_path / "gentle-stream", None)]                        # None: the committed manifest
    empty = {"sources": {}, "instruments": [], "drums": []}
    monkeypatch.undo()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    root = samples.setup_recorded(manifest=empty, fetch=lambda *a: pytest.fail("nothing to download"))
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


# ---------------------------------------------------------------- SFZ zone parameters
def rms(x):
    return float(np.sqrt(np.mean(np.square(x))))


def one_zone(info, tail=0.0, **extra):
    return {"sources": {k: dict(v) for k, v in SOURCES.items()},
            "instruments": [{"name": "inst", "gm": [[0, 0]], "tail": tail, "zones": [{**zone(info), **extra}]}],
            "drums": []}


def render(tmp_path, m, key=60, vel=127, hold=1.0, sr=SR):
    cache = tmp_path / f"cache{len(list(tmp_path.glob('cache*')))}"
    recorded.install(cache, m)
    return recorded.Layered(Fake(sr), recorded.load_library(cache, sr)).note(0, 0, 0, key, vel, hold, 0.0)


def test_archive_members_are_extracted_and_each_archive_is_downloaded_once(tmp_path):
    import io
    import tarfile
    import zipfile
    up = tmp_path / "upstream"
    up.mkdir()
    wav = {}
    for m in (60, 67):
        buf = io.BytesIO()
        soundfile.write(buf, tone(m), SR, format="WAV", subtype="PCM_16")
        wav[m] = buf.getvalue()
    zpath, tpath = up / "bank.zip", up / "bank.tar.xz"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("bank/samples/C4.wav", wav[60])
        z.writestr("bank/samples/G4.wav", wav[67])
    with tarfile.open(tpath, "w:xz") as t:
        info = tarfile.TarInfo("./bank/G4.wav")
        info.size = len(wav[67])
        t.addfile(info, io.BytesIO(wav[67]))
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    arch = {"url": zpath.as_uri(), "sha256": sha(zpath), "source": "test"}
    tar = {"url": tpath.as_uri(), "sha256": sha(tpath), "source": "test"}
    m = {"sources": {k: dict(v) for k, v in SOURCES.items()}, "drums": [], "instruments": [
        {"name": "zip", "gm": [[0, 0]], "tail": 0.0, "zones": [
            {**zone(arch, root=60, hikey=63), "member": "bank/samples/C4.wav"},
            {**zone(arch, root=67, lokey=64), "member": "bank/samples/G4.wav"}]},
        {"name": "tar", "gm": [[0, 1]], "tail": 0.0, "zones": [{**zone(tar, root=67), "member": "bank/G4.wav"}]}]}
    fetched = []

    def fetch(url, sha256, work):
        fetched.append(url)
        return recorded._fetch(url, sha256, work)

    root = recorded.install(tmp_path / "cache", m, fetch=fetch)
    assert sorted(fetched) == sorted([arch["url"], tar["url"]])                # one download per archive
    lay = recorded.Layered(Fake(), recorded.load_library(tmp_path / "cache", SR))
    assert peak_hz(lay.note(0, 0, 0, 60, 100, 1.0, 0.0)) == pytest.approx(mtof(60), rel=0.01)
    assert peak_hz(lay.note(0, 0, 0, 67, 100, 1.0, 0.0)) == pytest.approx(mtof(67), rel=0.01)
    assert peak_hz(lay.note(0, 0, 1, 67, 100, 1.0, 0.0)) == pytest.approx(mtof(67), rel=0.01)
    lines = (root / "SOURCES.txt").read_text()
    assert "bank/samples/G4.wav" in lines and "bank/G4.wav" in lines


def test_a_member_missing_from_the_archive_is_a_setup_error_and_leaves_nothing(tmp_path, files):
    import zipfile
    zpath = tmp_path / "upstream" / "bank.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("other.wav", b"x")
    arch = {"url": zpath.as_uri(), "sha256": hashlib.sha256(zpath.read_bytes()).hexdigest(), "source": "test"}
    with pytest.raises(samples.SetupError):
        recorded.install(tmp_path / "cache", one_zone(arch, member="bank/C4.wav"))
    assert not (tmp_path / "cache").exists()


def test_offset_skips_the_start_of_the_recording(tmp_path):
    lead = 0.8 * np.ones(int(0.25 * SR))                                       # a click/noise before the note
    info = write(tmp_path / "offset.wav", np.concatenate([lead, tone(60, 1.0)]))
    x = render(tmp_path, one_zone(info, offset=len(lead)), hold=0.5)
    y = render(tmp_path, one_zone(info), hold=0.5)
    plain = render(tmp_path, one_zone(write(tmp_path / "plain.wav", tone(60, 1.0))), hold=0.5)
    assert np.min(y[:100]) > 0.7                                                # without the offset: the lead-in
    assert np.allclose(x[:SR // 4], plain[:SR // 4], atol=2e-3)


def test_volume_scales_the_zone_and_quiet_sources_keep_their_resolution(tmp_path):
    quiet = write(tmp_path / "quiet.wav", tone(60, 1.0, amp=0.5 * 10 ** (-48 / 20)))
    loud = write(tmp_path / "loud.wav", tone(60, 1.0))
    a = render(tmp_path, one_zone(quiet, volume=48.0), hold=0.5)
    b = render(tmp_path, one_zone(loud), hold=0.5)
    assert rms(a) == pytest.approx(rms(b), rel=0.02)
    assert np.max(np.abs(a - b)) < 0.01                                        # not 16-bit steps of -48 dB audio
    c = render(tmp_path, one_zone(loud, volume=-6.0), hold=0.5)
    assert rms(c) == pytest.approx(rms(b) * 10 ** (-6 / 20), rel=0.02)


@pytest.mark.parametrize("tune", [100, -50])
def test_tune_shifts_the_pitch_in_cents(tmp_path, tune):
    x = render(tmp_path, one_zone(write(tmp_path / "t.wav", tone(60, 2.0)), tune=tune), hold=1.5)
    assert peak_hz(x) == pytest.approx(mtof(60) * 2 ** (tune / 1200), rel=0.005)


def test_a_looped_recording_sustains_a_note_held_longer_than_it(tmp_path):
    t = np.arange(int(0.5 * SR)) / SR
    x = 0.5 * np.sin(2 * np.pi * 260 * t) * np.minimum(1, t / 0.02)
    info = write(tmp_path / "loop.wav", x)
    loop = [int(0.1 * SR), int(0.4 * SR)]                                       # 78 whole cycles, as SFZ loops are
    held = render(tmp_path, one_zone(info, tail=0.3, loop=loop), hold=2.0)
    assert len(held) == int(2.3 * SR)
    late = held[int(1.5 * SR):int(1.9 * SR)]
    assert rms(late) == pytest.approx(rms(x[loop[0]:loop[1]]), rel=0.1)
    assert np.max(np.abs(np.diff(held))) < 2 * np.max(np.abs(np.diff(x))) + 0.01   # no click at the loop seams
    assert rms(held[-int(0.05 * SR):]) < 0.2 * rms(late)                      # the tail fades out
    unlooped = render(tmp_path, one_zone(info, tail=0.3), hold=2.0)
    assert len(unlooped) <= len(x) + 1                                          # without a loop: the recording's end


def test_loop_points_follow_the_offset_and_the_sample_rate(tmp_path):
    t = np.arange(int(0.5 * 48000)) / 48000
    x = 0.5 * np.sin(2 * np.pi * mtof(60) * t)
    info = write(tmp_path / "loop48.wav", np.concatenate([np.zeros(4800), x]), sr=48000)
    held = render(tmp_path, one_zone(info, offset=4800, loop=[4800 + 4800, 4800 + 19200]), hold=1.5, sr=22050)
    assert len(held) == int(1.5 * 22050)
    assert peak_hz(held[22050 // 2:], 22050) == pytest.approx(mtof(60), rel=0.01)
    assert rms(held[-11025:]) == pytest.approx(rms(x), rel=0.1)


def bowed(seconds=2.4, steady=2.0):
    """A recorded sustained note with no loop points: a 0.1 s attack, a steady part with a little noise, and the
    player's release from `steady` seconds on."""
    t = np.arange(int(seconds * SR)) / SR
    env = np.minimum(1, t / 0.1) * np.where(t < steady, 1.0, np.exp(-(t - steady) / 0.08))
    noise = np.random.default_rng(3).normal(0, 0.02, len(t))
    return 0.5 * env * (np.sin(2 * np.pi * mtof(60) * t) + noise)


def sustained_instrument(info, sustain=True):
    m = one_zone(info, tail=0.3)
    if sustain:
        m["instruments"][0]["sustain"] = True
    return m


def test_a_sustained_instrument_without_loop_points_holds_its_steady_part_as_long_as_the_note(tmp_path):
    x = bowed()
    info = write(tmp_path / "bowed.wav", x)
    steady = rms(x[int(0.5 * SR):int(1.9 * SR)])
    held = render(tmp_path, sustained_instrument(info), hold=6.0)
    assert len(held) == int(6.3 * SR)
    for a in np.arange(1.0, 5.6, 0.5):                                         # no pumping and no early release
        assert rms(held[int(a * SR):int((a + 0.4) * SR)]) == pytest.approx(steady, rel=0.15), a
    assert np.max(np.abs(np.diff(held))) < 2 * np.max(np.abs(np.diff(x))) + 0.01   # no click at the seams
    assert peak_hz(held[int(3 * SR):int(5 * SR)]) == pytest.approx(mtof(60), rel=0.01)
    assert rms(held[-int(0.05 * SR):]) < 0.2 * steady                          # the tail still fades out
    almost = render(tmp_path, sustained_instrument(info), hold=2.3)             # released just past the steady
    assert rms(almost[int(2.05 * SR):int(2.25 * SR)]) == pytest.approx(steady, rel=0.15)   # part: still holding


def test_without_sustain_a_recording_ends_where_it_ends_and_a_short_note_is_untouched(tmp_path):
    x = bowed()
    info = write(tmp_path / "bowed.wav", x)
    plain = render(tmp_path, sustained_instrument(info, sustain=False), hold=6.0)
    assert len(plain) <= len(x) + 1                                             # a piano or guitar decays
    short = render(tmp_path, sustained_instrument(info), hold=1.0)
    assert np.array_equal(short, render(tmp_path, sustained_instrument(info, sustain=False), hold=1.0))


def test_a_recording_too_short_to_find_a_steady_part_is_played_as_recorded(tmp_path):
    x = bowed(seconds=0.5, steady=0.3)
    held = render(tmp_path, sustained_instrument(write(tmp_path / "short.wav", x)), hold=3.0)
    assert len(held) <= len(x) + 1


# ---------------------------------------------------------------- level reference key
class Ranged(Fake):
    """A FluidR3-like preset that is silent outside keys 28-55 (like FluidR3's contrabass at middle C)."""

    def note(self, chan, bank, program, key, vel, hold, tail):
        x = super().note(chan, bank, program, key, vel, hold, tail)
        return x if 28 <= key <= 55 else np.zeros_like(x)


def test_level_is_measured_inside_a_fluidr3_preset_that_is_silent_at_middle_c():
    inst = samples.SampleInstruments(Ranged(), np.random.default_rng(0))
    p = samples.Program(43, rms=0.2)
    level = samples._program_level(inst.r, SR, p)
    assert np.isfinite(level) and level < 100
    assert samples._rms(inst.play(p, 40, 1.0, 0.8), SR) == pytest.approx(0.2, rel=0.25)


def test_level_of_a_recorded_instrument_is_measured_inside_its_recorded_range(tmp_path):
    low = write(tmp_path / "low.wav", tone(36, 2.0))
    m = {"sources": {k: dict(v) for k, v in SOURCES.items()}, "drums": [], "instruments": [
        {"name": "bass", "gm": [[0, 43]], "tail": 0.2, "zones": [zone(low, root=36, lokey=24, hikey=48)]}]}
    recorded.install(tmp_path / "cache", m)
    lay = recorded.Layered(Ranged(), recorded.load_library(tmp_path / "cache", SR))
    assert 24 <= lay.reference_key(0, 0, 43) <= 48
    assert lay.reference_key(0, 0, 0) == 60                                    # not recorded: FluidR3's middle C
    inst = samples.SampleInstruments(lay, np.random.default_rng(0))
    p = samples.Program(43, rms=0.2)
    assert samples._rms(inst.play(p, lay.reference_key(0, 0, 43), 1.0, 0.8), SR) == pytest.approx(0.2, rel=0.1)


def test_velocity_follows_the_sfz_default_curve_over_levelled_layers(tmp_path):
    soft = write(tmp_path / "soft.wav", tone(60, 1.0, amp=0.4))                  # layers recorded (or levelled by
    loud = write(tmp_path / "loud.wav", tone(60, 1.0, amp=0.4))                  # their SFZ volume) to one level
    m = {"sources": {k: dict(v) for k, v in SOURCES.items()}, "drums": [
        {"name": "hit", "gm": [["*", 38]], "zones": [zone(soft, root=38, hivel=63), zone(loud, root=38, lovel=64)]}],
         "instruments": [{"name": "inst", "gm": [[0, 0]], "tail": 0.0,
                          "zones": [zone(soft, hivel=63), zone(loud, lovel=64)]}]}
    recorded.install(tmp_path / "cache", m)
    lay = recorded.Layered(Fake(), recorded.load_library(tmp_path / "cache", SR))
    for chan, prog, key in ((0, 0, 60), (9, 0, 38)):
        level = {v: rms(lay.note(chan, 128 if chan == 9 else 0, prog, key, v, 0.5, 0.0)) for v in (32, 63, 64, 127)}
        assert level[63] / level[127] == pytest.approx((63 / 127) ** 2, rel=0.02)   # no jump between layers
        assert level[64] / level[63] == pytest.approx((64 / 63) ** 2, rel=0.02)
        assert level[32] / level[63] == pytest.approx((32 / 63) ** 2, rel=0.02)
