import signal
from unittest.mock import Mock

import pytest

from gentle_stream import linux_desktop as desktop
from gentle_stream import linux_desktop_child as child


@pytest.fixture
def host(monkeypatch, tmp_path):
    terminal, window, glib = Mock(), Mock(), Mock()
    glib.timeout_add.return_value = 123
    monkeypatch.setattr(desktop.os, 'pidfd_open', Mock(return_value=42), raising=False)
    monkeypatch.setattr(desktop.os, 'waitid', Mock(), raising=False)
    for name, value in {'P_PIDFD': 3, 'WEXITED': 4, 'WNOHANG': 1, 'WNOWAIT': 0x1000000}.items():
        monkeypatch.setattr(desktop.os, name, value, raising=False)
    monkeypatch.setattr(desktop.os, 'close', Mock())
    monkeypatch.setattr(desktop.signal, 'pidfd_send_signal', Mock(), raising=False)
    monkeypatch.setattr(desktop.signal, 'SIGKILL', 9, raising=False)
    preview = desktop.Preview(window, terminal, glib, tmp_path)
    return preview, window, terminal, glib, tmp_path


def test_close_while_vte_is_starting_reaps_only_the_owned_child(host):
    preview, window, terminal, glib, root = host
    preview.start()
    args = terminal.spawn_async.call_args.args
    assert args[1] == str(root)
    assert args[2] == [str(root / 'venv/bin/python'), '-I', '-B', '-m', 'gentle_stream.linux_desktop_child']
    assert args[4] == 0  # no PATH search, shell, external terminal, or daemon
    assert len(args) == 10
    assert args[5:8] == (None, 5000, None)  # child_setup, timeout, cancellable
    assert preview.close() is True
    window.destroy.assert_not_called()
    desktop.signal.pidfd_send_signal.assert_not_called()
    args[-2](terminal, 345, None, None)
    desktop.os.pidfd_open.assert_called_once_with(345)
    desktop.signal.pidfd_send_signal.assert_called_once_with(42, signal.SIGTERM)
    assert glib.timeout_add.call_args.args[0] == 8000
    glib.timeout_add.call_args.args[1]()
    assert desktop.signal.pidfd_send_signal.call_args.args == (42, signal.SIGKILL)
    window.destroy.assert_not_called()
    preview.exited(terminal, 9)
    desktop.os.close.assert_called_once_with(42)
    window.destroy.assert_called_once_with()


def test_normal_child_exit_closes_without_signalling(host):
    preview, window, terminal, glib, _ = host
    preview.start()
    terminal.spawn_async.call_args.args[-2](terminal, 345, None, None)
    preview.exited(terminal, 0)
    window.destroy.assert_called_once_with()
    desktop.signal.pidfd_send_signal.assert_not_called()
    glib.timeout_add.assert_not_called()


def test_close_never_signals_a_pid_reused_after_child_exit(host):
    preview, window, terminal, _, _ = host
    preview.start()
    preview.close()
    desktop.os.waitid.side_effect = ChildProcessError
    terminal.spawn_async.call_args.args[-2](terminal, 345, None, None)
    desktop.os.close.assert_called_once_with(42)
    desktop.signal.pidfd_send_signal.assert_not_called()
    preview.exited(terminal, 0)
    window.destroy.assert_called_once_with()


def test_spawn_error_is_visible_and_does_not_disclose_details(host, capsys):
    preview, window, terminal, _, _ = host
    preview.start()
    terminal.spawn_async.call_args.args[-2](terminal, -1, RuntimeError('private path'), None)
    terminal.feed.assert_called_once_with(b'Could not start the preview. Re-run the installer.\r\n')
    assert capsys.readouterr() == ('', '')
    window.destroy.assert_not_called()
    assert preview.close() is True
    window.destroy.assert_called_once_with()


@pytest.mark.parametrize('fail', [False, True])
def test_child_owns_player_and_cancels_without_startup_model(monkeypatch, capsys, fail):
    from gentle_stream import app, bridge, client, engine, player

    monkeypatch.setattr(client, 'ensure', Mock(side_effect=AssertionError('daemon access')))
    monkeypatch.setattr(client, 'connect', Mock(side_effect=AssertionError('daemon access')))
    make_engine = Mock()
    owned = Mock()
    shell = Mock()
    monkeypatch.setattr(engine, 'Engine', make_engine)
    monkeypatch.setattr(player, 'Player', Mock(return_value=owned))
    monkeypatch.setattr(bridge, 'GentleShell', Mock(return_value=shell))

    class FakeApp:
        return_code = 0

        def __init__(self, audio, moods, initial_mood=None):
            assert (audio, moods, initial_mood) == (owned, shell, None)

        async def run_async(self):
            if fail:
                raise RuntimeError('private path')
            event = Mock()
            self.on_unmount(event)
            event.prevent_default.assert_called_once_with()

    monkeypatch.setattr(app, 'StreamApp', FakeApp)
    monkeypatch.setattr(child, 'install_signals', Mock())
    assert child.main() == int(fail)
    player.Player.assert_called_once_with(make_engine.return_value)
    shell.interpret.assert_not_called()
    shell.interpret_with_usage.assert_not_called()
    shell.cancel.assert_called_once_with()
    owned.close.assert_called_once_with()
    owned.detach.assert_not_called()
    assert capsys.readouterr() == ('', 'gentle-stream: desktop preview failed.\n' if fail else '')
