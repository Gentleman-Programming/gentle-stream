"""Versioned named playlists, independent of the legacy saved-prompt store."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .presets import Preset, PresetError, _parse, normalize_name
from .style import Style

VERSION = 1


@dataclass(frozen=True)
class Playlist:
    name: str
    songs: tuple[Preset, ...]


def default_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "gentle-stream" / "playlist-library.json"


def _key(name: str) -> str:
    return " ".join(str(name).split()).casefold()


class PlaylistLibrary:
    """Read fresh snapshots on every call; `error` describes the last read failure.

    Bad files are never overwritten. Writes use atomic replacement, not a
    multi-process transaction. No call reads or writes legacy presets.json.
    """

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else default_path()
        self.error: str | None = None

    def _load(self) -> tuple[dict[str, Playlist], bool]:
        self.error = None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}, True
        except (OSError, UnicodeDecodeError, ValueError) as e:
            self.error = f"Cannot read {self.path}: {e}"
            return {}, False
        try:
            if not isinstance(data, dict):
                raise ValueError("unexpected format")
            if type(data.get("version")) is not int or data["version"] != VERSION:
                raise ValueError("unsupported playlist library version")
            if not isinstance(data.get("playlists"), list):
                raise ValueError("expected a playlists list")
            playlists = {}
            for group in data["playlists"]:
                if not isinstance(group, dict) or not isinstance(group.get("name"), str):
                    raise ValueError("invalid playlist")
                name = normalize_name(group["name"])
                if name.casefold() in playlists or not isinstance(group.get("songs"), list):
                    raise ValueError("duplicate playlist name or invalid songs list")
                songs = {}
                for entry in group["songs"]:
                    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                        raise ValueError("invalid saved song")
                    song = _parse(entry)
                    if song is None or song.name.casefold() in songs:
                        raise ValueError("invalid saved song or duplicate song name")
                    songs[song.name.casefold()] = song
                playlists[name.casefold()] = Playlist(
                    name, tuple(sorted(songs.values(), key=lambda p: p.name.casefold()))
                )
            return playlists, True
        except (PresetError, TypeError, ValueError, OverflowError) as e:
            self.error = f"Cannot read {self.path}: {e}"
            return {}, False

    def _writable(self) -> dict[str, Playlist]:
        playlists, ok = self._load()
        if not ok:
            raise PresetError(f"{self.error}; fix or move the file to save songs again.")
        return playlists

    def _write(self, playlists: dict[str, Playlist]) -> None:
        data = {"version": VERSION, "playlists": [
            {"name": group.name, "songs": [
                {"name": p.name, "mood": p.mood, "style": p.style.to_dict(), "saved_at": p.saved_at}
                for p in sorted(group.songs, key=lambda p: p.name.casefold())
            ]}
            for group in sorted(playlists.values(), key=lambda p: p.name.casefold())
        ]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".playlist-library-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        except (OSError, TypeError, ValueError) as e:
            raise PresetError(f"Cannot write {self.path}: {e}") from e

    def list(self) -> list[Playlist]:
        """Named groups including empty ones, sorted ignoring case."""
        return sorted(self._load()[0].values(), key=lambda p: p.name.casefold())

    def list_songs(self, playlist: str) -> list[Preset]:
        """Sorted songs, or an empty list for an unknown group."""
        group = self._load()[0].get(_key(playlist))
        return list(group.songs) if group else []

    def get(self, playlist: str, name: str) -> Preset | None:
        return next((p for p in self.list_songs(playlist) if _key(p.name) == _key(name)), None)

    def save(self, playlist: str, name: str, style: Style) -> tuple[Preset, bool]:
        """Save the caller's audible Style; create its group only on successful save.

        Return the validated song and whether its case-insensitive name replaced
        a song within this group. Existing group spelling is preserved.
        """
        playlist, name = normalize_name(playlist), normalize_name(name)
        if not isinstance(style, Style):
            raise PresetError("A saved song requires a Style.")
        try:
            # Reuse preset parsing for name/mood/voice rules and Style.from_dict.
            song = _parse({"name": name, "mood": style.mood, "style": style.to_dict(),
                           "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        except (TypeError, ValueError, OverflowError) as e:
            raise PresetError(f"Cannot save song: invalid Style: {e}") from e
        if song is None:
            raise PresetError("Cannot save song: invalid Style.")
        playlists = self._writable()
        group = playlists.get(playlist.casefold(), Playlist(playlist, ()))
        songs = {p.name.casefold(): p for p in group.songs}
        replaced = name.casefold() in songs
        songs[name.casefold()] = song
        playlists[playlist.casefold()] = Playlist(group.name, tuple(songs.values()))
        self._write(playlists)
        return song, replaced

    def delete(self, playlist: str, name: str) -> bool:
        """Delete only the selected song; retain its group even when empty."""
        playlists = self._writable()
        group = playlists.get(_key(playlist))
        if group is None:
            return False
        songs = tuple(p for p in group.songs if _key(p.name) != _key(name))
        if len(songs) == len(group.songs):
            return False
        playlists[_key(playlist)] = Playlist(group.name, songs)
        self._write(playlists)
        return True
