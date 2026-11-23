"""Regression checks using the real daemon, fake sinks, and public client/JSON."""
import hashlib
import json
import os
import select
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from gentle_stream import client, daemon
from gentle_stream.style import Style


def inventory():
    return json.loads(Path(os.environ['INVENTORY']).read_bytes())


def query(kind=None):
    data = inventory()
    rows = [dict(index=index, name=name, max_output_channels=2, **({"hostapi": api} if api is not None else {})) for index, name, api in data['sinks']]
    return next(d for d in rows if d['index'] == data['default']) if kind == 'output' else rows


def event(*args):
    with Path(os.environ['EVENTS']).open('a') as out:
        print(json.dumps(args), file=out)


class Sink:
    streams = []
    monitoring = False

    def __init__(self, callback, **kw):
        self.cb = callback
        settings = inventory()
        self.requested = kw.get('device')
        self.actual = self.requested if self.requested is not None else settings['default']
        self.name = next(name for idx, name, _ in settings['sinks'] if idx == self.actual)
        self.serial = len(self.streams)
        self.streams.append(self)
        event('open', self.serial, self.requested, self.actual, self.name)

    def start(self):
        event('start', self.serial)
        if not self.monitoring:
            Sink.monitoring = True
            threading.Thread(target=self.monitor, daemon=True).start()
        self.pull('start-inline', underflow=True)
        if self.actual in inventory()['fail']:
            raise OSError(f'refused sink {self.actual}')

    @classmethod
    def monitor(cls):
        cmd = Path(os.environ['PUMP'])
        prior = None
        while True:
            if cmd.exists():
                token, serial = json.loads(cmd.read_bytes())
                if token != prior:
                    cls.streams[serial].pull(token, underflow=False)
                    prior = token
            time.sleep(.004)

    def pull(self, token, underflow):
        out = np.full((4096, 2), -1, dtype=np.float32)
        self.cb(out, len(out), None, SimpleNamespace(output_underflow=underflow))
        event('pull', self.serial, token, bool(np.any(out)))

    def stop(self):
        self.pull('retiring', underflow=True)
        event('stop', self.serial)

    abort = stop

    def close(self):
        event('close', self.serial)


class Broken:
    def __init__(self, **_): pass
    def start(self): raise OSError('sink vanished while starting')
    def stop(self): raise OSError('sink stop failed')
    def close(self): raise OSError('sink closure failed')


def put(path, value):
    pending = path.with_suffix('.tmp')
    pending.write_text(json.dumps(value))
    pending.replace(path)


class Rig:
    def __init__(self, base):
        self.base = base
        self.inv = base / 'inventory.json'
        self.pumpfile = base / 'pump.json'
        self.events = base / 'events'
        self.config = base / 'config'
        self.configfile = self.config / 'gentle-stream/audio.json'
        self.timeout = 10
        self.token = 0
        flags = ["-S"] if sys.flags.no_site else []
        self.program = [sys.executable, *flags, '-m', 'gentle_stream', 'daemon', '--ready-stdout']
        self.outputs([(0, 'Monitor', 0), (3, 'Travel speaker', 0), (5, 'Desk speaker', 0)])

    def outputs(self, sinks, fail=(), default=0):
        put(self.inv, dict(sinks=sinks, fail=list(fail), default=default))

    def calls(self, name):
        if not self.events.exists():
            return []
        rows = [json.loads(line) for line in self.events.read_text().splitlines()]
        return [tuple(row[1:]) for row in rows if row[0] == name]

    def digest(self):
        return hashlib.sha256(self.configfile.read_bytes()).hexdigest()

    def pump(self, serial, audible):
        self.token += 1
        token = f'manual-{self.token}'
        put(self.pumpfile, [token, serial])
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            rows = [row for row in self.calls('pull') if row[1] == token]
            if rows:
                assert rows == [(serial, token, audible)]
                return
            time.sleep(.008)
        pytest.fail(f'no callback result for {token}')


@pytest.fixture
def rig(monkeypatch):
    with tempfile.TemporaryDirectory(prefix='v-') as directory:
        r = Rig(Path(directory))
        root = Path(__file__).resolve().parents[1]
        monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, (str(root / "src"), str(root), os.getenv("PYTHONPATH")))))
        for key, value in dict(XDG_CONFIG_HOME=r.config, XDG_CACHE_HOME=r.base / 'cache', XDG_RUNTIME_DIR=r.base,
                               INVENTORY=r.inv, EVENTS=r.events, PUMP=r.pumpfile,
                               GENTLE_STREAM_DEVICE_QUERY=f'{__name__}:query', GENTLE_STREAM_AUDIO_FACTORY=f'{__name__}:Sink').items():
            monkeypatch.setenv(key, str(value))
        yield r


@contextmanager
def running(rig):
    with subprocess.Popen(rig.program, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        p = None
        try:
            assert select.select([proc.stdout], [], [], rig.timeout)[0], 'readiness timed out'
            assert proc.stdout.readline() == b'{"ready": true}\n'
            p = client.connect()
            assert p.pid == proc.pid
            yield p
            p.request('stop')
            assert proc.wait(timeout=5) == 0
            assert (proc.stdout.read(), proc.stderr.read()) == (b'', b'')
        finally:
            if p:
                p.detach()
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)


