import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from tests.test_daemon import fake_devices


@pytest.fixture
def config(monkeypatch):
    with TemporaryDirectory() as root:
        monkeypatch.setenv("XDG_CONFIG_HOME", root)
        yield Path(root) / "gentle-stream" / "audio.json"


def test_discovers_outputs_and_default(monkeypatch):
    from gentle_stream.audio_devices import Device, output_devices

    monkeypatch.setenv("GENTLE_STREAM_DEVICE_QUERY", "tests.test_daemon:fake_devices")
    assert output_devices() == [Device(1, "Built-in Speakers", True), Device(4, "USB Speakers", False)]


def test_query_load_failure_is_a_device_error(monkeypatch):
    from gentle_stream.audio_devices import DeviceError, output_devices

    monkeypatch.setenv("GENTLE_STREAM_DEVICE_QUERY", "tests.test_daemon:not_a_query")
    with pytest.raises(DeviceError, match="cannot list the audio devices"):
        output_devices()


def test_resolves_index_name_and_only_unambiguous_substrings():
    from gentle_stream.audio_devices import DeviceError, output_devices, resolve

    devices = output_devices(fake_devices)
    for spec in (4, "4", "  USB Speakers  ", "usb"):
        assert resolve(spec, devices) == devices[1]
    with pytest.raises(DeviceError, match="^“Speakers” matches several audio output devices: Built-in Speakers, USB Speakers$"):
        resolve("Speakers", devices)
    with pytest.raises(DeviceError, match="^no audio output device matches “0”$"):
        resolve(0, devices)


def test_remembers_the_name_and_can_return_to_default(config):
    from gentle_stream.audio_devices import load_choice, save_choice

    assert load_choice() is None
    save_choice("Écouteurs")
    assert load_choice() == "Écouteurs"
    assert json.loads(config.read_text(encoding="utf-8")) == {"device": "Écouteurs"}
    save_choice(None)
    assert load_choice() is None
    assert json.loads(config.read_text(encoding="utf-8")) == {"device": None}
    assert list(config.parent.iterdir()) == [config]


@pytest.mark.parametrize("content", [b"{bad", b"\xff", b'[]', b'{"device":3}'])
def test_invalid_memory_uses_default_without_overwriting(config, content):
    from gentle_stream.audio_devices import load_choice

    config.parent.mkdir()
    config.write_bytes(content)
    assert load_choice() is None
    assert config.read_bytes() == content


def test_failed_write_does_not_replace_previous_choice(config, monkeypatch):
    from gentle_stream.audio_devices import DeviceError, load_choice, save_choice

    save_choice("Built-in Speakers")
    def cannot_replace(*args):
        raise OSError("read only")

    monkeypatch.setattr("gentle_stream.audio_devices.os.replace", cannot_replace)
    with pytest.raises(DeviceError, match="read only"):
        save_choice("USB Speakers")
    assert load_choice() == "Built-in Speakers"
    assert list(config.parent.iterdir()) == [config]
