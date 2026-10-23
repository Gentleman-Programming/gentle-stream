import pytest
import sounddevice as sd

from gentle_stream import main


def test_help_lists_flags_and_controls(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    out = capsys.readouterr().out
    assert exit_info.value.code == 0
    for text in ("--model", "--mood", "--gentle-shell", "play / pause", "next piece", "describe a mood"):
        assert text in out


def test_an_unavailable_audio_output_is_a_clean_error(monkeypatch, capsys):
    def no_device(**kw):
        raise sd.PortAudioError("Error querying device -1")

    monkeypatch.setattr(sd, "OutputStream", no_device)
    assert main([]) == 1
    err = capsys.readouterr().err
    assert err == "gentle-stream: cannot open the audio output: Error querying device -1\n"
