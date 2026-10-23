import json

import pytest

from gentle_stream.presets import NAME_MAX, PresetError, PresetStore, default_path, normalize_name
from gentle_stream.style import Style

RAIN = Style(name="Midnight Rain", mood="rainy night in a small café", key="F", mode="dorian", bpm=81.0,
             progression=(1, 4, 1, 4), chords="rhodes", lead="nylon", drums="soft", energy=0.3)


@pytest.fixture
def path(tmp_path):
    return tmp_path / "config" / "gentle-stream" / "presets.json"


def test_the_default_path_follows_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_path() == tmp_path / "gentle-stream" / "presets.json"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert default_path().parts[-3:] == (".config", "gentle-stream", "presets.json")


def test_a_missing_file_is_an_empty_store(path):
    store = PresetStore(path)
    assert store.list() == []
    assert store.error is None
    assert not path.exists()


def test_save_get_list_and_delete(path):
    store = PresetStore(path)
    preset, replaced = store.save("Rainy café", RAIN)
    assert not replaced
    assert preset.name == "Rainy café" and preset.mood == RAIN.mood
    assert preset.style.name == "Rainy café"          # the preset name is the track name
    assert preset.style.to_dict() == {**RAIN.to_dict(), "name": "Rainy café"}
    assert preset.saved_at
    store.save("blue hour", Style(mood="dusk"))
    store.save("Archive", Style())
    assert [p.name for p in store.list()] == ["Archive", "blue hour", "Rainy café"]
    assert store.get("rainy CAFÉ").name == "Rainy café"
    assert store.get("nope") is None
    assert store.delete("BLUE HOUR")
    assert not store.delete("blue hour")
    assert [p.name for p in store.list()] == ["Archive", "Rainy café"]


def test_saving_the_same_name_ignoring_case_replaces_it(path):
    store = PresetStore(path)
    store.save("Rainy Café", RAIN)
    preset, replaced = store.save("  rainy   café ", Style(mood="other"))
    assert replaced
    assert [p.name for p in store.list()] == ["rainy café"]
    assert store.get("RAINY CAFÉ").mood == "other"


def test_presets_persist_across_store_instances(path):
    PresetStore(path).save("Rainy café", RAIN)
    preset = PresetStore(path).get("rainy café")
    assert preset.style == Style.from_dict({**RAIN.to_dict(), "name": "Rainy café"})
    assert preset.mood == RAIN.mood


def test_the_file_is_written_atomically_as_readable_json(path):
    store = PresetStore(path)
    store.save("Rainy café", RAIN)
    data = json.loads(path.read_text(encoding="utf-8"))
    (entry,) = data["presets"]
    assert entry["name"] == "Rainy café"
    assert entry["mood"] == RAIN.mood
    assert entry["style"]["key"] == "F" and entry["style"]["progression"] == [1, 4, 1, 4]
    assert isinstance(entry["saved_at"], str)
    assert [p.name for p in path.parent.iterdir()] == ["presets.json"]   # no temp file left behind


def test_a_corrupt_file_does_not_crash_and_is_left_untouched(path):
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    store = PresetStore(path)
    assert store.list() == []
    assert store.error and "presets.json" in store.error
    with pytest.raises(PresetError):
        store.save("Rainy café", RAIN)
    with pytest.raises(PresetError):
        store.delete("Rainy café")
    assert path.read_text(encoding="utf-8") == "{not json"


def test_a_file_with_the_wrong_shape_is_reported_as_corrupt(path):
    path.parent.mkdir(parents=True)
    path.write_text("[1, 2]", encoding="utf-8")
    store = PresetStore(path)
    assert store.list() == []
    assert store.error


def test_invalid_entries_are_skipped_and_styles_are_validated(path):
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"presets": [
        "junk",
        {"name": "", "mood": "x", "style": {}},
        {"name": "no style", "mood": "x"},
        {"name": "Loud", "mood": "storm", "style": {"bpm": 999, "key": "nope", "drums": "busy"},
         "saved_at": "2026-10-23T10:00:00+00:00"},
    ]}), encoding="utf-8")
    store = PresetStore(path)
    (preset,) = store.list()
    assert preset.name == "Loud" and preset.mood == "storm"
    assert (preset.style.bpm, preset.style.key, preset.style.drums) == (140, "D", "busy")
    assert preset.style.name == "Loud" and preset.style.mood == "storm"
    assert store.error and "3" in store.error


@pytest.mark.parametrize("raw, expected", [("  Rainy   café ", "Rainy café"), ("x" * NAME_MAX, "x" * NAME_MAX)])
def test_names_are_trimmed(raw, expected):
    assert normalize_name(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "x" * (NAME_MAX + 1)])
def test_invalid_names_are_rejected(path, raw):
    with pytest.raises(PresetError):
        normalize_name(raw)
    with pytest.raises(PresetError):
        PresetStore(path).save(raw, RAIN)
    assert not path.exists()
