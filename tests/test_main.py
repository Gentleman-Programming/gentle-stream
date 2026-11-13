import json

import pytest

from gentle_stream import main
from gentle_stream.client import AudioError, NoPlayer, PlayerError
from gentle_stream.style import Style
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


def test_setup_samples_installs_once_and_reports_it(tmp_path, monkeypatch, capsys):
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


def test_setup_samples_reports_a_checksum_mismatch(tmp_path, monkeypatch, capsys):
    from gentle_stream import samples
    from tests.test_samples import make_deb

    url, _ = make_deb(tmp_path / "pkg.deb")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(samples, "DEB_URL", url)
    monkeypatch.setattr(samples, "DEB_SHA256", "0" * 64)
    assert main(["setup-samples"]) == 1
    assert "checksum" in capsys.readouterr().err.lower()


# ------------------------------------------------------------ ctl JSON machine interface
ENVELOPE_KEYS = ["ok", "running", "playing", "volume", "name", "genre", "mood"]


def fake_state(style=None, playing=True, volume=0.6):
    style = style or Style(name="Midnight Rain", mood="", genre="lofi")
    return {"pid": 999, "playing": playing, "volume": volume, "level": 0.4, "backend": "procedural",
            "now": {"style": style.to_dict(), "piece": 1, "bar": 0}, "style": style.to_dict()}


class FakeClient:
    """A `client.PlayerClient` stand-in for the CLI tests; no socket, no audio."""

    def __init__(self, state, apply=True):
        self.state = dict(state)
        self.style = Style.from_dict(state["style"])
        self.requests = []
        self.closed = self.detached = False
        self._apply = apply

    def request(self, cmd, **args):
        self.requests.append((cmd, args))
        if self._apply:
            if cmd == "set_volume":
                self.state["volume"] = float(min(1.0, max(0.0, args["volume"])))
            elif cmd == "pause":
                self.state["playing"] = False
            elif cmd == "play":
                self.state["playing"] = True
            elif cmd == "toggle":
                self.state["playing"] = not self.state["playing"]
            elif cmd == "set_style":
                self.state["style"] = args["style"]
                self.style = Style.from_dict(args["style"])
        return dict(self.state)

    def close(self):
        self.closed = True

    def detach(self):
        self.detached = True


class FakeShell:
    """A `bridge.GentleShell` stand-in: interpretation is deterministic and never calls a model."""

    def __init__(self, binary="gentle-shell", model=None):
        self.binary, self.model = binary, model
        self.calls = []
        self.error = None
        self.answer = {"name": "Summer Storm", "genre": "jazz"}

    def interpret(self, mood, current):
        from gentle_stream.bridge import InterpretError

        self.calls.append((mood, current))
        if self.error:
            raise InterpretError(self.error)
        return Style.from_dict({**self.answer, "mood": mood}, current)


def test_ctl_status_json_reports_the_snapshot_envelope(monkeypatch, capsys):
    client = FakeClient(fake_state())
    monkeypatch.setattr("gentle_stream.client.connect", lambda: client)
    assert main(["ctl", "status", "--json"]) == 0
    out = capsys.readouterr().out
    assert out.endswith("\n") and out.count("\n") == 1
    data = json.loads(out)
    assert data == {"ok": True, "running": True, "playing": True, "volume": 60,
                    "name": "Midnight Rain", "genre": "lofi", "mood": ""}
    assert list(data) == ENVELOPE_KEYS


