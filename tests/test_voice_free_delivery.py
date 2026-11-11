"""Instrumental public contracts; no output device, provider, or speech process."""
import json
import sys
import subprocess
from unittest.mock import Mock
from types import SimpleNamespace

import numpy as np
import pytest

from gentle_stream import main
from gentle_stream.bridge import GentleShell, InterpretError
from gentle_stream.engine import Engine
from gentle_stream.player import Player
from gentle_stream.style import Style


def test_help_describes_music_controls_without_voice_opt_in(capsys):
    with pytest.raises(SystemExit) as result:
        main(["--help"])
    out, err = capsys.readouterr()
    assert result.value.code == 0 and err == ""
    assert "agrega frases" not in out and "integrated-voice" not in out
    assert "Panic" in out and "setup-samples" in out and "--model" in out


@pytest.mark.parametrize("mood", ["quiet trap", "quiet trap, agrega frases"])
def test_interpretation_is_instrumental_even_with_legacy_words_and_model_fields(monkeypatch, mood):
    # Any attempt to import the excluded adapter fails, rather than synthesizing.
    monkeypatch.setitem(sys.modules, "gentle_stream.voice", None)
    monkeypatch.setattr(subprocess, "Popen", Mock(side_effect=AssertionError("unexpected external process")))
    shell = GentleShell()
    shell._run = Mock(return_value=json.dumps({
        "name": "Night", "genre": "trap", "bpm": 88,
        "voice_lines": ["Keep moving onward", "Take another step", "Trust your rhythm"],
    }))
    style, usage = shell.interpret_with_usage(mood, Style())
    assert style == Style(name="Night", mood=mood, genre="trap", bpm=88)
    assert usage is None and "voice_lines" not in style.to_dict()
    prompt = shell._run.call_args.args[0][-1]
    assert '"voice_lines"' not in prompt
    assert shell._run.call_count == 1
    engine = Engine(style, seed=4, samples=False)
    audio = engine.render_bar()
    assert audio.shape == (round(240 / 88 * engine.sr), 2)
    assert audio.dtype == np.float32 and np.isfinite(audio).all()
    assert 0 < np.max(np.abs(audio)) < 1


def test_existing_bridge_error_and_current_musical_defaults_are_preserved():
    current = Style(name="Rain", genre="ambient", bpm=65, lead="none")
    shell = GentleShell()
    shell._run = Mock(return_value='{"energy":0.2}')
    style = shell.interpret("softer", current)
    assert style == Style.from_dict({"mood": "softer", "energy": .2}, current)
    shell._run.return_value = "no object"
    with pytest.raises(InterpretError) as error:
        shell.interpret("softer", current)
    assert str(error.value) == "the model answered with no JSON object"
    assert current == Style(name="Rain", genre="ambient", bpm=65, lead="none")


def test_retained_model_listing_and_jsonl_usage_contract():
    from gentle_stream.bridge import Usage

    shell = GentleShell(model="provider/model")
    shell._run = Mock(return_value="provider model\nopenai example\n")
    assert shell.list_models() == ["openai/example"]
    shell._run.assert_called_once_with(["--no-mcp", "--no-skill-registry", "--list-models"])
    event = {"type": "message_end", "message": {"role": "assistant", "content": [
        {"type": "text", "text": '{"genre":"Jazz", "bpm":90}'}],
        "usage": {"totalTokens": 42}}}
    shell._run.return_value = json.dumps(event)
    style, usage = shell.interpret_with_usage("club", Style(space=.8))
    assert (style.genre, style.bpm, style.space) == ("jazz", 90, .8)
    assert usage == Usage(total=42)


def test_retained_instrumental_rendering_matches_unmixed_master_path():
    # Preserve the former mix module's instrumental-only comparison without importing speech code.
    class UnmixedEngine(Engine):
        def render_bar(self):
            self._schedule_bar()
            n = int(round(self.bar_seconds * self.sr))
            out = self._acc[:n].copy()
            self._acc[:-n] = self._acc[n:]
            self._acc[-n:] = 0
            out = self._master(out)
            self.bar += 1
            self.samples_out += n
            return out.astype(np.float32)

    style = Style(bpm=140, genre="rock", vinyl=0)
    current = Engine(style, seed=123, sr=22050, samples=False)
    old = UnmixedEngine(style, seed=123, sr=22050, samples=False)
    for _ in range(3):
        np.testing.assert_array_equal(current.render_bar(), old.render_bar())


def test_wsl_discovery_uses_path_or_explicit_override_without_local_voice_environment(tmp_path, monkeypatch):
    from gentle_stream import windows_audio

    python = tmp_path / "python.exe"
    override = tmp_path / "explicit-python.exe"
    ffplay = tmp_path / "ffplay.exe"
    for path in (python, override, ffplay):
        path.write_bytes(b"MZ synthetic executable, never launched")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Synthetic")
    monkeypatch.delenv("GENTLE_STREAM_WINDOWS_PYTHON", raising=False)
    monkeypatch.delenv("GENTLE_STREAM_WINDOWS_FFPLAY", raising=False)
    monkeypatch.setattr(windows_audio.shutil, "which", lambda name: str(tmp_path / name))
    convert = Mock(return_value=SimpleNamespace(stdout="C:/synthetic/ffplay.exe\n"))
    monkeypatch.setattr(subprocess, "run", convert)
    assert windows_audio._executables() == (str(python), "C:/synthetic/ffplay.exe")
    monkeypatch.setenv("GENTLE_STREAM_WINDOWS_PYTHON", str(override))
    assert windows_audio._executables() == (str(override), "C:/synthetic/ffplay.exe")
    assert convert.call_count == 2
    for call in convert.call_args_list:
        assert call.args[0] == ["wslpath", "-w", str(ffplay)]
        assert call.kwargs == dict(check=True, capture_output=True, text=True, timeout=5)


def test_staged_style_commit_and_ordinary_next_preserve_instrumental_settings():
    player = Player(Engine(Style(name="Old"), seed=4, sr=16000, samples=False))
    chosen = Style(name="Chosen", mood="calm", lead="none", drums="none")
    player.pause()
    player.set_volume(.37)
    with player.stage_style(chosen) as commit:
        assert commit() is True
    player.next()
    assert player.style == chosen and player.engine.piece == 2
    assert not player.playing and player.volume == .37
    player.next()
    assert player.style.name == chosen.name and player.style.mood == chosen.mood
    assert player.style.lead == "none" and player.style.drums == "none"
    assert player.engine.piece == 3
