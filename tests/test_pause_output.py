"""Owned output pause contract: real pipes/supervisor, fake native sink, no devices."""
import os
from pathlib import Path
import sys
import time
import struct
import subprocess
import threading

import numpy as np
import pytest

from gentle_stream import daemon
from gentle_stream.player import Player
from gentle_stream.style import Style
from gentle_stream.windows_audio import WindowsPCMStream


def wait_for(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, 'owned output did not reach expected boundary'
        time.sleep(.005)


class Engine:
    sr, piece, bar, backend = 44100, 1, 0, 'fake'
    style = Style()
    def queue_next(self):
        self.piece += 1
    def render_bar(self):
        self.bar += 1
        return np.repeat((np.arange(200000, dtype=np.float32) / 1000000)[:, None], 2, axis=1)


@pytest.fixture
def owned_output(tmp_path, monkeypatch, request):
    if sys.platform == "win32":
        pytest.skip("POSIX fake-sink pipe tests require a POSIX runtime")
    # The fake models a downstream queue: it retains bytes until the owned process
    # is stopped, rather than pretending silent callbacks flush an existing sink.
    events = tmp_path / 'events'
    sink = tmp_path / 'fake-sink'
    sink.write_text(f'''#!{sys.executable}
import os, signal, sys, time
from pathlib import Path
root = Path({str(tmp_path)!r})
def event(text):
    with (root/'events').open('a') as f: f.write(str(os.getpid())+' '+text+'\\n')
def stop(*args):
    event('stopped')
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
event('started')
with (root/(str(os.getpid())+'.pcm')).open('wb', buffering=0) as f:
    while data := os.read(0, 512):
        f.write(data)
        event('queued')
        if {getattr(request, 'param', '') == 'failed'!r}:
            event('failed')
            sys.exit(3)
        if {getattr(request, 'param', '') == 'blocked'!r}:
            event('blocked')
            while True: time.sleep(.01)
        time.sleep(.002)
event('eof')
''')
    sink.chmod(0o700)
    monkeypatch.setattr('gentle_stream.windows_audio._executables', lambda: (sys.executable, str(sink)))
    engine = Engine()
    if getattr(request, 'param', '') == 'playlist':
        from gentle_stream.engine import Engine as MusicEngine
        engine = MusicEngine(Style(name='One'), seed=42, sr=16000, samples=False)
    p = Player(engine, stream_factory=WindowsPCMStream, blocksize=256)
    if getattr(request, 'param', '') == 'playlist':
        p.set_playlist((Style(name='One'), Style(name='Two')), name='Evening', continuous=True)
    p.next()
    stream = WindowsPCMStream(samplerate=engine.sr, channels=2, dtype='float32',
                              blocksize=256, callback=p._callback)
    p._stream = stream
    stream.start()
    try:
        wait_for(lambda: events.exists() and 'queued' in events.read_text())
        yield p, stream, events, tmp_path
    finally:
        p.close()


sink = owned_output


def test_host_pause_delivers_stop_with_full_pending_queue(monkeypatch):
    import io
    import queue
    import runpy
    from types import SimpleNamespace

    host = runpy.run_path(str(Path(__file__).parents[1] /
                              'src/gentle_stream/_windows_output_host.py'))
    release = threading.Event()
    gated = threading.Event()
    queues, children, writers, effects = [], [], [], []

    class Pending(queue.Queue):
        def __init__(self, maxsize):
            super().__init__(maxsize)
            self.first = not queues
            queues.append(self)

        def get(self, *args, **kwargs):
            if self.first and not gated.is_set():
                gated.set()
                assert release.wait(2), 'stop insertion never released the writer'
            return super().get(*args, **kwargs)

        def put_nowait(self, item):
            try:
                return super().put_nowait(item)
            finally:
                if self.first and item is None:
                    effects.append('stop attempted')
                    release.set()

    class Pipe:
        def __init__(self, child):
            self.child = child
            self.data = bytearray()
            self.closed = False

        def write(self, data):
            self.data.extend(data)
            return len(data)

        def close(self):
            self.closed = True
            if self.child.code is None:
                self.child.code = 0

    class Child:
        def __init__(self, argv, **kwargs):
            assert argv[0] == 'fake-ffplay' and argv[-1] == 'pipe:0'
            self.code = None
            self.stdin = Pipe(self)
            children.append(self)

        def poll(self):
            return self.code

        def terminate(self):
            effects.append('terminate')
            self.code = -15

        def kill(self):
            pytest.fail('fake child should terminate without kill')

        def wait(self, timeout):
            assert timeout == 1
            if len(children) == 1:
                assert gated.wait(2)
                assert queues[0].full(), 'pause cleanup must encounter four queued frames'
                assert not release.is_set(), 'writer resumed before full-queue cleanup'
            effects.append(('wait', self.code))
            return self.code

    def thread(**kwargs):
        writer = threading.Thread(**kwargs)
        writers.append(writer)
        return writer

    def frame(op, sequence, data=b''):
        return host['HEADER'].pack(op, sequence, len(data)) + data

    pcm = bytes(range(8))
    frames = b''.join(frame(b'D', n, pcm) for n in range(1, 5))
    frames += frame(b'P', 5) + frame(b'R', 6)
    frames += frame(b'D', 7, pcm) + frame(b'D', 8, pcm)
    output, errors = io.BytesIO(), io.StringIO()
    globals_ = host['main'].__globals__
    monkeypatch.setitem(globals_, 'sys', SimpleNamespace(
        stdin=SimpleNamespace(buffer=io.BytesIO(frames)),
        stdout=SimpleNamespace(buffer=output), stderr=errors))
    monkeypatch.setitem(globals_, 'subprocess', SimpleNamespace(
        Popen=Child, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL,
        TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setitem(globals_, 'queue', SimpleNamespace(
        Queue=Pending, Full=queue.Full, Empty=queue.Empty))
    monkeypatch.setitem(globals_, 'threading', SimpleNamespace(
        Thread=thread, Event=threading.Event, Lock=threading.Lock))
    try:
        result = host['main'](['fake-ffplay', '44100'])
    finally:
        release.set()
        for work, writer in zip(queues, writers):
            if writer.is_alive():
                work.put_nowait(None)
                writer.join(timeout=2)
        assert all(not writer.is_alive() for writer in writers)
    assert result == 0, errors.getvalue()
    assert errors.getvalue() == ''
    assert output.getvalue() == b''.join(host['ACK'].pack(op, n) for op, n in
                                       [(b'P', 5), (b'R', 6), (b'D', 7), (b'D', 8)])
    assert len(children) == 2
    assert children[0].stdin.data == b''
    assert children[1].stdin.data == pcm * 2
    assert all(child.stdin.closed for child in children)
    assert effects == ['terminate', ('wait', -15), 'stop attempted',
                       'stop attempted', ('wait', 0)]


def test_pause_ack_stops_owned_queued_sink_and_resume_retains_music(owned_output):
    p, stream, events, root = owned_output
    started = time.monotonic()
    response = daemon.handle(p, {'cmd': 'pause'})
    assert response['ok'] and not response['state']['playing']
    # This is the required cut boundary, not just a test for a pause method.
    log = events.read_text().splitlines()
    pid = log[0].split()[0]
    assert pid + ' stopped' in log, 'pause acknowledged while pre-pause queued sink was still alive'
    assert time.monotonic() - started < 3
    held = (p.position, p.now, p.buffered_seconds, p.volume)
    before = (root/(pid+'.pcm')).read_bytes()
    p.pause()
    time.sleep(.06)
    assert (p.position, p.now, p.buffered_seconds, p.volume) == held
    assert (root/(pid+'.pcm')).read_bytes() == before
    p.set_volume(.42)
    p.play()
    p.play()
    wait_for(lambda: events.read_text().count('started') == 2 and p.position > held[0])
    p.pause()
    assert p.volume == .42
    log = events.read_text().splitlines()
    pids = [line.split()[0] for line in log if line.endswith('started')]
    assert len(set(pids)) == 2
    assert all(pid + ' stopped' in log for pid in pids)
    # Restart receives new callback PCM, not a replay of the discarded sink queue.
    resumed = np.frombuffer((root/(pids[1]+'.pcm')).read_bytes(), dtype='<f4')
    assert resumed.size and resumed[0] > 0
    assert resumed[0] != np.frombuffer(before, dtype='<f4')[0]
    print(f'owned sink cut/resume cycle={time.monotonic()-started:.3f}s; held position={held[0]}; pids={pids}; acoustic latency unmeasured')


def test_rapid_toggle_and_close_leave_no_owned_sink(owned_output):
    p, stream, events, root = owned_output
    for _ in range(5):
        p.toggle()
        held = p.position
        time.sleep(.01)
        assert p.position == held
        p.toggle()
    p.close()
    p.close()
    log = events.read_text().splitlines()
    starts = {line.split()[0] for line in log if line.endswith('started')}
    ends = {line.split()[0] for line in log if line.endswith(('stopped', 'eof'))}
    assert starts <= ends
    assert stream._process.poll() is not None
    assert not stream._thread.is_alive()


@pytest.mark.parametrize('owned_output', ['blocked'], indirect=True)
def test_pause_cuts_a_backpressured_native_writer(owned_output, monkeypatch):
    p, stream, events, root = owned_output
    wait_for(lambda: 'blocked' in events.read_text())
    time.sleep(.15)  # Fill the fake sink pipe, not a real output device.
    original = os.write
    monkeypatch.setattr(os, 'write', lambda fd, data: original(fd, data[:17]))
    start = time.monotonic()
    p.pause()
    elapsed = time.monotonic() - start
    assert elapsed < 3
    assert 'stopped' in events.read_text()
    held = (p.position, p.now, p.buffered_seconds)
    time.sleep(.05)
    assert (p.position, p.now, p.buffered_seconds) == held
    p.play()
    wait_for(lambda: events.read_text().count('blocked') == 2)
    p.pause()
    assert events.read_text().count('stopped') == 2
    print(f'backpressured sink stopped before ACK in {elapsed:.3f}s; partial writes capped at 17 bytes')


def test_concurrent_public_controls_and_close_are_serialized(owned_output):
    p, stream, events, root = owned_output
    barrier = threading.Barrier(3)
    errors = []
    def control():
        try:
            barrier.wait(timeout=2)
            for _ in range(3):
                p.pause()
                p.play()
        except Exception as error:
            errors.append(error)
    threads = [threading.Thread(target=control) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=8)
        assert not thread.is_alive()
    assert not errors
    p.close()
    assert stream._process.poll() is not None
    assert not stream._thread.is_alive()


def test_close_racing_pause_resume_has_no_deadlock_or_child(owned_output):
    p, stream, events, root = owned_output
    barrier = threading.Barrier(3)
    errors = []
    def controls():
        barrier.wait(timeout=2)
        try:
            for _ in range(5):
                p.pause()
                p.play()
        except ValueError:
            pass  # A close that wins the race rejects further transport controls.
        except Exception as error:
            errors.append(error)
    def close():
        barrier.wait(timeout=2)
        try:
            p.close()
        except Exception as error:
            errors.append(error)
    threads = [threading.Thread(target=controls), threading.Thread(target=close)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=8)
        assert not thread.is_alive()
    assert not errors
    assert stream._process.poll() is not None
    assert not stream._thread.is_alive()


@pytest.mark.parametrize('message', [
    struct.pack('!cII', b'X', 1, 0),
    struct.pack('!cII', b'P', 1, 8) + bytes(8),
    struct.pack('!cII', b'D', 1, 8193),
    struct.pack('!cII', b'D', 1, 7) + bytes(7),
    struct.pack('!cII', b'D', 0, 8) + bytes(8),
    struct.pack('!cII', b'D', 1, 8) + bytes(4),
    b'D',
])
def test_supervisor_rejects_malformed_bounded_protocol(owned_output, message):
    from gentle_stream import _windows_output_host as host
    p, stream, events, root = owned_output
    result = subprocess.run([sys.executable, '-I', '-B', host.__file__,
                             str(root/'fake-sink'), '44100'], input=message,
                            capture_output=True, timeout=5)
    assert result.returncode == 1
    assert result.stdout == b''
    assert b'Windows audio:' in result.stderr
    assert b'frame' in result.stderr


def test_failed_pause_is_visible_and_never_acknowledged_as_success(owned_output, monkeypatch):
    p, stream, events, root = owned_output
    # Inject an actual broken control write at the transport boundary. Cleanup
    # still delivers EOF and waits for this supervisor's child; no global kill.
    original = stream._write
    def broken(data):
        if data[:1] == b'P':
            raise BrokenPipeError('controlled pause pipe failure')
        return original(data)
    monkeypatch.setattr(stream, '_write', broken)
    response = daemon.handle(p, {'cmd': 'pause'})
    assert response == {'ok': False, 'error': 'bad request: Windows output control failed: controlled pause pipe failure'}
    assert 'controlled pause pipe failure' in p.audio_output_error
    assert daemon.state(p)['playing'] is False
    p.close()
    assert stream._process.poll() is not None
    assert not stream._thread.is_alive()
    held = (p.position, p.now, p.volume, p.buffered_seconds)
    with pytest.raises(ValueError, match='controlled pause pipe failure'):
        p.play()
    assert (p.position, p.now, p.volume, p.buffered_seconds) == held
    assert daemon.handle(p, {'cmd': 'state'})['ok']


@pytest.mark.parametrize('owned_output', ['failed'], indirect=True)
def test_native_sink_exit_surfaces_and_supervisor_exits(owned_output):
    p, stream, events, root = owned_output
    wait_for(lambda: stream.error is not None)
    wait_for(lambda: stream._process.poll() is not None)
    assert not daemon.state(p)['playing']
    assert p.audio_output_error
    p.close()
    assert not stream._thread.is_alive()
    assert 'failed' in events.read_text()


@pytest.mark.parametrize('owned_output', ['playlist'], indirect=True)
def test_pause_holds_continuous_playlist_snapshot_and_volume(owned_output):
    p, stream, events, root = owned_output
    p.pause()
    held = (p.position, p.now, p.buffered_seconds, p.playback_state(),
            p.engine.samples_out, p.engine.bar, p.engine.piece)
    p.set_volume(.31)
    for _ in range(3):
        p.pause()
        assert p.playback_state() == held[3]
        time.sleep(.02)
    assert (p.position, p.now, p.buffered_seconds, p.playback_state(),
            p.engine.samples_out, p.engine.bar, p.engine.piece) == held
    assert p.continuous and p.playlist_name == 'Evening'
    p.play()
    wait_for(lambda: p.position > held[0])
    p.pause()
    assert p.volume == .31 and p.now.playlist_name == 'Evening'
    assert p.now.playlist_index == held[1].playlist_index
