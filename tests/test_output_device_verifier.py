"""S1 verifier probes with fake sinks and an isolated daemon."""
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import numpy as np
import pytest

from gentle_stream import audio_devices, client, daemon, platform_runtime
from gentle_stream.engine import Engine
from gentle_stream.player import Player
from gentle_stream.style import Style


def query(kind=None):
    rows = [(0, "Monitor", 2), (1, "Input", 0), (7, "Headset A", 2), (9, "Headset B", 2)]
    output = [{'index': i, 'name': n, 'max_output_channels': count} for i, n, count in rows]
    return output[0] if kind else output


class Sink:
    def __init__(self, callback, device=None, **kw):
        self.cb, self.index, self.active = callback, device, False
        self.closed = False

    def pull(self):
        out = np.full((32, 2), -123, dtype=np.float32)
        self.cb(out, len(out), None, SimpleNamespace(output_underflow=True))
        return out

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    abort = stop

    def close(self):
        self.active = False
        self.closed = True


class Bridge(Sink):
    audio_output = 'windows'
    reports_output_underflows = False
    pause = Sink.stop
    resume = Sink.start


def ring(p):
    with p._lock:
        return (p.position, p._offset, p._buffered, p.now, p.underruns, p.output_underflows,
                tuple((c.meta, hashlib.sha256(c.audio.tobytes()).hexdigest()) for c in p._chunks))


def sha():
    return hashlib.sha256(audio_devices.config_path().read_bytes()).hexdigest()


@pytest.fixture
def probe(monkeypatch):
    with TemporaryDirectory(prefix='gs-o-') as directory:
        root = Path(directory)
        monkeypatch.setenv('XDG_CONFIG_HOME', str(root / 'config'))
        monkeypatch.setenv('XDG_RUNTIME_DIR', str(root))
        monkeypatch.setenv('XDG_CACHE_HOME', str(root / 'cache'))
        monkeypatch.setattr(audio_devices, '_query', lambda: query)
        yield root


@contextmanager
def session(factory, remembered=False):
    eng = Engine(Style(name='Start'), seed=19, sr=16000, samples=False)
    p = Player(eng, stream_factory=factory, prebuffer=.005, remember_device=remembered)
    p.set_playlist([Style(name='Start'), Style(name='Second')], name='Set', continuous=True)
    p.start()
    try:
        yield p
    finally:
        p.close()


def test_inflight_rollback_fences_retired_and_failed_callbacks(probe):
    entered, release, reading = (threading.Event() for _ in range(3))
    seen = []

    class Held(Sink):
        def start(self):
            super().start()
            if self.index == 9:
                entered.set()
                assert release.wait(3)
                np.testing.assert_array_equal(self.pull(), 0)
                raise OSError('sink got unplugged')

    def factory(**kw):
        sink = Held(**kw)
        seen.append(sink)
        return sink

    with session(factory, True) as p, ThreadPoolExecutor(3) as pool:
        p.set_device(7)
        p.set_volume(.125)
        seen[-1].pull()
        original, saved = ring(p), sha()
        moving = pool.submit(daemon.handle, p, {'cmd': 'set_device', 'device': 9})

        def read():
            reading.set()
            return daemon.handle(p, {'cmd': 'state', 'bands': 3})

        try:
            assert entered.wait(3)
            state = pool.submit(read)
            assert reading.wait(3) and not state.done()
            assert seen[-2].closed
            np.testing.assert_array_equal(seen[-2].pull(), 0)
            np.testing.assert_array_equal(seen[-1].pull(), 0)
            assert ring(p) == original and sha() == saved
        finally:
            release.set()
        assert moving.result(3) == {'ok': False, 'error': 'Cannot switch audio output: sink got unplugged'}
        output = state.result(3)['state']
        assert (output['device'], output['playing'], output['volume'], output['playlist_name']) == ('Headset A', True, .125, 'Set')
        assert len(output['spectrum']) == 3 and ring(p) == original and sha() == saved
        assert [s.index for s in seen if s.active] == [7]
        for stale in seen[:-1]:
            np.testing.assert_array_equal(stale.pull(), 0)
        seen[-1].pull()
        assert p.position == original[0] + 32


def test_bridge_rejection_keeps_persisted_native_choice_and_transport(probe, monkeypatch):
    audio_devices.save_choice('Headset A')
    saved = sha()

    def forbidden():
        pytest.fail('native query attempted by FFplay bridge')

    monkeypatch.setattr(audio_devices, '_query', forbidden)
    with session(Bridge, True) as p:
        for request in ({'cmd': 'devices'}, {'cmd': 'set_device', 'device': None}, {'cmd': 'set_device', 'device': 7}):
            assert daemon.handle(p, request) == {'ok': False, 'error': 'Audio output selection is not supported by the Windows FFplay bridge'}
            assert sha() == saved
        assert daemon.handle(p, {'cmd': 'pause'})['state']['playing'] is False
        assert daemon.handle(p, {'cmd': 'play'})['state']['playing'] is True
        assert p.audio_output == 'windows'


