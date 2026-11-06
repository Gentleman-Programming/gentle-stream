"""The manifest builder's pure parts (SFZ parsing, zone mapping, thinning, licence filter) and the committed
manifest it generated."""
from pathlib import Path

import pytest

from gentle_stream import recorded
from tools import build_recorded_manifest as build

FIXTURES = Path(__file__).parent / "fixtures" / "recorded"


def tiny_regions():
    return build.parse_sfz((FIXTURES / "tiny.sfz").read_text(), "Keys",
                           include=lambda p: (FIXTURES / p).read_text())


# ---------------------------------------------------------------- SFZ
@pytest.mark.parametrize("value, key", [("60", 60), ("c4", 60), ("C4", 60), ("c#4", 61), ("Db2", 37), ("b3", 59),
                                        ("bb3", 58), ("a0", 21), ("c-1", 0), (64, 64)])
def test_note_number_reads_numbers_and_note_names_with_middle_c_as_c4(value, key):
    assert build.note_number(value) == key


def test_note_number_rejects_what_is_not_a_key():
    with pytest.raises(ValueError):
        build.note_number("h4")


def test_parse_sfz_merges_inherited_opcodes_and_resolves_sample_paths():
    regions = tiny_regions()
    assert len(regions) == 7
    first = regions[1]
    assert first["path"] == "Keys/Piano/Sus/C4 soft rr1.wav"                 # default_path, spaces, `\` -> `/`
    assert first["ampeg_release"] == "0.4" and first["volume"] == "-2"         # #define and <global>
    assert first["lovel"] == "0" and first["seq_length"] == "2"                # <group>
    assert regions[3]["path"] == "Keys/Piano/Sus/E4, soft.wav" and regions[3]["volume"] == "3.5"
    assert regions[5]["path"] == "Keys/Piano/Rel/C4 rel.wav"                    # `..` normalised
    assert regions[6]["path"] == "Keys/Piano/Sus/E4 loud.wav"                   # from the #include
    assert "locc64" not in regions[1] and regions[0]["locc64"] == "64"         # a new <group> resets the old one


def test_zones_keep_note_on_regions_with_the_pedal_up_and_read_their_mapping():
    zones = build.zones_from(tiny_regions())
    by = {z["path"].rsplit("/", 1)[-1]: z for z in zones}
    assert sorted(by) == ["C4 loud.wav", "C4 soft rr1.wav", "C4 soft rr2.wav", "E4 loud.wav", "E4, soft.wav"]
    soft = by["C4 soft rr1.wav"]
    assert (soft["root"], soft["lokey"], soft["hikey"], soft["lovel"], soft["hivel"]) == (60, 59, 61, 1, 63)
    assert (soft["offset"], soft["volume"], soft["tune"]) == (120, -2.0, -12)
    assert soft["order"][0] == 1 and by["C4 soft rr2.wav"]["order"][0] == 2
    assert (by["E4, soft.wav"]["root"], by["E4, soft.wav"]["lokey"], by["E4, soft.wav"]["hikey"]) == (64, 64, 64)
    assert by["C4 loud.wav"]["loop"] == [100, 900] and "loop" not in soft
    assert by["E4 loud.wav"]["root"] == 52                                     # transpose=12 plays it an octave up
    assert build.zones_from(tiny_regions(), keep=lambda r: "loud" in r["sample"]) == [
        by["C4 loud.wav"], by["E4 loud.wav"]]


def z(root, lovel=1, hivel=127, order=1, lokey=None, hikey=None):
    return {"path": f"{root}-{lovel}-{order}.wav", "root": root, "lokey": root if lokey is None else lokey,
            "hikey": root if hikey is None else hikey, "lovel": lovel, "hivel": hivel, "offset": 0, "volume": 0.0,
            "tune": 0, "order": (order, 0.0, f"{root}-{lovel}-{order}")}


