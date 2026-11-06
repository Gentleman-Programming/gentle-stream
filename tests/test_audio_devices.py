import json

import pytest

from gentle_stream import audio_devices
from gentle_stream.audio_devices import Device, DeviceError
from tests.test_daemon import fake_devices, no_devices

OUTPUTS = [Device(1, "MacBook Pro Speakers", True), Device(2, "Studio Display Speakers", False),
           Device(3, "BlackHole 2ch", False)]


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("GENTLE_STREAM_DEVICE_QUERY", raising=False)


# ------------------------------------------------------------ listing
def test_lists_only_output_devices_with_their_index_and_the_default():
    assert audio_devices.output_devices(fake_devices) == OUTPUTS


def test_the_query_can_be_replaced_through_the_environment(monkeypatch):
    monkeypatch.setenv("GENTLE_STREAM_DEVICE_QUERY", "tests.test_daemon:fake_devices")
    assert audio_devices.output_devices() == OUTPUTS


def test_a_failing_query_is_a_device_error():
    with pytest.raises(DeviceError) as error:
        audio_devices.output_devices(no_devices)
    assert str(error.value) == "cannot list the audio devices: PortAudio library not found"


def test_no_default_output_marks_none():
    def query(device=None, kind=None):
        if kind == "output":
            raise OSError("no default output device")
        return fake_devices()

    assert not any(d.default for d in audio_devices.output_devices(query))


# ------------------------------------------------------------ resolving NAME|INDEX
@pytest.mark.parametrize("spec, index", [
    (2, 2), ("3", 3),                                   # exact index
    ("studio display speakers", 2), ("BLACKHOLE 2CH", 3),   # exact name, any case
    ("studio", 2), ("hole", 3), ("macbook", 1),         # unique substring
])
def test_resolves_an_index_an_exact_name_or_a_unique_substring(spec, index):
    assert audio_devices.resolve(spec, OUTPUTS).index == index


def test_an_exact_name_wins_over_a_longer_name_that_contains_it():
    devices = [Device(4, "Display Speakers", False), Device(2, "Studio Display Speakers", False)]
    assert audio_devices.resolve("display speakers", devices).index == 4


def test_an_ambiguous_substring_is_refused_with_the_candidates():
    with pytest.raises(DeviceError) as error:
        audio_devices.resolve("speakers", OUTPUTS)
    assert str(error.value) == ("“speakers” matches several audio output devices: MacBook Pro Speakers, "
                                "Studio Display Speakers")


@pytest.mark.parametrize("spec", ["nope", "0", 7, ""])
def test_an_unknown_device_is_refused(spec):
    with pytest.raises(DeviceError) as error:
        audio_devices.resolve(spec, OUTPUTS)
    assert str(error.value) == f"no audio output device matches “{spec}” (run gentle-stream devices to list them)"


def test_find_matches_only_the_exact_name():
    assert audio_devices.find("studio display speakers", OUTPUTS).index == 2
    assert audio_devices.find("studio", OUTPUTS) is None


# ------------------------------------------------------------ remembered choice
def test_the_choice_is_remembered_in_the_config_dir(tmp_path):
    assert audio_devices.load_choice() is None
    audio_devices.save_choice("Studio Display Speakers")
    path = tmp_path / "xdg" / "gentle-stream" / "audio.json"
    assert audio_devices.config_path() == path
    assert json.loads(path.read_text()) == {"device": "Studio Display Speakers"}
    assert audio_devices.load_choice() == "Studio Display Speakers"
    audio_devices.save_choice(None)
    assert audio_devices.load_choice() is None
    assert [p.name for p in path.parent.iterdir()] == ["audio.json"]     # written atomically, no temp file left


@pytest.mark.parametrize("content", ["{not json", '{"device": 3}', '["x"]'])
def test_an_unreadable_choice_counts_as_none(tmp_path, content):
    path = tmp_path / "xdg" / "gentle-stream" / "audio.json"
    path.parent.mkdir(parents=True)
    path.write_text(content)
    assert audio_devices.load_choice() is None


def test_a_choice_that_cannot_be_written_is_a_device_error(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(blocker))
    with pytest.raises(DeviceError, match="Cannot write"):
        audio_devices.save_choice("x")