# The endpoint identity probes inspect the explicit index sent to the fake sink,
# not just the logical name echoed by the daemon.
@pytest.mark.parametrize('old_api,rows,wanted', [
    pytest.param(14,[(3,'Office speaker',5),(8,'Travel speaker',14),(11,'TRAVEL SPEAKER',21)],8,id='moved-known-with-other-api-homonym'),
    pytest.param(14,[(3,'Travel speaker',21),(8,'Travel speaker',14)],8,id='moved-known-with-recycled-homonym'),
    pytest.param(14,[(3,'Office speaker',5),(8,'Travel speaker',21)],None,id='only-different-api'),
    pytest.param(14,[(3,'Office speaker',5),(8,'Travel speaker',None)],None,id='moved-row-missing-api'),
    pytest.param(None,[(3,'Office speaker',5),(8,'Travel speaker',14)],None,id='original-row-missing-api'),
    pytest.param(None,[(3,'Travel speaker',21),(8,'Travel speaker',14)],None,id='unknown-old-api-with-homonyms'),
    pytest.param(14,[(3,'Office speaker',5),(8,'Travel speaker',14),(11,'Travel speaker',14)],None,id='two-moved-same-api-homonyms'),
])
def test_rollback_identity_evidence(rig, old_api, rows, wanted):
    fixed = [(0,'Monitor',0),(5,'Busy HDMI',21)]
    rig.outputs(fixed + [(3,'Travel speaker',old_api)])
    with running(rig) as p:
        p.set_device(3)
        saved = rig.digest()
        state = p.request('state')
        rig.outputs(fixed + rows, fail=[5])
        with pytest.raises(client.PlayerError) as caught:
            p.set_device(5)
        opened = [row[2] for row in rig.calls('open')]
        after = p.request('state')
        expected_error = 'Cannot switch audio output: refused sink 5'
        if wanted is None:
            expected_error += '; previous output could not reopen: “Travel speaker” is unavailable or ambiguous'
        expected = dict(opened=[0,3,5]+([wanted] if wanted is not None else []),
                        picked='Travel speaker' if wanted is not None else None,
                        playing=wanted is not None, error=expected_error)
        observed = dict(opened=opened, picked=after['device'], playing=after['playing'], error=str(caught.value))
        print(json.dumps({'expected':expected,'observed':observed}, ensure_ascii=False))
        assert rig.digest() == saved and after['pid'] == state['pid']
        assert observed == expected


class RepeatedClose(Sink):
    def start(self):
        raise OSError('start fault')

    def close(self):
        raise OSError('close fault')


class DistinctShutdownFailures(RepeatedClose):
    def stop(self):
        raise OSError('stop fault')


class SameTextDifferentOperation(RepeatedClose):
    closes = 0

    def stop(self):
        raise OSError('close fault')  # Same host error string, but from a different operation.

    def close(self):
        self.closes += 1
        if self.closes == 1:
            super().close()


@pytest.mark.parametrize('factory,required', [
    (RepeatedClose, ('start fault','output cleanup failed: close fault')),
    (DistinctShutdownFailures, ('start fault','output cleanup failed: close fault','shutdown cleanup failed: stop fault')),
    (SameTextDifferentOperation, ('start fault','output cleanup failed: close fault','shutdown cleanup failed: close fault')),
])
def test_readiness_preserves_distinct_failures(rig, monkeypatch, factory, required):
    with monkeypatch.context() as m:
        m.setenv('GENTLE_STREAM_AUDIO_FACTORY', f'{__name__}:{factory.__name__}')
        proc = subprocess.run(rig.program, capture_output=True, timeout=12)
    expected = (1,(json.dumps({'error': '; '.join(required),'audio':True})+'\n').encode(),b'')
    observed = (proc.returncode, proc.stdout, proc.stderr)
    print(f'expected={expected!r}; observed={observed!r}')
    paths = daemon.Paths(daemon.runtime_dir())
    assert not paths.sock.exists() and not paths.key.exists()
    with running(rig) as p:
        assert (p.device, p.playing) == (None, True)
    assert observed == expected


def test_panic_does_not_route_a_recycled_index(rig):
    old = [(0,'Monitor',0),(3,'Travel speaker',14)]
    rig.outputs(old)
    with running(rig) as p:
        p.set_device(3)
        saved = rig.digest()
        rig.outputs([(0,'Monitor',0),(3,'Travel speaker',21),(8,'Travel speaker',14)])
        try:
            p.panic()
        except client.PlayerError:
            pass  # A safe failure is acceptable; opening an unrelated homonym is not.
        selected = rig.calls('open')[2:]
        print(f'expected no reopen at recycled 3; observed open calls={selected!r}')
        assert rig.digest() == saved
        assert all(row[2] != 3 for row in selected)
