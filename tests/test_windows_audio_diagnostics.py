"""Isolated WSL-to-Windows audio failure evidence; no native device is opened."""
import contextlib
import io
import os
import struct
import subprocess
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from gentle_stream import _windows_output_host as host, daemon
from gentle_stream.player import Player
from gentle_stream.style import Style
from gentle_stream.windows_audio import WindowsPCMStream


class SyntheticEngine:
    sr, piece, bar, backend = 1000, 1, 0, "synthetic"
    style = Style()

    def render_bar(self):
        self.bar += 1
        return np.full((4096, 2), .25, dtype=np.float32)


class WindowsAudioDiagnosticsTests(unittest.TestCase):
    def test_supervisor_exit_and_bounded_native_stderr_reach_public_state(self):
        real_popen = subprocess.Popen
        script = ("import sys; sys.stderr.write('x' * 5000 + "
                  "'FFplay: output unavailable\\n'); sys.stderr.flush(); sys.exit(23)")

        def exit_before_ack(*_args, **_kwargs):
            return real_popen([sys.executable, "-u", "-c", script],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, bufsize=0)

        with patch("gentle_stream.windows_audio._executables", return_value=("fake.exe", "fake.exe")), \
             patch("gentle_stream.windows_audio.subprocess.Popen", side_effect=exit_before_ack):
            player = Player(SyntheticEngine(), stream_factory=WindowsPCMStream, blocksize=64)
            try:
                player.start()
                deadline = time.monotonic() + 3
                while player._stream.error is None and time.monotonic() < deadline:
                    time.sleep(.01)
                state = daemon.state(player)
                error = state["audio_output_error"]
                self.assertEqual(state["audio_output"], "windows")
                self.assertFalse(state["playing"])
                self.assertIsNone(state["output_underflows"])
                self.assertIn("supervisor exit status 23", error)
                self.assertIn("FFplay: output unavailable", error)
                self.assertLess(len(error), 1100)
            finally:
                player.close()

    def test_native_host_reports_ffplay_exit_code_on_rejected_pcm(self):
        class ExitedSink:
            def __init__(self):
                self.stdin = io.BytesIO()
                self.returncode = 37

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

        output, errors = io.StringIO(), io.StringIO()
        framed = struct.pack("!cII", b"D", 1, 8) + bytes(8)
        with patch.object(host.subprocess, "Popen", return_value=ExitedSink()), \
             patch.object(host.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(framed))), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = host.main(["C:/synthetic-ffplay.exe", "44100"])
        self.assertEqual(code, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("FFplay exit status 37", errors.getvalue())


class DiagnosticCoverageTests(unittest.TestCase):
    def stream(self):
        with patch("gentle_stream.windows_audio._executables",
                   return_value=("fake.exe", "fake.exe")):
            return WindowsPCMStream(samplerate=1000, channels=2, dtype="float32",
                                    blocksize=64, callback=lambda *_: None)

    def test_resume_exception_preserves_native_diagnostic(self):
        stream = self.stream()
        stream._process = SimpleNamespace(poll=lambda: 23)
        stream._paused.set()
        stream._stderr_tail.extend(b"FFplay: output unavailable\n")
        with patch.object(stream, "_packet", return_value=1), \
             patch.object(stream, "_ack", side_effect=OSError("Windows native output write failed")):
            with self.assertRaises(ValueError) as caught:
                stream.resume()
        self.assertIn("supervisor exit status 23", str(caught.exception))
        self.assertIn("FFplay: output unavailable", str(caught.exception))
        self.assertEqual(stream.error, str(caught.exception).removeprefix('Windows output control failed: '))

    def test_final_native_stderr_replaces_stale_control_diagnostic_in_public_state(self):
        player = Player(SyntheticEngine())
        stream = self.stream()
        player._stream = stream  # Fake transport; no process or device is started.
        player.output_underflows = None
        stream._process = SimpleNamespace(poll=lambda: 23)
        stream._paused.set()
        stream._stderr_tail.extend(b"early supervisor error\n")
        with patch.object(stream, "_packet", return_value=1), \
             patch.object(stream, "_ack", side_effect=OSError("native control failed")):
            with self.assertRaisesRegex(ValueError, "early supervisor error"):
                player.play()
        stream._stderr_tail.extend(b"final FFplay device failure\n")
        # The worker's finalizer enriches the earlier control error after the native exit.
        stream.error = stream._diagnostic(stream.error)
        state = daemon.state(player)
        self.assertEqual(state["audio_output"], "windows")
        self.assertFalse(state["playing"])
        self.assertIsNone(state["output_underflows"])
        self.assertEqual(state["audio_output_error"],
                         "native control failed; supervisor exit status 23; "
                         "native stderr: early supervisor error final FFplay device failure")

    def test_writer_failure_includes_exited_ffplay_status(self):
        class Sink:
            def __init__(self):
                self.returncode = None
                self.stdin = SimpleNamespace(write=self.write, close=lambda: None)

            def write(self, data):
                self.returncode = 37
                raise BrokenPipeError("native output closed")

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

        output, errors = io.BytesIO(), io.StringIO()
        framed = struct.pack("!cII", b"D", 1, 8) + bytes(8)
        with patch.object(host.subprocess, "Popen", return_value=Sink()), \
             patch.object(host.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(framed))), \
             patch.object(host.sys, "stdout", SimpleNamespace(buffer=output)), \
             contextlib.redirect_stderr(errors):
            code = host.main(["C:/synthetic-ffplay.exe", "44100"])
        self.assertEqual(code, 1)
        self.assertEqual(output.getvalue(), struct.pack("!cI", b"E", 1))
        self.assertEqual(errors.getvalue(),
                         "Windows audio: native audio writer stalled or failed; "
                         "FFplay exit status 37\n")

    def test_close_stops_reader_when_descendant_retains_stderr(self):
        import threading

        stream = self.stream()
        read_fd, write_fd = os.pipe()
        pipe = os.fdopen(read_fd, "rb", buffering=0)
        stream._process = SimpleNamespace(stderr=pipe, poll=lambda: 0)
        stream._thread = SimpleNamespace(join=lambda timeout=None: None,
                                         is_alive=lambda: False)
        reader = threading.Thread(target=stream._collect_stderr, daemon=True)
        stream._stderr_thread = reader
        reader.start()
        try:
            self.assertTrue(reader.is_alive())  # The descendant still owns the write end.
            stream.close()
            self.assertFalse(reader.is_alive())
        finally:
            os.close(write_fd)
            reader.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