def daemon_command(factory):
    env = dict(os.environ, GENTLE_STREAM_DEVICE_QUERY=f'{__name__}:query',
               GENTLE_STREAM_AUDIO_FACTORY=f'{__name__}:{factory.__name__}')
    root = Path(__file__).resolve().parents[1]
    env['PYTHONPATH'] = os.pathsep.join((str(root / 'src'), str(root)))
    return [sys.executable, '-B', '-m', 'gentle_stream', 'daemon', '--ready-stdout'], env


@contextmanager
def background(factory=Sink):
    command, env = daemon_command(factory)
    with subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as proc:
        player = None
        try:
            assert proc.stdout.readline() == '{"ready": true}\n'
            player = client.connect()
            yield player
            player.close()
            assert proc.wait(timeout=5) == 0
            assert proc.stderr.read() == ''
        finally:
            if player is not None:
                player.detach()
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)


def test_public_socket_protocol_and_rejected_requests_do_not_rewrite(probe):
    with background() as p:
        q = client.connect()
        try:
            assert [(d.index, d.name, d.default) for d in q.devices()] == [(0, 'Monitor', True), (7, 'Headset A', False), (9, 'Headset B', False)]
            pid = p.pid
            p.set_volume(.375)
            p.pause()
            p.set_device(0)
            saved = sha()
            for choice, message in [('', 'no audio output device matches “”'), (-1, 'no audio output device matches “-1”'),
                                    (999999, 'no audio output device matches “999999”'), ('Headset', '“Headset” matches several audio output devices: Headset A, Headset B'),
                                    ({'index': 7}, 'bad request: device must be a name, an index or null')]:
                with pytest.raises(client.PlayerError) as error:
                    q.set_device(choice)
                assert str(error.value) == message and sha() == saved
                assert q.device == 'Monitor'
            p.set_device(9)
            reply = q.request('state', bands=2)
            assert (reply['pid'], reply['playing'], reply['volume'], reply['device']) == (pid, False, .375, 'Headset B')
            p.set_device(None)
            assert p.device is None and json.loads(audio_devices.config_path().read_bytes()) == {'device': None}
        finally:
            q.detach()


def test_duplicate_device_name_warns_and_falls_back_to_default(probe, monkeypatch):
    def duplicates(kind=None):
        rows = [query('output')] + [{'index': index, 'name': 'USB Audio', 'max_output_channels': 2, 'hostapi': api}
                                   for index, api in [(7, 0), (9, 1)]]
        return rows[0] if kind else rows

    monkeypatch.setattr(audio_devices, '_query', lambda: duplicates)
    opened = []

    def factory(**kw):
        sink = Sink(**kw)
        opened.append(duplicates('output')['index'] if sink.index is None else sink.index)
        return sink

    with session(factory, True) as p:
        p.set_device(9)
        assert opened == [0, 9] and p.device == 'USB Audio'
        saved = sha()
    with session(factory, True) as p:
        assert (p.device, p.device_missing, p.playing) == (None, 'USB Audio', True)
        assert opened == [0, 9, 0] and sha() == saved


@pytest.mark.parametrize('can_restore', [True, False])
def test_rollback_does_not_claim_old_device_for_reused_index(probe, monkeypatch, can_restore):
    inventory = {0: 'Monitor', 7: 'Headset A', 9: 'Headset B'}

    def dynamic(kind=None):
        rows = [{'index': i, 'name': n, 'max_output_channels': 2} for i, n in inventory.items()]
        return rows[0] if kind else rows

    monkeypatch.setattr(audio_devices, '_query', lambda: dynamic)
    opened = []

    def factory(**kw):
        index = kw.get('device')
        if index == 9:
            # The inventory changes after resolving the target; rollback must query again.
            inventory[7], inventory[8] = 'Other Speaker', 'Headset A'
            if not can_restore:
                inventory[10] = 'Headset A'
            raise OSError('target busy')
        sink = Sink(**kw)
        opened.append((inventory[index if index is not None else 0], sink))
        return sink

    with session(factory, True) as p:
        p.set_device(7)
        saved = sha()
        outcome = daemon.handle(p, {'cmd': 'set_device', 'device': 9})
        message = 'Cannot switch audio output: target busy'
        state = daemon.handle(p, {'cmd': 'state'})['state']
        active = [(name, sink.index) for name, sink in opened if sink.active]
        if can_restore:
            assert (state['device'], state['playing'], active) == ('Headset A', True, [('Headset A', 8)])
        else:
            message += '; previous output could not reopen: “Headset A” is unavailable or ambiguous'
            assert (state['device'], state['playing'], active) == (None, False, [])
            assert state['audio_output_error'] == message
        assert not any(name == 'Other Speaker' for name, _ in opened)
        assert outcome == {'ok': False, 'error': message} and sha() == saved


