"""Saved prompts: named styles kept in a small JSON file, applied later without asking Gentle Shell again.

The file lives at `$XDG_CONFIG_HOME/gentle-stream/presets.json` (default `~/.config/gentle-stream/`). It is
rewritten atomically. A corrupt file never crashes the app and is never overwritten; invalid entries are skipped.
Styles are read back through `Style.from_dict`, so a hand-edited file still yields playable styles.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from .style import Style

NAME_MAX = 40
VERSION = 1


class PresetError(Exception):
    """A preset cannot be saved or deleted; the message is meant for the status line."""


@dataclass(frozen=True)
class Preset:
    name: str
    mood: str
    style: Style      # named after the preset, so the track shows the preset name
    saved_at: str     # ISO 8601, UTC


def default_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "gentle-stream" / "presets.json"


def normalize_name(name: str) -> str:
    """Trim and collapse whitespace; reject empty or too long names."""
    name = " ".join(str(name).split())
    if not name:
        raise PresetError("The name cannot be empty.")
    if len(name) > NAME_MAX:
        raise PresetError(f"The name is too long (max {NAME_MAX} characters).")
    return name


def _parse(entry) -> Preset | None:
    if not isinstance(entry, dict) or not isinstance(entry.get("style"), dict):
        return None
    try:
        name = normalize_name(entry.get("name", ""))
    except PresetError:
        return None
    mood = entry.get("mood", entry["style"].get("mood", ""))
    style = Style.from_dict({**entry["style"], "name": name, "mood": mood if isinstance(mood, str) else ""})
    saved_at = entry.get("saved_at")
    return Preset(name, style.mood, style, saved_at if isinstance(saved_at, str) else "")


class PresetStore:
    """Named presets on disk. Every call reads the file, so several app instances never lose each other's saves.

    `error` describes the problem found by the last read (corrupt file, skipped entries), or is None.
    """

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else default_path()
        self.error: str | None = None

    def _load(self) -> tuple[dict[str, Preset], bool]:
        """Presets by lowercase name, and whether the file is readable (False: leave it untouched)."""
        self.error = None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}, True
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            self.error = f"Cannot read {self.path}: {e}"
            return {}, False
        entries = data.get("presets") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            self.error = f"Cannot read {self.path}: unexpected format"
            return {}, False
        presets, skipped = {}, 0
        for entry in entries:
            if (preset := _parse(entry)) is None:
                skipped += 1
            else:
                presets[preset.name.casefold()] = preset
        if skipped:
            self.error = f"Skipped {skipped} invalid saved prompt(s) in {self.path}"
        return presets, True

    def _writable(self) -> dict[str, Preset]:
        presets, ok = self._load()
        if not ok:
            raise PresetError(f"{self.error}; fix or move the file to save prompts again.")
        return presets

    def _write(self, presets: dict[str, Preset]) -> None:
        data = {"version": VERSION, "presets": [
            {"name": p.name, "mood": p.mood, "style": p.style.to_dict(), "saved_at": p.saved_at}
            for p in sorted(presets.values(), key=lambda p: p.name.casefold())
        ]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".presets-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError as e:
            raise PresetError(f"Cannot write {self.path}: {e}") from e

    def list(self) -> list[Preset]:
        """All presets, sorted by name ignoring case."""
        return sorted(self._load()[0].values(), key=lambda p: p.name.casefold())

    def get(self, name: str) -> Preset | None:
        return self._load()[0].get(" ".join(str(name).split()).casefold())

    def save(self, name: str, style: Style) -> tuple[Preset, bool]:
        """Save `style` (its mood included) under `name`; returns the preset and whether it replaced one."""
        name = normalize_name(name)
        presets = self._writable()
        style = replace(style, name=name)
        preset = Preset(name, style.mood, style, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        replaced = presets.pop(name.casefold(), None) is not None
        presets[name.casefold()] = preset
        self._write(presets)
        return preset, replaced

    def delete(self, name: str) -> bool:
        """Remove the preset called `name` (ignoring case); False if there was none."""
        presets = self._writable()
        if presets.pop(" ".join(str(name).split()).casefold(), None) is None:
            return False
        self._write(presets)
        return True
