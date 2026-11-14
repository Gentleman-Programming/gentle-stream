import shlex
from unittest.mock import Mock

import pytest

from gentle_stream.window_launcher import LaunchError, launch_window, parse_window_size


@pytest.mark.parametrize('text', ['0x5', '-1x5', '5x0', '99999x20', '20X30', 'x', '1x2x3'])
def test_invalid_dimensions(text):
    with pytest.raises(ValueError, match='WIDTHxHEIGHT'):
        parse_window_size(text)


@pytest.mark.parametrize('mode', ['window', 'floating'])
def test_launch_exact_classic_contract(monkeypatch, mode):
    monkeypatch.setenv('HYPRLAND_INSTANCE_SIGNATURE', 'test')
    monkeypatch.setattr('gentle_stream.window_launcher.shutil.which', lambda name: '/bin/' + name)
    run = Mock(return_value=Mock(returncode=0, stderr='', stdout='ok'))
    monkeypatch.setattr('gentle_stream.window_launcher.subprocess.run', run)
    args = ['--mood', 'fog; $(touch nope)', '--model', 'x/y', '--gentle-shell', '/a b/gs']
    launch_window(mode, (640, 240), 'kitty', args)
    command = run.call_args.args[0]
    assert command[:3] == ['/bin/hyprctl', 'dispatch', 'exec']
    rules, serialized = command[3].split('] ', 1)
    assert rules == ('[float; size 640 240' if mode == 'floating' else '[size 640 240')
    tokens = shlex.split(serialized)
    title = 'gentle-dot' if mode == 'floating' else 'gentle-stream'
    assert tokens[:5] == ['/bin/kitty', '--class', title, '--title', title]
    assert tokens[-len(args):] == args
    assert tokens.count('--compact') == int(mode == 'floating')
    assert '--window-mode' not in tokens
    assert run.call_args.kwargs['timeout'] == 10
    assert not run.call_args.kwargs.get('shell', False)


@pytest.mark.parametrize('failure', ['session', 'kitty', 'hyprctl', 'compositor', 'os'])
def test_launch_failure_visible(monkeypatch, failure):
    monkeypatch.delenv('HYPRLAND_INSTANCE_SIGNATURE', raising=False)
    if failure != 'session':
        monkeypatch.setenv('HYPRLAND_INSTANCE_SIGNATURE', 'test')
    monkeypatch.setattr('gentle_stream.window_launcher.shutil.which',
                        lambda name: None if name == failure else '/bin/' + name)
    run = Mock(return_value=Mock(returncode=1, stderr='dispatch refused', stdout=''))
    if failure == 'os':
        run.side_effect = OSError('cannot execute')
    monkeypatch.setattr('gentle_stream.window_launcher.subprocess.run', run)
    with pytest.raises(LaunchError):
        launch_window('floating', None, 'kitty', [])
    if failure in ('session', 'kitty', 'hyprctl'):
        run.assert_not_called()