def test_ctl_status_json_without_a_player_is_ok_running_false(monkeypatch, capsys):
    monkeypatch.setattr("gentle_stream.client.connect", lambda: (_ for _ in ()).throw(NoPlayer("x")))
    assert main(["ctl", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "running": False}


@pytest.mark.parametrize("action", ["play", "pause", "toggle", "next", "stop"])
def test_ctl_json_action_without_a_player_is_nothing_playing(monkeypatch, capsys, action):
    monkeypatch.setattr("gentle_stream.client.connect", lambda: (_ for _ in ()).throw(NoPlayer("x")))
    assert main(["ctl", action, "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False,
                                                   "error": {"code": "nothing_playing",
                                                              "message": "nothing is playing"}}


def test_ctl_volume_json_validates_and_applies_the_percent_scale(monkeypatch, capsys):
    client = FakeClient(fake_state())
    monkeypatch.setattr("gentle_stream.client.connect", lambda: client)
    assert main(["ctl", "volume", "60", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["volume"] == 60
    assert client.requests == [("set_volume", {"volume": 0.6})]


@pytest.mark.parametrize("value", [["abc"], ["-1"], ["101"], ["60.5"], ["60", "70"], []])
def test_ctl_volume_json_rejects_bad_values_without_contacting_the_player(monkeypatch, capsys, value):
    called = []
    monkeypatch.setattr("gentle_stream.client.connect", lambda: called.append(True))
    assert main(["ctl", "volume", *value, "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and data["error"]["code"] == "invalid_request"
    assert called == []


def test_ctl_volume_json_reports_the_actual_snapshot_not_assumed_success(monkeypatch, capsys):
    client = FakeClient(fake_state(playing=False), apply=False)   # a player that reports it stayed paused
    monkeypatch.setattr("gentle_stream.client.connect", lambda: client)
    assert main(["ctl", "play", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["playing"] is False


def test_ctl_mood_json_uses_the_model_and_reports_the_queued_style(monkeypatch, capsys):
    current = Style(name="Current", mood="old mood")
    client = FakeClient(fake_state(current))
    shell = FakeShell()

    def factory(binary=None, model=None):
        shell.binary, shell.model = binary, model
        return shell

    monkeypatch.setattr("gentle_stream.client.connect", lambda: client)
    monkeypatch.setattr("gentle_stream.bridge.GentleShell", factory)
    assert main(["ctl", "mood", "rainy night", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["name"] == "Summer Storm"
    assert data["genre"] == "jazz" and data["mood"] == "rainy night"
    assert shell.calls == [("rainy night", current)]
    assert (shell.binary, shell.model) == ("gentle-shell", None)
    assert client.requests[0][0] == "set_style"
    assert client.requests[0][1]["style"]["mood"] == "rainy night"


def test_ctl_mood_json_passes_the_model_and_binary_to_the_shell(monkeypatch, capsys):
    client = FakeClient(fake_state())
    seen = {}

    def factory(binary=None, model=None):
        seen["shell"] = FakeShell(binary, model)
        return seen["shell"]

    monkeypatch.setattr("gentle_stream.client.connect", lambda: client)
    monkeypatch.setattr("gentle_stream.bridge.GentleShell", factory)
    assert main(["--model", "claude-bridge/claude-haiku-5-5", "--gentle-shell", "/custom/gentle-shell",
                 "ctl", "mood", "rain", "--json"]) == 0
    shell = seen["shell"]
    assert (shell.binary, shell.model) == ("/custom/gentle-shell", "claude-bridge/claude-haiku-5-5")
    assert shell.calls[0][0] == "rain"


def test_ctl_mood_json_preserves_unicode_and_spaces(monkeypatch, capsys):
    shell = FakeShell()
    client = FakeClient(fake_state())
    monkeypatch.setattr("gentle_stream.client.connect", lambda: client)
    monkeypatch.setattr("gentle_stream.bridge.GentleShell", lambda binary=None, model=None: shell)
    mood = "café ☕ 東京 night"
    assert main(["ctl", "mood", mood, "--json"]) == 0
    assert shell.calls[0][0] == mood
    assert json.loads(capsys.readouterr().out)["mood"] == mood


@pytest.mark.parametrize("value", [[], [""], ["   "], ["x" * 201]])
def test_ctl_mood_json_rejects_invalid_text_without_contacting_the_player(monkeypatch, capsys, value):
    called = []
    monkeypatch.setattr("gentle_stream.client.connect", lambda: called.append(True))
    assert main(["ctl", "mood", *value, "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["error"]["code"] == "invalid_request"
    assert called == []


def test_ctl_mood_json_reports_unavailable_when_interpretation_fails(monkeypatch, capsys):
    shell = FakeShell()
    shell.error = 'Model "nope" not found.'
    client = FakeClient(fake_state())
    monkeypatch.setattr("gentle_stream.client.connect", lambda: client)
    monkeypatch.setattr("gentle_stream.bridge.GentleShell", lambda binary=None, model=None: shell)
    assert main(["ctl", "mood", "rain", "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False,
                                                   "error": {"code": "unavailable",
                                                              "message": "mood interpretation is unavailable"}}
    assert client.requests == []
    assert client.detached is True


def test_ctl_json_errors_never_leak_paths_or_secrets(monkeypatch, capsys):
    def boom():
        raise PlayerError("cannot reach the player: /secret/authkey socket=player.sock")

    monkeypatch.setattr("gentle_stream.client.connect", boom)
    assert main(["ctl", "status", "--json"]) == 1
    out = capsys.readouterr().out
    assert json.loads(out) == {"ok": False,
                               "error": {"code": "unavailable", "message": "the player is unavailable"}}
    assert "/secret" not in out and "authkey" not in out and "player.sock" not in out and "Traceback" not in out


def test_ctl_json_unexpected_error_is_a_safe_envelope(monkeypatch, capsys):
    class Broken(FakeClient):
        def request(self, cmd, **args):
            raise KeyError("state missing 'now'")

    monkeypatch.setattr("gentle_stream.client.connect", lambda: Broken(fake_state()))
    assert main(["ctl", "status", "--json"]) == 1
    out = capsys.readouterr().out
    assert json.loads(out) == {"ok": False,
                               "error": {"code": "unavailable", "message": "unexpected error from the player"}}
    assert "KeyError" not in out and "'now'" not in out and "Traceback" not in out


def test_ctl_json_malformed_player_json_is_a_safe_envelope(monkeypatch, capsys):
    class Broken(FakeClient):
        def request(self, cmd, **args):
            raise json.JSONDecodeError("Expecting value", "<player>", 0)

    monkeypatch.setattr("gentle_stream.client.connect", lambda: Broken(fake_state()))
    assert main(["ctl", "status", "--json"]) == 1
    out = capsys.readouterr().out
    assert json.loads(out) == {"ok": False,
                               "error": {"code": "unavailable", "message": "unexpected error from the player"}}
    assert "Expecting value" not in out and "Traceback" not in out


def test_ctl_legacy_unexpected_error_is_raised_not_swallowed(monkeypatch, capsys):
    class Broken(FakeClient):
        def request(self, cmd, **args):
            raise KeyError("boom")

    monkeypatch.setattr("gentle_stream.client.connect", lambda: Broken(fake_state()))
    with pytest.raises(KeyError):
        main(["ctl", "status"])


def test_ctl_volume_and_mood_legacy_print_nothing_on_success(monkeypatch, capsys):
    client = FakeClient(fake_state())
    shell = FakeShell()
    monkeypatch.setattr("gentle_stream.client.connect", lambda: client)
    monkeypatch.setattr("gentle_stream.bridge.GentleShell", lambda binary=None, model=None: shell)
    assert main(["ctl", "volume", "60"]) == 0
    assert capsys.readouterr().out == ""
    assert main(["ctl", "mood", "rain"]) == 0
    assert capsys.readouterr().out == ""
    assert client.requests[0] == ("set_volume", {"volume": 0.6})


# ------------------------------------------------------------ ctl start --json (the ensure owner)
def test_ctl_start_json_uses_ensure_once_to_attach_or_start(monkeypatch, capsys):
    """`start` is the one ctl action that may spawn; it delegates to client.ensure exactly once, never connect."""
    client = FakeClient(fake_state(playing=True, volume=0.55))
    calls = []
    connected = []
    monkeypatch.setattr("gentle_stream.client.ensure", lambda: calls.append("ensure") or client)
    monkeypatch.setattr("gentle_stream.client.connect", lambda: connected.append("connect") or client)
    assert main(["ctl", "start", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {"ok": True, "running": True, "playing": True, "volume": 55,
                    "name": "Midnight Rain", "genre": "lofi", "mood": ""}
    assert list(data) == ENVELOPE_KEYS
    assert calls == ["ensure"] and connected == []
    assert client.detached is True


def test_ctl_start_json_reports_the_actual_snapshot_not_assumed_success(monkeypatch, capsys):
    client = FakeClient(fake_state(playing=False, volume=0.3), apply=False)   # a player that reports it stayed paused
    monkeypatch.setattr("gentle_stream.client.ensure", lambda: client)
    monkeypatch.setattr("gentle_stream.client.connect",
                        lambda: (_ for _ in ()).throw(AssertionError("start must use ensure, not connect")))
    assert main(["ctl", "start", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["playing"] is False and data["volume"] == 30


def test_ctl_start_json_audio_failure_is_a_safe_unavailable_envelope(monkeypatch, capsys):
    def ensure():
        raise AudioError("Error querying device -1")

    monkeypatch.setattr("gentle_stream.client.ensure", ensure)
    monkeypatch.setattr("gentle_stream.client.connect",
                        lambda: (_ for _ in ()).throw(AssertionError("start must use ensure, not connect")))
    assert main(["ctl", "start", "--json"]) == 1
    out = capsys.readouterr().out
    assert json.loads(out) == {"ok": False,
                               "error": {"code": "unavailable", "message": "the player is unavailable"}}
    assert "device" not in out and "-1" not in out and "Traceback" not in out


def test_ctl_start_json_start_failure_never_leaks_paths_or_secrets(monkeypatch, capsys):
    def ensure():
        raise PlayerError("the player did not start in time (see /secret/player.log)")

    monkeypatch.setattr("gentle_stream.client.ensure", ensure)
    assert main(["ctl", "start", "--json"]) == 1
    out = capsys.readouterr().out
    assert json.loads(out) == {"ok": False,
                               "error": {"code": "unavailable", "message": "the player is unavailable"}}
    assert "/secret" not in out and "player.log" not in out and "Traceback" not in out


def test_ctl_status_still_never_starts_a_player(monkeypatch, capsys):
    """status and play keep attaching only; `start` is the only ctl action that may spawn."""
    monkeypatch.setattr("gentle_stream.client.ensure",
                        lambda: (_ for _ in ()).throw(AssertionError("status must never start a player")))
    monkeypatch.setattr("gentle_stream.client.connect",
                        lambda: (_ for _ in ()).throw(NoPlayer("nothing is playing")))
    assert main(["ctl", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "running": False}
