import json
from dataclasses import replace

import pytest

from gentle_stream.presets import NAME_MAX, Preset, PresetError, PresetStore
from gentle_stream.style import Style


@pytest.fixture
def store(tmp_path):
    from gentle_stream.playlist_library import PlaylistLibrary
    return PlaylistLibrary(tmp_path / "playlist-library.json")


def test_missing_library_and_default_path(monkeypatch, tmp_path, store):
    from gentle_stream.playlist_library import PlaylistLibrary, default_path
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_path() == tmp_path / "gentle-stream" / "playlist-library.json"
    assert PlaylistLibrary().path == default_path()
    assert store.list() == [] and store.list_songs("missing") == []
    assert store.get("missing", "song") is None
    assert store.error is None and not store.path.exists()


def test_save_reload_and_sorted_snapshots(store):
    from gentle_stream.playlist_library import Playlist, PlaylistLibrary
    song, replaced = store.save("  Night   café ", "  Rain   song ", Style(mood="rain", key="F"))
    assert isinstance(song, Preset) and not replaced
    assert song.name == song.style.name == "Rain song"
    assert song.mood == song.style.mood == "rain" and song.saved_at.endswith("+00:00")
    store.save("Archive", "zebra", Style())
    store.save("Archive", "Alpha", Style())
    loaded = PlaylistLibrary(store.path)
    assert loaded.list() == [Playlist("Archive", tuple(loaded.list_songs("Archive"))), Playlist("Night café", (song,))]
    assert [p.name for p in loaded.list_songs(" ARCHIVE ")] == ["Alpha", "zebra"]
    assert loaded.get("night CAFÉ", "RAIN SONG") == song
    assert json.loads(store.path.read_text())["version"] == 1
    assert list(store.path.parent.iterdir()) == [store.path]


def test_casefold_replacement_is_local_to_playlist(store):
    store.save("Night", "Rain", Style(mood="first"))
    store.save("Other", "Rain", Style(mood="independent"))
    song, replaced = store.save(" NIGHT ", " rain ", Style(mood="replacement"))
    assert replaced and song.name == "rain"
    assert len(store.list()) == 2
    assert len(store.list_songs("Night")) == 1
    assert store.get("Night", "Rain") == song
    assert store.get("Other", "Rain").mood == "independent"
    assert [p.name for p in store.list()] == ["Night", "Other"]


def test_delete_selected_song_keeps_empty_playlist(store):
    store.save("Night", "Rain", Style())
    store.save("Other", "Rain", Style())
    before = store.path.read_bytes()
    assert not store.delete("missing", "Rain")
    assert not store.delete("Night", "missing")
    assert store.path.read_bytes() == before
    assert store.delete(" NIGHT ", " RAIN ")
    assert [p.name for p in store.list()] == ["Night", "Other"]
    assert store.list_songs("Night") == []
    assert store.get("Other", "Rain") is not None


@pytest.mark.parametrize("playlist,name", [("", "ok"), ("  ", "ok"), ("x" * (NAME_MAX + 1), "ok"), ("ok", ""), ("ok", "x" * (NAME_MAX + 1))])
@pytest.mark.parametrize("existing", [False, True])
def test_invalid_save_is_all_or_nothing(store, playlist, name, existing):
    if existing:
        store.save("Existing", "Song", Style())
    before = store.path.read_bytes() if existing else None
    listed = store.list()
    with pytest.raises(PresetError):
        store.save(playlist, name, Style())
    assert store.list() == listed
    assert (store.path.read_bytes() if store.path.exists() else None) == before


def test_non_style_save_rejected_before_new_playlist(store):
    store.save("Existing", "Song", Style())
    before, listed = store.path.read_bytes(), store.list()
    with pytest.raises(PresetError, match="Style"):
        store.save("New", "Song", {"mood": "not a Style"})
    assert store.list() == listed and store.path.read_bytes() == before


@pytest.mark.parametrize("raw", ["{broken", "[]", '{"version":2,"playlists":[]}', '{"version":true,"playlists":[]}', '{"version":1,"playlists":{}}', '{"version":1,"playlists":[{"name":"Bad","songs":[{}]}]}', '{"version":1,"playlists":[{"name":"Bad","songs":[]},{"name":"bad","songs":[]}]}'])
def test_corrupt_or_unknown_library_is_read_only(store, raw):
    store.path.write_text(raw)
    assert store.list() == []
    assert store.error and str(store.path) in store.error
    with pytest.raises(PresetError):
        store.save("New", "Song", Style())
    with pytest.raises(PresetError):
        store.delete("Bad", "Song")
    assert store.path.read_text() == raw


@pytest.mark.parametrize("legacy", [b'{"presets":[{"name":"Old","style":{}}]}', b"{corrupt legacy"])
def test_legacy_bytes_never_touched(tmp_path, monkeypatch, legacy):
    from gentle_stream.playlist_library import PlaylistLibrary
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    old = PresetStore().path
    old.parent.mkdir(parents=True)
    old.write_bytes(legacy)
    store = PlaylistLibrary()
    assert store.list() == []
    store.save("New", "Song", Style())
    store.delete("New", "Song")
    assert old.read_bytes() == legacy


def test_saved_and_loaded_styles_validate_music_and_ignore_unknown_fields(store):
    lines = ("Keep moving onward", "Take another step", "Trust your own rhythm")
    style = replace(Style(mood="instrumental"), bpm=999, key="invalid", energy=-5)
    saved, _ = store.save("Night", "Rain", style)
    assert (saved.style.bpm, saved.style.key, saved.style.energy) == (140, "D", 0)
    assert "voice_lines" not in saved.style.to_dict()
    data = json.loads(store.path.read_text())
    entry = data["playlists"][0]["songs"][0]
    entry["style"].update(bpm=999, key="invalid", voice_lines=lines)
    store.path.write_text(json.dumps(data))
    assert store.get("Night", "Rain").style == saved.style
    instrumental = Style.from_dict({"mood": "agrega frases", "voice_lines": lines})
    store.save("Night", "Legacy words", instrumental)
    loaded = store.get("Night", "Legacy words").style
    assert loaded.mood == "agrega frases" and "voice_lines" not in loaded.to_dict()


def test_failed_atomic_replace_keeps_previous_bytes(store, monkeypatch):
    import gentle_stream.playlist_library as module
    store.save("Night", "Rain", Style())
    before, listed = store.path.read_bytes(), store.list()
    def fail(*args):
        raise OSError("simulated replace failure")
    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(PresetError, match="simulated replace failure"):
        store.save("New", "Song", Style())
    assert store.path.read_bytes() == before and store.list() == listed
    assert list(store.path.parent.iterdir()) == [store.path]
