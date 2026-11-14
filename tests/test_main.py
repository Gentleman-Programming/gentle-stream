import shlex
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gentle_stream import main
from tests.test_daemon import runtime  # noqa: F401  (fixture: private runtime dir, fake audio, cleanup)


@pytest.mark.parametrize('argv,message', [
    (['--window-size', '640x240'], '--window-mode'),
    (['--terminal', 'kitty'], '--window-mode'),
    (['--compact', '--mood', 'fog'], 'incompatible'),
    (['--window-mode', 'floating', '--model', 'x/y'], 'incompatible'),
    (['--compact', '--window-mode', 'window'], 'incompatible'),
    (['--window-mode', 'window', 'ctl', 'status'], 'subcommands'),
])
def test_new_invalid_options_do_not_open_audio(monkeypatch, capsys, argv, message):
    from gentle_stream import client, window_launcher

    ensure, launch = Mock(), Mock()
    monkeypatch.setattr(client, 'ensure', ensure)
    monkeypatch.setattr(window_launcher, 'launch_window', launch)
    with pytest.raises(SystemExit) as error:
        main(argv)
    assert error.value.code == 2
    output = capsys.readouterr()
    assert output.out == '' and message in output.err
    ensure.assert_not_called()
    launch.assert_not_called()


@pytest.mark.parametrize('command', [['ctl', 'status', '--compact'], ['setup-samples', '--compact']])
def test_sibling_commands_keep_rejecting_unknown_options(command, capsys):
    with pytest.raises(SystemExit) as error:
        main(command)
    assert error.value.code == 2
    output = capsys.readouterr()
    assert output.out == ''
    assert output.err.endswith('gentle-stream: error: unrecognized arguments: --compact\n')


def test_launcher_failure_is_visible_before_audio(monkeypatch, capsys):
    from gentle_stream import client, window_launcher

    ensure = Mock()
    monkeypatch.setattr(client, 'ensure', ensure)
    monkeypatch.setattr(window_launcher, 'launch_window', Mock(side_effect=window_launcher.LaunchError('dispatch refused')))
    assert main(['--window-mode', 'floating']) == 1
    output = capsys.readouterr()
    assert output.out == '' and output.err == 'gentle-stream: dispatch refused\n'
    ensure.assert_not_called()


def test_launcher_dispatch_precedes_audio(monkeypatch, capsys):
    from gentle_stream import client, window_launcher
    ensure, launch = Mock(), Mock()
    monkeypatch.setattr(client, 'ensure', ensure)
    monkeypatch.setattr(window_launcher, 'launch_window', launch)
    assert main(['--window-mode', 'window', '--mood', 'fog; x', '--gentle-shell', 'a b']) == 0
    launch.assert_called_once_with('window', None, 'kitty', ['--mood=fog; x', '--gentle-shell=a b'])
    ensure.assert_not_called()
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('flag', ['--mood', '--model', '--gentle-shell'])
@pytest.mark.parametrize('value', ['-n', '--compact'])
def test_window_forwarded_values_survive_public_child_parser(monkeypatch, capsys, tmp_path, flag, value):
    from gentle_stream import bridge, client, window_launcher
    from gentle_stream.app import StreamApp

    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    ensure = Mock()
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout='ok', stderr=''))
    monkeypatch.setattr(client, 'ensure', ensure)
    monkeypatch.setenv('HYPRLAND_INSTANCE_SIGNATURE', 'test')
    monkeypatch.setattr(window_launcher.shutil, 'which', lambda name: '/bin/' + name)
    monkeypatch.setattr(window_launcher.subprocess, 'run', run)
    assert main(['--window-mode', 'window', f'{flag}={value}']) == 0
    ensure.assert_not_called()
    command = shlex.split(run.call_args.args[0][-1])
    # Locate the actual Python module invocation, not positional Kitty tokens.
    module = command.index('-m')
    assert command[module + 1] == 'gentle_stream'
    child_args = command[module + 2:]
    shell = Mock(side_effect=lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(bridge, 'GentleShell', shell)
    seen = []
    monkeypatch.setattr(StreamApp, 'run', lambda app: seen.append(app))
    assert main(child_args) == 0
    assert f'{flag}={value}' in child_args
    ensure.assert_called_once()
    assert len(seen) == 1
    if flag == '--mood':
        assert seen[0].initial_mood == value
    else:
        assert getattr(seen[0].shell, 'model' if flag == '--model' else 'binary') == value
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('mode', [['--compact'], ['--window-mode', 'floating']])
@pytest.mark.parametrize('override', [['--gentle-shell', 'custom shell'],
                                    ['--gentle-shell=gentle-shell'], ['--gentle-shell=-n']])
def test_explicit_shell_override_rejected_before_audio_or_compositor(monkeypatch, capsys, mode, override):
    from gentle_stream import client, window_launcher
    from gentle_stream.compact_app import CompactStreamApp

    ensure, launch, run = Mock(), Mock(), Mock()
    monkeypatch.setattr(client, 'ensure', ensure)
    monkeypatch.setattr(window_launcher, 'launch_window', launch)
    monkeypatch.setattr(CompactStreamApp, 'run', run)
    with pytest.raises(SystemExit) as error:
        main([*mode, *override])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert output.out == ''
    assert output.err.endswith('gentle-stream: error: explicit --gentle-shell is incompatible with compact/floating mode (no shell invocation); use window mode\n')
    ensure.assert_not_called()
    launch.assert_not_called()
    run.assert_not_called()


def test_floating_implicit_shell_default_remains_valid(monkeypatch, capsys):
    from gentle_stream import client, window_launcher

    ensure, launch = Mock(), Mock()
    monkeypatch.setattr(client, 'ensure', ensure)
    monkeypatch.setattr(window_launcher, 'launch_window', launch)
    assert main(['--window-mode', 'floating']) == 0
    launch.assert_called_once_with('floating', None, 'kitty', [])
    ensure.assert_not_called()
    assert capsys.readouterr() == ('', '')


def test_compact_dispatch_has_no_shell(monkeypatch):
    from gentle_stream import client
    from gentle_stream.bridge import GentleShell
    from gentle_stream.compact_app import CompactStreamApp
    ensure, run, shell = Mock(), Mock(), Mock()
    monkeypatch.setattr(client, 'ensure', ensure)
    monkeypatch.setattr(CompactStreamApp, 'run', run)
    monkeypatch.setattr(GentleShell, '__init__', shell)
    assert main(['--compact']) == 0
    ensure.assert_called_once()
    run.assert_called_once()
    shell.assert_not_called()


def test_help_lists_flags_and_controls(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    out = capsys.readouterr().out
    assert exit_info.value.code == 0
    for text in ("--model", "--mood", "--gentle-shell", "play / pause", "next piece", "describe a mood",
                 "save what is playing", "saved prompts", "presets.json", "the music keeps playing",
                 "quit and stop the music", "ctl", "play|pause|toggle|next|stop|status",
                 "underruns", "output_underflows", "buffered_seconds", "null with older players"):
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
