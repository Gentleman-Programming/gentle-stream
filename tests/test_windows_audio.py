"""Optional Windows output: device-free PCM/lifecycle and public routing regressions."""
import contextlib
import io
import json
import os
import subprocess
import struct
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from gentle_stream import main, daemon
from gentle_stream.client import PlayerClient, PlayerError, ensure
from gentle_stream.player import Player
from gentle_stream.style import Style
from gentle_stream.windows_audio import WindowsPCMStream
from gentle_stream import _windows_output_host as host


class PipeProcess:
    """Local pipe stands in for the interop supervisor; no process or audio device."""
    def __init__(self, *args, ack_transform=lambda op, seq: struct.pack('!cI', op, seq), **kwargs):
        read, write = os.pipe()
        self.stdin = os.fdopen(write, 'wb', buffering=0)
        ack_read, ack_write = os.pipe()
        self.stdout = os.fdopen(ack_read, 'rb', buffering=0)
        self.data = bytearray()
        self.ready = threading.Event()
        self.returncode = None
        def consume():
            try:
                source = os.fdopen(read, 'rb', buffering=0)
                while raw := host._read(source, 9):
                    op, sequence, size = struct.unpack('!cII', raw)
                    payload = host._read(source, size) if size else b''
                    if op == b'D':
                        self.data.extend(payload)
                        if len(self.data) >= 1024:
                            self.ready.set()
                    ack = ack_transform(op, sequence)
                    if ack:
                        os.write(ack_write, ack)
            finally:
                os.close(ack_write)
                source.close()
                if self.returncode is None:
                    self.returncode = 0
        self.reader = threading.Thread(target=consume, daemon=True)
        self.reader.start()
    def poll(self):
        return self.returncode
    def wait(self, timeout=None):
        self.reader.join(timeout)
        if self.reader.is_alive():
            raise subprocess.TimeoutExpired('fake supervisor', timeout)
        return self.returncode


class BinaryOutput(io.StringIO):
    def __init__(self):
        super().__init__()
        self.buffer = io.BytesIO()


class Engine:
    sr, piece, bar, backend = 1000, 1, 0, 'fake'
    style = Style()
    def render_bar(self):
        self.bar += 1
        return np.full((4096, 2), .25, dtype=np.float32)


