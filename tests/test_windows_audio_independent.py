"""Independent regression probes for native transport cancellation and failure reporting."""
import contextlib
import io
import os
import select
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gentle_stream import _windows_output_host as host, daemon
from gentle_stream.player import Player
from gentle_stream.style import Style
from gentle_stream.windows_audio import WindowsPCMStream


class IndependentT6(unittest.TestCase):
    def test_stop_cancels_full_pipe_without_native_process(self):
        read, write = os.pipe()
        sink = os.fdopen(write, 'wb', buffering=0)
        ack_read, ack_write = os.pipe()
        source = os.fdopen(ack_read, 'rb', buffering=0)
        proc = SimpleNamespace(stdin=sink, stdout=source, poll=lambda: 0 if sink.closed else None,
                               wait=lambda timeout=None: 0)
        try:
            with patch('gentle_stream.windows_audio._executables', return_value=('fake.exe', 'fake.exe')), \
                 patch('gentle_stream.windows_audio.subprocess.Popen', return_value=proc):
                stream = WindowsPCMStream(samplerate=44100, channels=2, dtype='float32',
                                          blocksize=65536, callback=lambda out, *args: out.fill(.25))
                stream.start()
                deadline = time.monotonic() + 2
                while not select.select([read], [], [], .01)[0]:
                    if time.monotonic() >= deadline:
                        break
                self.assertTrue(select.select([read], [], [], 0)[0])
                started = time.monotonic()
                stream.stop()
                self.assertLess(time.monotonic() - started, 1.)
                self.assertTrue(sink.closed)
                self.assertIsNone(stream.error)
        finally:
            sink.close()
            source.close()
            os.close(ack_write)
            os.close(read)

    def test_invalid_supervisor_rate_never_spawns(self):
        for argv in ([], ['fake.exe', '0'], ['fake.exe', '99'], ['fake.exe', '192001'],
                     ['fake.exe', '44100.0']):
            with self.subTest(argv=argv), patch.object(host.subprocess, 'Popen') as spawn:
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = host.main(argv)
                self.assertEqual((code, out.getvalue(), err.getvalue()),
                                 (1, '', 'Windows audio: expected FFplay path and sample rate\n'))
                spawn.assert_not_called()

    def test_failed_output_is_visible_with_unknown_underflows(self):
        engine = SimpleNamespace(sr=44100, style=Style(), piece=1, backend='fake')
        player = Player(engine)
        # A failed device is injected; the assertion uses the public daemon state response.
        player._stream = SimpleNamespace(audio_output='windows', error='native device failed')
        player.output_underflows = None
        snapshot = daemon.state(player)
        self.assertEqual(snapshot['audio_output_error'], 'native device failed')
        self.assertFalse(snapshot['playing'])
        self.assertIsNone(snapshot['output_underflows'])


if __name__ == '__main__':
    unittest.main()
