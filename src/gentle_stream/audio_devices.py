"""Discover PortAudio outputs and remember the last choice by name, not transient index."""
from __future__ import annotations

import importlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

QUERY_ENV = "GENTLE_STREAM_DEVICE_QUERY"  # Testing seam: module:attr replaces sounddevice.query_devices.


class DeviceError(Exception):
    """The requested audio output could not be listed, selected or remembered."""


@dataclass(frozen=True)
class Device:
    index: int
    name: str
    default: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _query():
    if spec := os.environ.get(QUERY_ENV):
        module, _, attr = spec.partition(":")
        return getattr(importlib.import_module(module), attr)
    import sounddevice as sd

    return sd.query_devices


def output_devices(query=None) -> list[Device]:
    """List output-capable devices in PortAudio order, with the system default marked."""
    try:
        query = query if query is not None else _query()
        devices = list(query())
        try:
            default = int(query(kind="output")["index"])
        except Exception:  # A missing system default need not prevent explicit selection.
            default = None
        outputs = []
        for i, device in enumerate(devices):
            if device.get("max_output_channels", 0) > 0:
                index = int(device.get("index", i))
                outputs.append(Device(index, str(device["name"]), index == default))
        return outputs
    except Exception as error:
        raise DeviceError(f"cannot list the audio devices: {error}") from error


def find(name: str, devices: list[Device]) -> Device | None:
    """Resolve a stored name exactly (ignoring case), never by a partial match."""
    key = name.casefold()
    return next((device for device in devices if device.name.casefold() == key), None)


def resolve(spec: str | int, devices: list[Device]) -> Device:
    """Resolve an index, an exact name, or an unambiguous part of a name."""
    text = str(spec).strip()
    if text.isdecimal():
        try:
            index = int(text)
        except ValueError:
            index = None
        if (match := next((device for device in devices if device.index == index), None)) is not None:
            return match
    elif text:
        if (match := find(text, devices)) is not None:
            return match
        partial = [device for device in devices if text.casefold() in device.name.casefold()]
        if len(partial) == 1:
            return partial[0]
        if partial:
            names = ", ".join(device.name for device in partial)
            raise DeviceError(f"“{text}” matches several audio output devices: {names}")
    raise DeviceError(f"no audio output device matches “{text}”")


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "gentle-stream" / "audio.json"


def load_choice() -> str | None:
    """An absent or unreadable preference means the system default."""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    name = data.get("device") if isinstance(data, dict) else None
    return name if isinstance(name, str) and name else None


def save_choice(name: str | None) -> None:
    """Atomically remember a successfully opened output (None: system default)."""
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".audio-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump({"device": name}, file, ensure_ascii=False)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as error:
        raise DeviceError(f"Cannot write {path}: {error}") from error