class WindowsAudioTests(unittest.TestCase):
    def test_opt_in_pcm_is_paced_and_stop_closes_only_owned_pipe(self):
        calls = []
        def callback(out, frames, timing, status):
            self.assertIsNone(status)
            out[:] = .25
            calls.append(frames)
        proc = PipeProcess()
        with patch('gentle_stream.windows_audio._executables', return_value=('python.exe', 'C:/ffplay.exe')), \
             patch('gentle_stream.windows_audio.subprocess.Popen', return_value=proc) as spawn:
            stream = WindowsPCMStream(samplerate=1000, channels=2, dtype='float32',
                                      blocksize=64, latency='high', callback=callback)
            started = time.monotonic()
            stream.start()
            self.assertTrue(proc.ready.wait(2))
            stream.stop()
            stream.close()
            self.assertLessEqual(len(calls), int((time.monotonic() - started) / .064) + 2)
        self.assertIsNone(stream.error)
        self.assertEqual(stream.audio_output, 'windows')
        self.assertFalse(stream.reports_output_underflows)
        self.assertFalse(proc.reader.is_alive())
        self.assertGreaterEqual(len(calls), 2)
        np.testing.assert_array_equal(np.frombuffer(proc.data, dtype='<f4'), .25)
        argv = spawn.call_args.args[0]
        self.assertEqual(argv[0], 'python.exe')
        self.assertEqual(argv[-2:], ['C:/ffplay.exe', '1000'])
        self.assertFalse(spawn.call_args.kwargs.get('shell', False))

    def test_malformed_acknowledgments_fail_closed_and_stop_owned_transport(self):
        cases = [(lambda op, seq: struct.pack('!cI', b'X', seq), 'Invalid'),
                 (lambda op, seq: struct.pack('!cI', b'R', seq), 'Mismatched'),
                 (lambda op, seq: struct.pack('!cI', op, seq + 1), 'Invalid'),
                 (lambda op, seq: b'', 'timed out')]
        for transform, message in cases:
            with self.subTest(message=message):
                proc = PipeProcess(ack_transform=transform)
                with patch('gentle_stream.windows_audio._executables', return_value=('fake', 'fake')), \
                     patch('gentle_stream.windows_audio.subprocess.Popen', return_value=proc):
                    stream = WindowsPCMStream(samplerate=1000, channels=2, dtype='float32',
                                              blocksize=64, callback=lambda out, *args: out.fill(.25))
                    started = time.monotonic()
                    stream.start()
                    stream._thread.join(timeout=5)
                    stream.close()
                    self.assertLess(time.monotonic() - started, 5)
                    self.assertIn(message, stream.error)
                    self.assertFalse(proc.reader.is_alive())
                    self.assertTrue(proc.stdin.closed)
                    self.assertTrue(proc.stdout.closed)

    def test_startup_pipe_failure_closes_owned_supervisor_input(self):
        proc = PipeProcess()
        try:
            with patch('gentle_stream.windows_audio._executables', return_value=('python.exe', 'C:/ffplay.exe')), \
                 patch('gentle_stream.windows_audio.subprocess.Popen', return_value=proc), \
                 patch('gentle_stream.windows_audio.os.set_blocking', side_effect=OSError('pipe failed')):
                stream = WindowsPCMStream(samplerate=1000, channels=2, dtype='float32',
                                          blocksize=64, callback=lambda *args: None)
                with self.assertRaisesRegex(OSError, 'pipe failed'):
                    stream.start()
                self.assertTrue(proc.stdin.closed)
                stream.close()
                self.assertFalse(proc.reader.is_alive())
        finally:
            proc.stdin.close()
            proc.wait(timeout=1)

    def test_callback_failure_closes_pipe_and_surfaces_output_error(self):
        proc = PipeProcess()
        entered = threading.Event()
        def fail(*args):
            entered.set()
            raise RuntimeError('callback failed')
        with patch('gentle_stream.windows_audio._executables', return_value=('python.exe', 'C:/ffplay.exe')), \
             patch('gentle_stream.windows_audio.subprocess.Popen', return_value=proc):
            stream = WindowsPCMStream(samplerate=1000, channels=2, dtype='float32',
                                      blocksize=64, callback=fail)
            stream.start()
            self.assertTrue(entered.wait(2))
            stream.stop()
            self.assertIn('callback failed', stream.error)
            self.assertTrue(proc.stdin.closed)
            self.assertFalse(proc.reader.is_alive())

    def test_native_failure_terminates_only_owned_windows_child(self):
        class BrokenSink:
            def write(self, data):
                raise BrokenPipeError('device failed')
            def close(self):
                pass
        native = SimpleNamespace(stdin=BrokenSink(), returncode=None, terminated=False)
        native.poll = lambda: native.returncode
        def terminate():
            native.terminated = True
            native.returncode = 1
        native.terminate = terminate
        native.wait = lambda timeout=None: native.returncode
        out, err = BinaryOutput(), io.StringIO()
        payload = struct.pack('!cII', b'D', 1, 1024) + bytes(1024)
        with patch.object(host.subprocess, 'Popen', return_value=native) as spawn, \
             patch.object(host.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(payload))), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(host.main(['C:/ffplay.exe', '44100']), 1)
        self.assertTrue(native.terminated)
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(out.getvalue(), '')
        self.assertIn('Windows audio:', err.getvalue())

    def test_default_factory_and_explicit_windows_selection(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(daemon._stream_factory())
        with patch.dict(os.environ, {'GENTLE_STREAM_AUDIO_OUTPUT': 'windows'}, clear=True):
            self.assertIs(daemon._stream_factory(), WindowsPCMStream)
        with patch.dict(os.environ, {'GENTLE_STREAM_AUDIO_OUTPUT': 'typo'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'GENTLE_STREAM_AUDIO_OUTPUT'):
                daemon._stream_factory()

    def test_native_controls_keep_silent_pause_and_unknown_host_counter(self):
        proc = PipeProcess()
        with patch('gentle_stream.windows_audio._executables', return_value=('python.exe', 'C:/ffplay.exe')), \
             patch('gentle_stream.windows_audio.subprocess.Popen', return_value=proc):
            player = Player(Engine(), stream_factory=WindowsPCMStream, blocksize=64)
            try:
                player.pause()
                player.start()
                self.assertTrue(player._stream._paused.is_set())
                self.assertEqual(player.position, 0)
                self.assertIsNone(player.output_underflows)
                np.testing.assert_array_equal(np.frombuffer(proc.data, dtype='<f4'), 0)
                snapshot = daemon.state(player)
                self.assertEqual(snapshot['audio_output'], 'windows')
                self.assertIsNone(snapshot['audio_output_error'])
                self.assertIsNone(snapshot['output_underflows'])
                player.play()
            finally:
                player.close()

    def test_existing_player_is_not_silently_replaced_or_mislabelled(self):
        existing = SimpleNamespace(audio_output='sounddevice', detach=lambda: None)
        with patch.dict(os.environ, {'GENTLE_STREAM_AUDIO_OUTPUT': 'windows'}), \
             patch('gentle_stream.client.connect', return_value=existing), \
             patch('gentle_stream.client._spawn') as spawn:
            with self.assertRaisesRegex(PlayerError, 'ctl stop'):
                ensure()
            spawn.assert_not_called()

    def test_default_status_remains_compatible_and_windows_is_identified(self):
        style = Style()
        player = SimpleNamespace(now=SimpleNamespace(style=style), playing=True, underruns=0,
                                 output_underflows=0, buffered_seconds=1., detach=lambda: None)
        for mode in ('sounddevice', 'windows'):
            player.audio_output = mode
            player.audio_output_error = None
            if mode == 'windows':
                player.output_underflows = None
            out, err = io.StringIO(), io.StringIO()
            with patch('gentle_stream.client.connect', return_value=player), \
                 contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(main(['ctl', 'status']), 0)
            data = json.loads(out.getvalue())
            self.assertEqual(err.getvalue(), '')
            if mode == 'windows':
                self.assertEqual(data['audio_output'], mode)
                self.assertIsNone(data['output_underflows'])
                self.assertIsNone(data['audio_output_error'])
            else:
                self.assertNotIn('audio_output', data)
                self.assertEqual(data['output_underflows'], 0)

    def test_native_supervisor_copies_bytes_without_speech_or_extra_processes(self):
        class Collector(io.BytesIO):
            def write(self, data):
                return super().write(data[:7])  # Real partial sink writes must preserve every byte.
            def close(self):
                self.closed_bytes = self.getvalue()
        sink = Collector()
        native = SimpleNamespace(stdin=sink, returncode=None)
        native.poll = lambda: native.returncode
        def wait(timeout=None):
            native.returncode = 0
            return 0
        native.wait = wait
        payload = np.full((128, 2), .25, dtype='<f4').tobytes()
        out, err = BinaryOutput(), io.StringIO()
        framed = struct.pack('!cII', b'D', 1, len(payload)) + payload
        with patch.object(host.subprocess, 'Popen', return_value=native) as spawn, \
             patch.object(host.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(framed))), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(host.main(['C:/ffplay with spaces.exe', '44100']), 0)
        self.assertEqual(sink.closed_bytes, payload)
        self.assertEqual(out.buffer.getvalue(), struct.pack('!cI', b'D', 1))
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(err.getvalue(), '')
        self.assertEqual(spawn.call_count, 1)
        argv = spawn.call_args.args[0]
        self.assertIn('f32le', argv)
        self.assertIn('stereo', argv)
        self.assertIn('pipe:0', argv)
        self.assertFalse(spawn.call_args.kwargs.get('shell', False))


if __name__ == '__main__':
    unittest.main()
