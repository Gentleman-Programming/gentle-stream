"""Device-free diagnostics tests; no daemon process, runtime files or model calls."""
import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from gentle_stream import main
from gentle_stream import daemon
from gentle_stream.client import NoPlayer, PlayerClient
from gentle_stream.player import Player
from gentle_stream.style import Style


class FakeEngine:
    sr = 44100
    piece = 1
    bar = 0
    backend = "fake"

    def __init__(self):
        self.style = Style()

    def queue_next(self):
        self.piece += 1

    def render_bar(self):
        self.bar += 1
        return np.full((4096, 2), 0.25, dtype=np.float32)


class FakeStream:
    def __init__(self, callback, **kwargs):
        self.callback = callback

    def pull(self, frames, status):
        out = np.empty((frames, 2), dtype=np.float32)
        self.callback(out, frames, None, status)
        return out


class Connection:
    def __init__(self, state):
        self.state = state
        self.closed = False
        self.requests = []

    def send_bytes(self, data):
        self.requests.append(json.loads(data))

    def recv_bytes(self):
        return json.dumps({"ok": True, "state": self.state}).encode()

    def close(self):
        self.closed = True


def base_state():
    style = Style().to_dict()
    return {"pid": 123, "playing": True, "volume": 0.8, "level": 0.1,
            "backend": "fake", "now": {"style": style, "piece": 1, "bar": 2}, "style": style}


class AudioDiagnosticsTests(unittest.TestCase):
    def player(self):
        player = Player(FakeEngine())
        self.addCleanup(player.close)
        # Public next() queues deterministic audio without a producer/device.
        player.next()
        return player, FakeStream(callback=player._callback)

    def test_host_underflow_with_full_queue_is_separate_and_audio_identical(self):
        flagged, stream = self.player()
        normal, reference = self.player()
        self.assertEqual(flagged.output_underflows, 0)
        self.assertEqual(flagged.buffered_seconds, 4096 / 44100)
        out = stream.pull(128, SimpleNamespace(output_underflow=True))
        np.testing.assert_array_equal(out, reference.pull(128, None))
        self.assertEqual(flagged.output_underflows, 1)
        self.assertEqual(flagged.underruns, 0)
        self.assertEqual(flagged.position, normal.position)
        self.assertEqual(flagged.level, normal.level)
        self.assertEqual(flagged.buffered_seconds, (4096 - 128) / 44100)
        np.testing.assert_array_equal(flagged.spectrum_levels(), normal.spectrum_levels())

    def test_app_starvation_is_independent_and_counts_each_short_callback(self):
        player, stream = self.player()
        stream.pull(4097, None)
        self.assertEqual(player.underruns, 1)
        self.assertEqual(player.output_underflows, 0)
        self.assertEqual(player.buffered_seconds, 0)
        stream.pull(128, SimpleNamespace(output_underflow=True))
        self.assertEqual((player.underruns, player.output_underflows), (2, 1))

    def test_absent_and_false_flags_do_not_count(self):
        player, stream = self.player()
        for status in (None, False, SimpleNamespace(output_underflow=False)):
            stream.pull(64, status)
        self.assertEqual((player.underruns, player.output_underflows), (0, 0))

    def test_paused_callback_counts_host_only_without_consuming_audio(self):
        player, stream = self.player()
        player.pause()
        before = player.buffered_seconds
        np.testing.assert_array_equal(stream.pull(128, SimpleNamespace(output_underflow=True)),
                                      np.zeros((128, 2), dtype=np.float32))
        self.assertEqual((player.underruns, player.output_underflows), (0, 1))
        self.assertEqual(player.buffered_seconds, before)
        self.assertEqual(player.position, 0)
        player.play()
        self.assertTrue(np.any(stream.pull(128, None)))
        self.assertEqual(player.output_underflows, 1)

    def test_daemon_state_and_client_controls_preserve_actual_values(self):
        player, stream = self.player()
        stream.pull(4097, SimpleNamespace(output_underflow=True))
        player.next()
        reply = daemon.handle(player, {"cmd": "state"})
        self.assertTrue(reply["ok"])
        state = reply["state"]
        for key, value in {"underruns": 1, "output_underflows": 1,
                           "buffered_seconds": 4096 / 44100}.items():
            self.assertEqual(state[key], value)
        conn = Connection(state)
        client = PlayerClient(conn)
        self.addCleanup(client.detach)
        self.assertEqual((client.underruns, client.output_underflows, client.buffered_seconds),
                         (1, 1, 4096 / 44100))
        state.update(underruns=3, output_underflows=5, buffered_seconds=0.25, playing=False)
        client.pause()
        self.assertFalse(client.playing)
        self.assertEqual((client.underruns, client.output_underflows, client.buffered_seconds), (3, 5, 0.25))
        self.assertEqual(conn.requests, [{"cmd": "state"}, {"cmd": "pause"}])

    def test_old_daemon_fields_are_unknown_in_client_and_cli(self):
        client = PlayerClient(Connection(base_state()))
        self.addCleanup(client.detach)
        self.assertEqual((client.underruns, client.output_underflows, client.buffered_seconds), (None, None, None))
        self.check_status(client, None, None, None)

    def check_status(self, client, underruns, output_underflows, buffered_seconds):
        out, err = io.StringIO(), io.StringIO()
        with patch("gentle_stream.client.connect", return_value=client), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(main(["ctl", "status"]), 0)
        style = client.now.style
        expected = {"running": True, "playing": True, "name": style.name, "genre": style.genre,
                    "mood": style.mood, "underruns": underruns, "output_underflows": output_underflows,
                    "buffered_seconds": buffered_seconds}
        self.assertEqual(out.getvalue(), json.dumps(expected, ensure_ascii=False) + "\n")
        self.assertEqual(err.getvalue(), "")
        self.assertTrue(client.gone)

    def test_public_cli_status_adds_metrics_without_changing_existing_values(self):
        state = base_state()
        state.update(underruns=7, output_underflows=11, buffered_seconds=0.375)
        self.check_status(PlayerClient(Connection(state)), 7, 11, 0.375)

    def test_public_cli_not_running_is_unchanged(self):
        out, err = io.StringIO(), io.StringIO()
        with patch("gentle_stream.client.connect", side_effect=NoPlayer("nothing is playing")), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(main(["ctl", "status"]), 0)
        self.assertEqual(out.getvalue(), '{"running": false}\n')
        self.assertEqual(err.getvalue(), "")


