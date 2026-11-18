"""Public channel regressions; no App, daemon, audio or native GUI."""
import os
import threading

import pytest

from gentle_stream import desktop_control as control


def frame(mode='full'):
    return {'epoch': 1, 'cols': 80, 'rows': 24, 'mode': mode,
            'theme': ['#000000', '#ffffff', '#ff0000'], 'regions': []}


@pytest.fixture
def channel(tmp_path, monkeypatch):
    # All channel state is project-owned test state, never the user's home.
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))
    channel = control.SessionChannel.create(tmp_path / 'channel')
    channel.request_layout(1, 80, 24)
    channel.publish(frame())
    channel.request_toggle()
    return channel


def test_public_controls_preserve_roundtrip_and_rejection(channel, capsys):
    peer = control.SessionChannel.open(channel.root, channel.nonce)
    assert peer.layout() == [1, 80, 24] and peer.state() == frame()
    with pytest.raises(ValueError, match='^Invalid desktop session$'):
        peer.publish({**frame(), 'regions': [[0, 0, 81, 1]]})
    with pytest.raises(ValueError, match='^Invalid desktop session$'):
        control.SessionChannel.open(channel.root, '0' * 48)
    assert peer.state() == frame() and peer.layout() == [1, 80, 24]
    assert peer.take_toggle() and not peer.take_toggle() and not peer.stopping()
    channel.request_toggle()
    assert peer.take_toggle() and not peer.take_toggle()
    channel.publish(frame('mini'))
    assert peer.state() == frame('mini')
    channel.request_layout(2, 60, 12)
    assert peer.layout() == [2, 60, 12] and peer.state() is None
    channel.request_shutdown()
    assert peer.stopping()
    # Vary UTF-16 path length/alignment, including a non-BMP character. Each
    # fresh identity and initially absent stop record must publish at its exact
    # name, not a name extended by bytes beyond the native rename buffer.
    for suffix in ('a', 'ab', 'abc', 'abcd'):
        fresh = control.SessionChannel.create(channel.root / ('peer-\U0001f3b5-' + suffix))
        reader = control.SessionChannel.open(fresh.root, fresh.nonce)
        assert reader.layout() is None and reader.state() is None
        assert not reader.stopping() and not reader.take_toggle()
        with pytest.raises(ValueError, match='^Invalid desktop session$'):
            control.SessionChannel.open(fresh.root, '0' * 48)
        fresh.request_layout(1, 80, 24)
        fresh.publish(frame())
        fresh.request_toggle()
        fresh.request_shutdown()
        assert reader.layout() == [1, 80, 24] and reader.state() == frame()
        assert reader.stopping() and reader.take_toggle() and not reader.take_toggle()
        assert control.SessionChannel.open(fresh.root, fresh.nonce).stopping()
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('operation', ['publish', 'layout', 'toggle', 'shutdown'])
def test_replace_denial_is_observable_once_without_committing_controls(
        channel, monkeypatch, capsys, operation):
    peer = control.SessionChannel.open(channel.root, channel.nonce)
    calls = []
    denied = PermissionError(13, 'controlled replacement denial')
    denied.winerror = 5

    def fail_replace(source, target):
        calls.append(True)
        raise denied

    actions = {'publish': lambda: channel.publish(frame('mini')),
               'layout': lambda: channel.request_layout(2, 60, 12),
               'toggle': channel.request_toggle,
               'shutdown': channel.request_shutdown}
    with monkeypatch.context() as patch:
        patch.setattr(control, '_replace', fail_replace)
        with pytest.raises(PermissionError) as caught:
            actions[operation]()
        assert caught.value is denied and caught.value.winerror == 5
        assert calls == [True]  # No hidden retry, wait or dropped success.
    assert channel.layout() == peer.layout() == [1, 80, 24]
    assert channel.state() == peer.state() == frame()
    assert not channel.stopping() and not peer.stopping()
    assert peer.take_toggle() and not peer.take_toggle()
    # The same writer must accept a fresh layout after a rejected publication;
    # no in-memory accepted layout or counter may have advanced on denial.
    channel.request_layout(2, 60, 12)
    assert channel.layout() == peer.layout() == [2, 60, 12]
    assert channel.state() is None and peer.state() is None
    channel.request_layout(1, 80, 24)
    assert channel.state() == peer.state() == frame()
    # Explicit subsequent publication is fresh, not stale/partial pending data.
    channel.publish(frame('mini'))
    assert peer.state() == frame('mini')
    channel.request_toggle()
    assert peer.take_toggle() and not peer.take_toggle()
    assert capsys.readouterr() == ('', '')


@pytest.mark.skipif(os.name != 'nt', reason='requires actual Windows file sharing')
def test_windows_reader_held_open_allows_atomic_publication(channel, monkeypatch, capsys):
    peer = control.SessionChannel.open(channel.root, channel.nonce)
    opened, release = threading.Event(), threading.Event()
    observed, errors = [], []
    open_read = control._open_read

    def hold_read(path):
        stream = open_read(path)  # Real production OS handle, not a fake file.
        if path.name == 'frame.json':
            opened.set()
            if not release.wait(2):
                stream.close()
                raise TimeoutError('reader release deadline')
        return stream

    def read_state():
        try:
            observed.append(peer.state())
        except Exception as exc:
            errors.append(type(exc).__name__)

    with monkeypatch.context() as patch:
        patch.setattr(control, '_open_read', hold_read)
        reader = threading.Thread(target=read_state, daemon=True)
        reader.start()
        try:
            assert opened.wait(2), 'reader open deadline'
            # Eight finite replacements while the original frame handle stays
            # open. The reader must keep the complete old snapshot, not a mix.
            for index in range(8):
                channel.publish(frame('mini' if index % 2 else 'full'))
                assert reader.is_alive() and not release.is_set()
                assert errors == []  # The real old handle remains retained.
        finally:
            release.set()
            reader.join(2)
        assert not reader.is_alive(), 'reader join deadline'
    assert errors == [] and observed == [frame()]
    assert peer.state() == frame('mini') and peer.layout() == [1, 80, 24]
    assert peer.take_toggle() and not peer.take_toggle() and not peer.stopping()
    assert capsys.readouterr() == ('', '')
