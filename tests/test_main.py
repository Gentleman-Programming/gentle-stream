import pytest

from gentle_stream import main
from tests.test_daemon import runtime  # noqa: F401  (fixture: private runtime dir, fake audio, cleanup)


def test_help_lists_flags_and_controls(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    out = capsys.readouterr().out
    assert exit_info.value.code == 0
    for text in ("--model", "--mood", "--gentle-shell", "play / pause", "next piece", "describe a mood",
                 "save what is playing", "saved prompts", "presets.json", "the music keeps playing",
                 "quit and stop the music", "ctl", "play|pause|toggle|next|stop|status"):
        assert text in out
    assert "daemon" not in out                     # started automatically, never by hand


def test_an_unavailable_audio_output_is_a_clean_error(runtime, monkeypatch, capsys):
    monkeypatch.setenv("GENTLE_STREAM_AUDIO_FACTORY", "tests.test_daemon:no_device")
    assert main([]) == 1
    err = capsys.readouterr().err
    assert err == "gentle-stream: cannot open the audio output: Error querying device -1\n"


def test_the_tui_attaches_to_the_music_already_playing(runtime, monkeypatch):
    from gentle_stream import client
    from gentle_stream.app import StreamApp

    running = client.ensure()
    seen = []
    monkeypatch.setattr(StreamApp, "run", lambda self: seen.append((self.player.pid, self.initial_mood)))
    assert main(["--mood", "foggy harbour"]) == 0
    assert seen == [(running.pid, "foggy harbour")]   # no second player, the mood goes to the running one


def test_ctl_rejects_an_unknown_action(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["ctl", "louder"])
    assert exit_info.value.code == 2


def test_help_lists_the_setup_samples_command(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "setup-samples" in capsys.readouterr().out


def test_setup_samples_installs_once_and_reports_it(tmp_path, monkeypatch, capsys, fake_recorded):
    from gentle_stream import samples
    from tests.test_samples import make_deb

    url, sha = make_deb(tmp_path / "pkg.deb")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(samples, "DEB_URL", url)
    monkeypatch.setattr(samples, "DEB_SHA256", sha)
    assert main(["setup-samples"]) == 0
    out = capsys.readouterr().out
    assert "installed" in out and "FluidR3_GM.sf2" in out and "MIT" in out
    assert main(["setup-samples"]) == 0
    assert "already installed" in capsys.readouterr().out


def test_setup_samples_reports_a_checksum_mismatch(tmp_path, monkeypatch, capsys, fake_recorded):
    from gentle_stream import samples
    from tests.test_samples import make_deb

    url, _ = make_deb(tmp_path / "pkg.deb")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(samples, "DEB_URL", url)
    monkeypatch.setattr(samples, "DEB_SHA256", "0" * 64)
    assert main(["setup-samples"]) == 1
    assert "checksum" in capsys.readouterr().err.lower()


@pytest.fixture
def fake_recorded(tmp_path, monkeypatch):
    """Replace the recorded-library install (hundreds of MB) with a fake that records its calls."""
    from gentle_stream import recorded, samples

    state = {"installed": None, "calls": 0, "error": None}

    def setup(progress=None, **kw):
        state["calls"] += 1
        if state["error"]:
            raise state["error"]
        if progress:
            progress(5, 10); progress(10, 10)
        state["installed"] = tmp_path / "cache" / "gentle-stream" / "recorded"
        return state["installed"]

    monkeypatch.setattr(samples, "setup_recorded", setup)
    monkeypatch.setattr(recorded, "installed", lambda cache, m=None: state["installed"])
    return state


def _fake_soundfont(tmp_path, monkeypatch):
    from gentle_stream import samples
    from tests.test_samples import make_deb

    url, sha = make_deb(tmp_path / "pkg.deb")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(samples, "DEB_URL", url)
    monkeypatch.setattr(samples, "DEB_SHA256", sha)


def test_setup_samples_also_installs_the_recorded_instruments(tmp_path, monkeypatch, capsys, fake_recorded):
    _fake_soundfont(tmp_path, monkeypatch)
    assert main(["setup-samples"]) == 0
    out = capsys.readouterr().out
    assert fake_recorded["calls"] == 1
    assert "recorded instruments" in out and "SOURCES.txt" in out and "CC0" in out
    assert main(["setup-samples"]) == 0
    assert fake_recorded["calls"] == 1 and "already installed" in capsys.readouterr().out


def test_setup_samples_installs_only_the_missing_recorded_instruments(tmp_path, monkeypatch, capsys,
                                                                     fake_recorded):
    from gentle_stream import samples

    _fake_soundfont(tmp_path, monkeypatch)
    samples.setup_samples()                         # an existing user: soundfont from an earlier version
    monkeypatch.setattr(samples, "DEB_URL", (tmp_path / "missing.deb").as_uri())
    assert main(["setup-samples"]) == 0
    out = capsys.readouterr().out
    assert fake_recorded["calls"] == 1 and "FluidR3_GM already installed" in out and "recorded instruments" in out


def test_setup_samples_reports_a_recorded_failure_and_keeps_the_soundfont(tmp_path, monkeypatch, capsys,
                                                                         fake_recorded):
    from gentle_stream import samples

    _fake_soundfont(tmp_path, monkeypatch)
    fake_recorded["error"] = samples.ChecksumError("checksum mismatch for piano.flac")
    assert main(["setup-samples"]) == 1
    assert "checksum mismatch for piano.flac" in capsys.readouterr().err
    assert samples.soundfont_path() is not None


# ------------------------------------------------------------ output device
def test_help_explains_how_to_choose_the_output_device(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    for text in ("--device NAME|INDEX", "devices", "choose the audio output device", "audio.json"):
        assert text in out


def test_devices_lists_the_outputs_the_default_and_the_remembered_choice(tmp_path, monkeypatch, capsys):
    from gentle_stream import audio_devices

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("GENTLE_STREAM_DEVICE_QUERY", "tests.test_daemon:fake_devices")
    audio_devices.save_choice("Studio Display Speakers")
    assert main(["devices"]) == 0
    assert capsys.readouterr() == ("    1  MacBook Pro Speakers *\n"
                                   ">   2  Studio Display Speakers\n"
                                   "    3  BlackHole 2ch\n"
                                   "* system default · > chosen output (remembered)\n", "")


def test_devices_reports_when_the_devices_cannot_be_listed(monkeypatch, capsys):
    monkeypatch.setenv("GENTLE_STREAM_DEVICE_QUERY", "tests.test_daemon:no_devices")
    assert main(["devices"]) == 1
    assert capsys.readouterr() == ("", "gentle-stream: cannot list the audio devices: PortAudio library not found\n")


def test_device_flag_switches_the_player_after_attaching(runtime, monkeypatch):
    from gentle_stream import audio_devices
    from gentle_stream.app import StreamApp

    seen = []
    monkeypatch.setattr(StreamApp, "run", lambda self: seen.append(self.player.device))
    assert main(["--device", "2"]) == 0
    assert seen == ["Studio Display Speakers"]
    assert audio_devices.load_choice() == "Studio Display Speakers"


def test_an_unknown_device_flag_is_a_clean_error_and_starts_nothing(runtime, capsys):
    from gentle_stream import client

    assert main(["--device", "nope"]) == 1
    assert capsys.readouterr() == ("", "gentle-stream: no audio output device matches “nope” "
                                       "(run gentle-stream devices to list them)\n")
    with pytest.raises(client.NoPlayer):
        client.connect()
