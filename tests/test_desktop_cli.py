"""Desktop opt-in rejects before optional imports or player/store effects."""
import sys
from unittest.mock import Mock

import pytest

import gentle_stream as cli


@pytest.mark.parametrize('args,message', [
    (['--desktop', '--compact'], '--desktop is incompatible with --compact and --window-mode'),
    (['--desktop', '--window-mode', 'window'], '--desktop is incompatible with --compact and --window-mode'),
    (['--desktop', 'ctl', 'status'], '--desktop cannot be combined with subcommands'),
])
def test_conflicts_before_effects(args, message, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    before = set(sys.modules)
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
    out, err = capsys.readouterr()
    assert out == ''
    assert err.endswith('gentle-stream: error: ' + message + '\n')
    assert '--desktop' in err
    assert not list(tmp_path.iterdir())
    assert not ({'webview', 'winpty', 'gentle_stream.desktop_session'} & (set(sys.modules) - before))


def test_nonwindows_preimport(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    assert cli.main(['--desktop']) == 1
    assert capsys.readouterr() == ('', 'gentle-stream: --desktop requires native Windows; use ordinary gentle-stream on this platform\n')
    assert not list(tmp_path.iterdir())


def test_forward_list_and_ordinary_ctl_preserved(monkeypatch, capsys):
    from gentle_stream import desktop_session
    launch = Mock(return_value=0)
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(desktop_session, 'launch', launch)
    assert cli.main(['--desktop', '--mood=-forest', '--model=p/m', '--gentle-shell=C:/shell with spaces.mjs']) == 0
    launch.assert_called_once_with(['--mood=-forest', '--model=p/m', '--gentle-shell=C:/shell with spaces.mjs'])
    control = Mock(return_value=0)
    monkeypatch.setattr(cli, 'ctl', control)
    assert cli.main(['ctl', 'status']) == 0
    control.assert_called_once_with('status')
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('args,message', [
    (['--window-size=360x180'], '--window-size and --terminal require --window-mode'),
    (['--compact', '--model=p/m'], '--mood/--model are incompatible with compact/floating mode (no mood interpretation); use window mode'),
    (['--compact', '--gentle-shell=launcher'], 'explicit --gentle-shell is incompatible with compact/floating mode (no shell invocation); use window mode'),
])
def test_legacy_validation_preserved(args, message, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
    out, err = capsys.readouterr()
    assert out == '' and err.endswith('gentle-stream: error: ' + message + '\n')


def test_setup_samples_preserved(monkeypatch, capsys, tmp_path):
    from gentle_stream import samples
    path = tmp_path / 'FluidR3_GM.sf2'
    monkeypatch.setattr(samples, 'soundfont_path', lambda: path)
    assert cli.main(['setup-samples']) == 0
    out, err = capsys.readouterr()
    assert out == f'FluidR3_GM already installed: {path} (license: {path.parent / samples.LICENSES_NAME})\n'
    assert err == '' and not list(tmp_path.iterdir())


def test_actual_help(capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(['--help'])
    assert error.value.code == 0
    out, err = capsys.readouterr()
    assert '--desktop' in out and 'Windows desktop' in out and err == ''