def test_pause_ack_vs_windows_queued_pcm_diagnostic(monkeypatch):
    """Legacy raw-output model, not the new Windows cut/ACK or speaker latency.

    The owned transport cut is asserted through Player/real supervisor pipes in
    test_pause_output.py. Keep this counterexample: callback silence alone never
    proves that already-submitted PCM was flushed.
    """
    player = Player(FakeEngine())
    player.next()
    submitted = []
    acknowledged = []
    for index in range(16):
        if index == 8:
            response = daemon.handle(player, {"cmd": "pause"})
            acknowledged.append((response, player.position))
        audio = np.empty((256, 2), dtype=np.float32)
        player._callback(audio, 256, None, None)
        submitted.append(audio.reshape(-1).copy())
    response, position_at_ack = acknowledged[0]
    assert response["ok"] and response["state"]["playing"] is False
    assert position_at_ack == 2048
    assert len(submitted) == 16 and np.any(submitted[7])
    # Callback sends only a short existing de-click fade, then zeros. Previously
    # submitted PCM still sits downstream: a state acknowledgment cannot retract it.
    assert any(np.any(block) for block in submitted[8:13])
    assert all(not np.any(block) for block in submitted[13:])
    queued_nonzero_frames = np.count_nonzero(np.concatenate(submitted).reshape(-1, 2).any(axis=1))
    assert queued_nonzero_frames > position_at_ack
    held = (player.position, player.now)
    for _ in range(3):
        out = np.empty((256, 2), dtype=np.float32)
        player._callback(out, 256, None, None)
        assert not np.any(out)
    assert (player.position, player.now) == held
    print(f"legacy raw pause ACK after {position_at_ack} queued frames; simulated downstream nonzero frames="
          f"{queued_nonzero_frames}; already-sent PCM not flushed; acoustic latency unmeasured")
    player.close()


if __name__ == "__main__":
    unittest.main()
