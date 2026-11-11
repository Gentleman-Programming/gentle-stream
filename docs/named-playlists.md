# Named playlist storage

`PlaylistLibrary` saves validated songs in named groups without reading, copying,
or changing legacy `presets.json`. The UI must provide existing “My playlist”
contents separately through `PresetStore`. This module does not control playback
or create UI controls.

## Quick path

```python
from gentle_stream.playlist_library import PlaylistLibrary

library = PlaylistLibrary()
song, replaced = library.save("Night café", "Rain", audible_style)
for playlist in library.list():
    print(playlist.name, playlist.songs)
songs = library.list_songs("Night café")
song = library.get("Night café", "Rain")
library.delete("Night café", "Rain")
```

Pass the currently audible `Style`, not a pending prompt or generated future song.
`save` creates a group and its first song together; there is no empty-group creation
operation. Deleting the last song retains the named group.

## API for UI integration

| Public member | Result |
| --- | --- |
| `PlaylistLibrary(path: Path \| str \| None = None)` | Store; default path follows XDG config home |
| `list() -> list[Playlist]` | All named groups, including empty groups |
| `list_songs(playlist: str) -> list[Preset]` | Songs; unknown group returns `[]` |
| `get(playlist: str, name: str) -> Preset \| None` | Selected song; unknown group/song returns `None` |
| `save(playlist: str, name: str, style: Style) -> tuple[Preset, bool]` | Validated song and replacement flag |
| `delete(playlist: str, name: str) -> bool` | Whether a song was removed |
| `error: str \| None` | Problem from the last read, suitable for a status line |

`Playlist` is frozen with `name: str` and `songs: tuple[Preset, ...]`.
`Preset` is the existing frozen record with `name`, `mood`, `style`, and `saved_at`.
Lists are fresh snapshots, sorted by case-insensitive name. Song lookups and group
lookups collapse whitespace and ignore case. Saved names use `normalize_name`
(nonempty, at most 40 characters). Existing group spelling is preserved; replacing
a song adopts the new spelling. The same song title in another group is independent.

## Persistence and errors

The separate `$XDG_CONFIG_HOME/gentle-stream/playlist-library.json` (default
`~/.config/gentle-stream/playlist-library.json`) has this versioned shape:

```json
{"version": 1, "playlists": [{"name": "Night café", "songs": []}]}
```

Each song uses the existing preset fields: `name`, `mood`, `style`, `saved_at`.
Styles pass through existing preset parsing and `Style.from_dict` on both save and
read, including bounded musical parameters. Unknown fields are ignored; song titles
and playlist names do not change musical parameters.

Missing files read as an empty library. Corrupt JSON, unsupported versions,
malformed records, and duplicate stored names fail closed: reads return empty
results with `error`, and writes raise `PresetError` without overwriting the file.
Repair or move the file before saving again. Invalid saves leave existing bytes
and groups unchanged. No data migration or legacy-file repair is performed.

Writes follow the existing preset convention: a same-directory temporary file,
flush, fsync, and atomic replacement. Failed replacements leave prior bytes intact.
Calls reload storage but are not multi-process transactions; simultaneous writers
can lose updates. No locking, import/export, whole-playlist deletion, or playback
sequencing is provided here.
