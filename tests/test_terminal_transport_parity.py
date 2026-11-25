"""Desktop transport behavior over the terminal JSON contract."""
import pytest

from tests.test_playlist_transport import Loopback, SONGS, output, protocol


def test_repeat_changes_only_rotation_and_previous_still_works(output, protocol):
    p, stream = output
    client, daemon = protocol
    connection = Loopback(lambda request: daemon.handle(p, request))
    remote = client.PlayerClient(connection)
    before = daemon.state(p)
    counters = p.position, p.underruns, p.buffered_seconds
    with pytest.raises(client.PlayerError, match=r'^bad request: Select a song in Playlist \(p\) before enabling Repeat\.$'):
        remote.set_continuous(True)
    assert daemon.state(p) == before
    assert (p.position, p.underruns, p.buffered_seconds) == counters
    remote.pause()
    remote.set_playlist(SONGS, start_index=1, name='Ordered')
    before = remote.request('state')
    remote.set_continuous(True)
    assert remote.request('state') == {**before, 'continuous': True}
    remote.next()
    assert (remote.playlist_index, remote.style) == (2, SONGS[2])
    remote.set_continuous(False)
    remote.previous()
    assert (remote.playlist_index, remote.style) == (1, SONGS[1])
    assert not remote.playing and not stream.pull().any() and p.position == 0
    assert remote.request('stop') is None


def test_panic_rewinds_audible_song_not_pending_selection(output, protocol):
    p, old_stream = output
    client, daemon = protocol
    remote = client.PlayerClient(Loopback(lambda request: daemon.handle(p, request)))
    remote.set_volume(0.37)
    remote.set_playlist(SONGS, start_index=2, name='Ordered', continuous=True)
    remote.previous()
    old_stream.pull()
    remote.spectrum(8)
    audible = remote.now
    seed = p.engine.seed
    remote.pause()
    remote.next()
    assert remote.playlist_index == 2 and audible.playlist_index == 1
    remote.panic()
    assert (remote.now.piece, remote.now.bar, remote.now.style) == (audible.piece, 0, SONGS[1])
    assert (remote.playlist_name, remote.playlist_index, remote.continuous) == ('Ordered', 1, True)
    assert p.engine.seed == seed and not remote.playing and p.position == 0
    assert remote.volume == 0.37
    remote.play()
    assert not old_stream.active and not old_stream.pull().any() and p.position == 0
    remote.next()
    assert remote.playlist_index == 2


def test_transport_capability_for_older_daemon_and_rejected_repeat(output, protocol):
    p, _ = output
    client, daemon = protocol
    before = daemon.state(p)
    counters = p.position, p.underruns, p.buffered_seconds
    assert daemon.handle(p, {'cmd': 'set_continuous', 'enabled': 1}) == {
        'ok': False, 'error': 'bad request: continuous must be a boolean'}
    assert daemon.state(p) == before
    assert (p.position, p.underruns, p.buffered_seconds) == counters
    before.pop('transport_parity_supported')
    connection = Loopback(lambda request: {'ok': True, 'state': before})
    legacy = client.PlayerClient(connection)
    assert not legacy.transport_parity_supported
    with pytest.raises(client.PlayerError, match='^Repeat is not supported by this daemon$'):
        legacy.set_continuous(False)
    with pytest.raises(client.PlayerError, match='^Panic restart is not supported by this daemon$'):
        legacy.panic()
    assert connection.commands == ['state']


def test_existing_ctl_envelopes_do_not_expose_transport_fields(output, protocol, monkeypatch, capsys):
    from gentle_stream import main

    p, _ = output
    client, daemon = protocol
    p.set_playlist(SONGS, name='Ordered', continuous=True)
    monkeypatch.setattr(client, 'connect', lambda: client.PlayerClient(Loopback(lambda req: daemon.handle(p, req))))
    assert main(['ctl', 'status']) == 0
    assert capsys.readouterr() == (
        '{"running": true, "playing": true, "name": "Standalone", "genre": "lofi", "mood": ""}\n', '')
    assert main(['ctl', 'volume', '37', '--json']) == 0
    assert capsys.readouterr() == (
        '{"ok": true, "running": true, "playing": true, "volume": 37, "name": "Standalone", "genre": "lofi", "mood": ""}\n', '')
    assert p.volume == 0.37
