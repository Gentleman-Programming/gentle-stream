"""Audio output devices: list them, resolve a NAME|INDEX choice, and remember the last choice.

Devices come from `sounddevice.query_devices`; any callable with its signature can stand in for it, so tests never
need audio hardware. The choice is remembered by name, not index (indices change when devices come and go), in
`$XDG_CONFIG_HOME/gentle-stream/audio.json` (default `~/.config/gentle-stream/`), rewritten atomically. An unreadable
file counts as no choice: the system default.
"""
from __future__ import annotations

import importlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

QUERY_ENV = "GENTLE_STREAM_DEVICE_QUERY"   # testing seam: "module:attr" replaces sounddevice.query_devices


class DeviceError(Exception):
    """A device cannot be listed, resolved or remembered; the message is meant for the terminal or status line."""


@dataclass(frozen=True)
class Device:
    index: int        # sounddevice's device index, what the output stream is opened with
    name: str
    default: bool     # the system default output

    def to_dict(self) -> dict:
        return asdict(self)


def _query():
    if spec := os.environ.get(QUERY_ENV):
        module, _, attr = spec.partition(":")
        return getattr(importlib.import_module(module), attr)
    import sounddevice as sd

    return sd.query_devices


def output_devices(query=None) -> list[Device]:
    """Every device that can play audio, in sounddevice's order."""
    query = query or _query()
    try:
        devices = list(query())
    except Exception as e:   # PortAudio missing or failing
        raise DeviceError(f"cannot list the audio devices: {e}") from e
    try:
        default = int(query(kind="output")["index"])
    except Exception:        # no default output device
        default = None
    out = []
    for i, d in enumerate(devices):
        if d.get("max_output_channels", 0) > 0:
            index = int(d.get("index", i))
            out.append(Device(index, str(d["name"]), index == default))
    return out


def find(name: str, devices: list[Device]) -> Device | None:
    """The device called exactly `name`, ignoring case: how a remembered choice is found again."""
    key = name.casefold()
    return next((d for d in devices if d.name.casefold() == key), None)


def resolve(spec: str | int, devices: list[Device]) -> Device:
    """An index, an exact name (ignoring case) or a part of exactly one device's name."""
    text = str(spec).strip()
    if text.isdigit():
        if (match := next((d for d in devices if d.index == int(text)), None)) is not None:
            return match
    elif text:
        if (match := find(text, devices)) is not None:
            return match
        partial = [d for d in devices if text.casefold() in d.name.casefold()]
        if len(partial) == 1:
            return partial[0]
        if partial:
            names = ", ".join(d.name for d in partial)
            raise DeviceError(f"“{text}” matches several audio output devices: {names}")
    raise DeviceError(f"no audio output device matches “{text}” (run gentle-stream devices to list them)")


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "gentle-stream" / "audio.json"


def load_choice() -> str | None:
    """The remembered device name, or None (system default) when there is none or the file is unreadable."""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    name = data.get("device") if isinstance(data, dict) else None
    return name if isinstance(name, str) and name else None


def save_choice(name: str | None) -> None:
    """Remember `name` (None: the system default) for the next player."""
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".audio-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"device": name}, f, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as e:
        raise DeviceError(f"Cannot write {path}: {e}") from e