def test_thin_keeps_spread_velocity_layers_and_the_first_takes_with_no_velocity_gap():
    layers = [(1, 40), (41, 80), (81, 100), (101, 127)]
    zones = [z(60, lo, hi, order) for lo, hi in layers for order in (1, 2, 3)]
    out = build.thin(zones, layers=3, takes=2)
    assert [(o["lovel"], o["hivel"], o["take"]) for o in out] == [
        (1, 40, 0), (1, 40, 1), (41, 100, 0), (41, 100, 1), (101, 127, 0), (101, 127, 1)]
    assert {o["path"] for o in out if o["take"] == 1} == {"60-1-2.wav", "60-81-2.wav", "60-101-2.wav"}
    one = build.thin(zones, layers=1, takes=1)
    assert [(o["lovel"], o["hivel"], o["path"]) for o in one] == [(1, 127, "60-101-1.wav")]


def test_thin_every_keeps_one_note_in_n_and_widens_its_key_range_halfway():
    zones = [z(r, lokey=r - 1, hikey=r + 1) for r in (48, 50, 52, 54, 56)]
    out = build.thin(zones, every=2)
    assert [(o["root"], o["lokey"], o["hikey"]) for o in out] == [(48, 47, 50), (52, 51, 54), (56, 55, 57)]


def test_window_splits_an_instrument_between_sources_by_root():
    zones = [z(50, lokey=40, hikey=55), z(60, lokey=56, hikey=70)]
    assert [(o["root"], o["lokey"], o["hikey"]) for o in build.window(zones, 0, 54)] == [(50, 40, 54)]
    assert [(o["root"], o["lokey"], o["hikey"]) for o in build.window(zones, 55, 127)] == [(60, 56, 70)]


# ---------------------------------------------------------------- licences
@pytest.mark.parametrize("name, licence", [
    ("license-cc0.txt", "CC0-1.0"), ("readme-cc0.txt", "CC0-1.0"), ("license-pd.txt", "public-domain"),
    ("license-cc-by.txt", None), ("license-cc-by-nc.txt", None), ("license-gpl.txt", None),
    ("license-unknown.txt", None)])
def test_licence_filter_accepts_only_cc0_and_public_domain(name, licence):
    assert build.license_of((FIXTURES / name).read_text()) == licence


# ---------------------------------------------------------------- the committed manifest
M = recorded.load_manifest()
PROGRAMS = {tuple(gm) for g in M["instruments"] for gm in g["gm"]}
DRUMS = {tuple(gm) for g in M["drums"] for gm in g["gm"]}


def test_every_source_is_pinned_and_cc0_or_public_domain():
    assert M["sources"]
    for s in M["sources"].values():
        assert s["license"] in recorded.LICENSES
        assert len(s["ref"]) == 40 or s["ref"].startswith(("v", "sha256:"))   # commit, release tag or archive hash
    used = {zn["source"] for kind in ("instruments", "drums") for g in M[kind] for zn in g["zones"]}
    assert used == set(M["sources"])


def test_the_acoustic_voices_every_genre_plays_are_recorded():
    played = {0, 11, 16, 17, 21, 23, 24, 26, 27, 29, 30, 32, 33, 34, 40, 41, 42, 46, 47, 48, 49, 56, 57, 58, 60, 61, 66}
    assert {(0, p) for p in played} <= PROGRAMS
    assert {(k, key) for k in (0, 8, 16) for key in (37, 38, 40, 42, 44, 46, 49, 56)} <= DRUMS
    assert {(16, 45), (48, 36), (48, 38), (48, 49)} <= DRUMS
    assert {("*", key) for key in (62, 63, 64, 69, 70, 73, 74)} <= DRUMS


def test_what_has_no_cc0_recording_stays_on_fluidr3():
    assert not {(0, p) for p in (4, 8, 52, 53, 54, 65, 91)} & PROGRAMS        # rhodes, celesta, choir, alto sax
    assert all(isinstance(k, int) and k in (0, 8, 16, 48) or k == "*" for k, _ in DRUMS)
    assert not {(k, key) for k in (0, 8, 16) for key in (35, 36, 51)} & DRUMS   # no CC0 kit kick or ride
    assert (0, 41) not in DRUMS                                                # the orchestral taiko
    assert not {("*", key) for key in (65, 66)} & DRUMS                        # timbales


def test_zone_urls_are_url_encoded():
    for kind in ("instruments", "drums"):
        for g in M[kind]:
            for zn in g["zones"]:
                assert " " not in zn["url"] and "," not in zn["url"] and "#" not in zn["url"]