def test_two_open_failures_serialize_pause_then_allow_recovery(probe):
    old_closing, release, pausing = (threading.Event() for _ in range(3))
    opened, bad = [], set()

    class HeldStop(Sink):
        def stop(self):
            if self.index == 7 and self.active:
                old_closing.set()
                assert release.wait(3)
                np.testing.assert_array_equal(self.pull(), 0)
            super().stop()

    def factory(**kw):
        index = kw.get('device')
        if index in bad:
            raise OSError(f'could not open {index}')
        result = HeldStop(**kw)
        opened.append(result)
        return result

    with session(factory, True) as p, ThreadPoolExecutor(2) as pool:
        p.set_device(7)
        opened[-1].pull()
        saved, before = sha(), ring(p)
        bad.update([7, 9])
        switch = pool.submit(daemon.handle, p, {'cmd': 'set_device', 'device': 9})

        def pause():
            pausing.set()
            return daemon.handle(p, {'cmd': 'pause'})

        try:
            assert old_closing.wait(3)
            concurrent_pause = pool.submit(pause)
            assert pausing.wait(3) and not concurrent_pause.done()
        finally:
            release.set()
        message = 'Cannot switch audio output: could not open 9; previous output could not reopen: could not open 7'
        assert switch.result(3) == {'ok': False, 'error': message}
        state = concurrent_pause.result(3)['state']
        assert (state['audio_output_error'], state['playing'], state['device']) == (message, False, None)
        assert sha() == saved and ring(p) == before and not any(s.active for s in opened)
        p.set_device(None)
        assert p.audio_output_error is None and not p.playing
        np.testing.assert_array_equal(opened[-1].pull(), 0)
        p.play()
        opened[-1].pull()
        assert p.position == before[0] + 32


class BusySink(Sink):
    def start(self):
        if self.index == 9:
            raise OSError('target busy')
        super().start()


@pytest.mark.skipif(platform_runtime.WINDOWS, reason='AF_UNIX daemon probe')
@pytest.mark.parametrize('action', ['switch', 'rollback', 'panic'])
def test_staged_mood_survives_output_changes_but_not_panic(probe, action):
    with background(BusySink) as p:
        p.set_device(7)
        mood = Style(name='Prepared', mood='distant rain')
        with p.stage_style(mood) as publish:
            if action == 'switch':
                p.set_device(0)
                assert p.device == 'Monitor' and p.playing
            elif action == 'rollback':
                with pytest.raises(client.PlayerError, match='^Cannot switch audio output: target busy$'):
                    p.set_device(9)
                assert p.device == 'Headset A' and p.playing
            else:
                p.panic()
            deadline = time.monotonic() + 3
            committed = publish()
            while committed is False and time.monotonic() < deadline:
                time.sleep(.005)
                committed = publish()
            assert committed is (None if action == 'panic' else True)
        p.next()
        rendered = Style.from_dict(p.request('state')['style'])
        if action == 'panic':
            assert rendered.name != mood.name and rendered.mood != mood.mood
        else:
            assert rendered == mood


class BrokenSink(Sink):
    def record(self, action):
        with (Path(os.environ['XDG_RUNTIME_DIR']) / 'events').open('a', encoding='utf-8') as events:
            print(action, file=events)

    def start(self):
        self.record('start')
        raise OSError('sink vanished while starting')

    def stop(self):
        self.record('stop')
        super().stop()

    def close(self):
        self.record('close')
        raise OSError('sink closure failed')


def test_start_and_cleanup_failure_still_emit_structured_audio_error(probe):
    command, env = daemon_command(BrokenSink)
    proc = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
    assert (proc.returncode, proc.stdout, proc.stderr) == (1, '{"error": "sink closure failed", "audio": true}\n', '')
    assert (probe / 'events').read_text(encoding='utf-8').splitlines() == ['start', 'close', 'stop', 'close']
    paths = daemon.Paths(daemon.runtime_dir())
    assert not paths.sock.exists() and not paths.key.exists()
