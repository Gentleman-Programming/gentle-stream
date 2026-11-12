"""The background player: a `gentle-stream daemon` process that owns the audio, controlled over a private socket.

Every test runs its daemons in a short private runtime dir under /tmp (AF_UNIX paths are limited to ~104 bytes) with
a fake audio stream, and stops or kills them afterwards. The user's real runtime dir is never touched.
"""
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from gentle_stream import main
from gentle_stream.style import Style

REPO = str(Path(__file__).resolve().parents[1])


class FakeAudio:
    """sounddevice.OutputStream stand-in for the daemon process: a thread pulls the callback 4x faster than real
    time, so "now playing" advances like on a real device, only quicker."""

    SPEED = 4

    def __init__(self, callback, samplerate, blocksize, **kw):
        self.callback, self.sr, self.blocksize = callback, samplerate, blocksize
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        out = np.zeros((self.blocksize, 2), dtype=np.float32)
        while not self._stop.wait(self.blocksize / self.sr / self.SPEED):
            self.callback(out, self.blocksize, None, None)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    abort = stop  # Fake output has no downstream queue to drain.

    def close(self):
        if self._thread.is_alive():
            self._thread.join(timeout=2)


def no_device(**kw):
    # Exercise startup-error transport without importing/probing a native audio host.
    raise OSError("Error querying device -1")


def wait_for(cond, timeout=10.0):
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > timeout:
            raise AssertionError("condition not reached")
        time.sleep(0.02)


def alive(pid):
    from gentle_stream.processes import is_alive
    return is_alive(pid)


def daemon_pids(run):
    lock = run / "player.lock"
    text = lock.read_text().strip() if lock.exists() else ""
    return [int(text)] if text.isdigit() else []


@pytest.fixture
def runtime(monkeypatch):
    """A private runtime dir for the daemons of one test; any daemon left behind is stopped, then killed."""
    root = Path(tempfile.mkdtemp(prefix="gs-", dir=os.environ.get("GENTLE_STREAM_TEST_TEMP", "/tmp")))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(root))
    monkeypatch.setenv("XDG_CACHE_HOME", str(root / "cache"))     # no soundfont: fast procedural instruments
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(Path(REPO) / "src"), REPO)))                          # the daemon imports FakeAudio from here
    monkeypatch.setenv("GENTLE_STREAM_AUDIO_FACTORY", "tests.test_daemon:FakeAudio")
    run = root / "gentle-stream"
    from gentle_stream import client
    children = []
    original = subprocess.Popen
    def spawn(argv, **kwargs):
        proc = original(argv, **kwargs)
        if isinstance(argv, list) and argv[1:4] == ["-m", "gentle_stream", "daemon"]:
            children.append(proc)
        return proc
    monkeypatch.setattr(subprocess, "Popen", spawn)
    try:
        yield run
    finally:
        from gentle_stream import client

        try:
            client.connect().close()
        except (client.NoPlayer, client.PlayerError):
            pass
        finally:
            for proc in children:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        proc.terminate()
                    except ProcessLookupError:
                        pass  # The owned child may exit between wait and terminate.
                    proc.wait(timeout=5)
            # Retain owned runtime/log evidence; cleanup belongs to the orchestrator.


@pytest.mark.parametrize("terminate_error", [None, ProcessLookupError, PermissionError, "exited"])
def test_runtime_teardown_handles_only_already_exited_processes(monkeypatch, terminate_error):
    from gentle_stream import client

    root = Path("fixture-only-runtime")
    events = []

    class Player:
        def close(self):
            events.append("close")

    class Child:
        waits = 0

        def wait(self, *, timeout):
            assert timeout == 5
            self.waits += 1
            events.append("wait")
            if self.waits == 1 and terminate_error != "exited":
                raise subprocess.TimeoutExpired("owned fake daemon", timeout)
            return 0

        def terminate(self):
            events.append("terminate")
            if terminate_error is not None:
                raise terminate_error("synthetic teardown error")

    child, bystander = Child(), Child()
    def spawn(argv, **kwargs):
        events.append(tuple(argv))
        return child if argv[1:4] == ["-m", "gentle_stream", "daemon"] else bystander

    monkeypatch.setattr(tempfile, "mkdtemp", lambda **kwargs: str(root))
    monkeypatch.setattr(client, "connect", lambda: Player())
    monkeypatch.setattr(subprocess, "Popen", spawn)
    # Fail closed if the fixture ever regresses to PID signaling or deletion.
    monkeypatch.setattr(os, "kill", lambda *args: pytest.fail("unexpected PID signal"))
    import shutil
    monkeypatch.setattr(shutil, "rmtree", lambda *args, **kwargs: pytest.fail("unexpected deletion"))
    fixture = runtime.__wrapped__(monkeypatch)
    assert next(fixture) == root / "gentle-stream"
    owned = [sys.executable, "-m", "gentle_stream", "daemon"]
    unrelated = [sys.executable, "-m", "unrelated"]
    assert subprocess.Popen(owned) is child
    assert subprocess.Popen(unrelated) is bystander
    with pytest.raises(PermissionError if terminate_error is PermissionError else StopIteration):
        next(fixture)
    tail = ["close", "wait"]
    if terminate_error != "exited":
        tail.append("terminate")
        if terminate_error is not PermissionError:
            tail.append("wait")
    assert events == [tuple(owned), tuple(unrelated), *tail]
    assert bystander.waits == 0


def run_ctl(capsys, action):
    code = main(["ctl", action])
    out, err = capsys.readouterr()
    return code, out, err


# ------------------------------------------------------------ lifecycle
def test_the_first_client_starts_the_player_and_the_next_one_attaches(runtime):
    from gentle_stream import client

    first = client.ensure()
    pid = first.pid
    assert alive(pid) and pid != os.getpid()
    second = client.ensure()                       # a second TUI attaches to the same music
    assert second.pid == pid
    second.set_volume(0.3)
    first.spectrum(16)                             # polling refreshes the cached state
    assert first.volume == pytest.approx(0.3)
    second.detach()
    first.detach()
    assert client.connect().pid == pid             # detaching leaves it running


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits and Unix socket; native ACL covered separately")
def test_the_runtime_dir_and_the_authkey_are_private(runtime):
    from gentle_stream import client

    client.ensure().detach()
    assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
    assert stat.S_IMODE((runtime / "authkey").stat().st_mode) == 0o600
    assert stat.S_ISSOCK((runtime / "player.sock").stat().st_mode)


def test_a_wrong_authkey_is_refused(runtime):
    from multiprocessing.connection import AuthenticationError, Client

    from gentle_stream import client

    client.ensure().detach()
    with pytest.raises((AuthenticationError, EOFError, OSError)):
        from gentle_stream import daemon, platform_runtime
        Client(platform_runtime.address(daemon.Paths(runtime)), family=platform_runtime.FAMILY, authkey=b"not the key")
    assert client.connect().playing                # the player survives the attempt


def test_only_one_player_runs_per_user(runtime):
    from gentle_stream import client

    pid = client.ensure().pid
    second = subprocess.run([sys.executable, "-m", "gentle_stream", "daemon"], timeout=30,
                            capture_output=True, text=True)
    assert second.returncode == 0
    assert client.connect().pid == pid             # the running player kept its socket


@pytest.mark.skipif(os.name == "nt", reason="SIGKILL leaves a POSIX socket inode")
def test_a_crashed_player_is_detected_and_replaced(runtime, capsys):
    from gentle_stream import client

    pid = client.ensure().pid
    os.kill(pid, signal.SIGKILL)                   # leaves the socket file behind
    wait_for(lambda: not alive(pid))
    assert (runtime / "player.sock").exists()
    assert run_ctl(capsys, "status") == (0, '{"running": false}\n', "")
    fresh = client.ensure()
    assert fresh.pid != pid and fresh.playing


@pytest.mark.skipif(os.name == "nt", reason="AF_UNIX inode recovery; native lock recovery covered separately")
def test_a_stale_socket_file_is_replaced(runtime):
    from gentle_stream import client

    runtime.mkdir(mode=0o700, parents=True)
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(runtime / "player.sock"))           # bound and closed: nobody listens
    s.close()
    with pytest.raises(client.NoPlayer):
        client.connect()
    assert client.ensure().playing


def test_stop_ends_the_player_process(runtime):
    from gentle_stream import client

    player = client.ensure()
    pid = player.pid
    player.close()                                 # what `q` does
    wait_for(lambda: not alive(pid))
    assert not (runtime / "player.sock").exists()
    with pytest.raises(client.NoPlayer):
        client.connect()


@pytest.mark.skipif(os.name == "nt", reason="Windows SIGTERM is TerminateProcess, not a catchable signal")
def test_sigterm_ends_the_player_cleanly(runtime):
    from gentle_stream import client

    pid = client.ensure().pid
    os.kill(pid, signal.SIGTERM)
    wait_for(lambda: not alive(pid))
    assert not (runtime / "player.sock").exists()


def test_an_audio_device_error_is_reported_to_the_client_and_no_player_stays(runtime, monkeypatch):
    from gentle_stream import client

    monkeypatch.setenv("GENTLE_STREAM_AUDIO_FACTORY", "tests.test_daemon:no_device")
    with pytest.raises(client.AudioError) as error:
        client.ensure()
    assert str(error.value) == "Error querying device -1"
    assert not (runtime / "player.sock").exists()
    with pytest.raises(client.NoPlayer):
        client.connect()


# ------------------------------------------------------------ controls and state
def test_controls_reach_the_player(runtime):
    from gentle_stream import client

    player = client.ensure()
    player.toggle()
    assert not player.playing
    player.play()
    assert player.playing
    player.pause()
    assert not player.playing
    player.set_volume(0.42)
    assert player.volume == pytest.approx(0.42)
    player.play()                                  # paused, nothing is pulled and "now" stands still
    piece = player.now.piece
    player.next()
    wait_for(lambda: player.spectrum(8) is not None and player.now.piece > piece)


def test_polling_returns_the_audible_style_and_the_spectrum(runtime):
    from gentle_stream import client

    player = client.ensure()
    wanted = Style(name="Second Wind", mood="rain on a tin roof", genre="jazz", bpm=110)
    player.set_style(wanted)
    wait_for(lambda: player.spectrum(8) is not None and player.style == wanted)     # rendered first
    wait_for(lambda: player.spectrum(8) is not None and player.now.style == wanted, timeout=20)
    bands = player.spectrum(24)
    assert bands.shape == (24,) and np.all((bands >= 0) & (bands <= 1)) and bands.max() > 0
    assert player.level > 0 and player.backend == "procedural"


def test_a_state_round_trip_is_fast(runtime):
    from gentle_stream import client

    player = client.ensure()
    player.spectrum(64)
    t0 = time.perf_counter()
    for _ in range(50):
        player.spectrum(64)
    assert (time.perf_counter() - t0) / 50 < 0.02   # well under one 15 fps frame


def test_a_stopped_player_makes_polling_fail_and_controls_harmless(runtime):
    from gentle_stream import client

    watcher = client.ensure()
    client.connect().close()                        # stopped from somewhere else
    watcher.toggle()                                # no exception from a key press
    with pytest.raises(ConnectionError):
        watcher.spectrum(8)
    watcher.close()                                 # quitting afterwards is harmless too


def test_playlist_snapshot_survives_detach_and_reconnect(runtime):
    from gentle_stream import client

    first = client.ensure()
    first.pause()
    songs = (Style(name="First saved", bpm=140), Style(name="Selected saved", bpm=140))
    first.set_playlist(songs, start_index=1, name="Evening", continuous=True)
    assert not first.playing and first.continuous and first.playlist_index == 1
    pid = first.pid
    first.detach()
    attached = client.connect()
    assert attached.pid == pid and attached.continuous and attached.playlist_name == "Evening"
    assert not attached.playing
    attached.next()
    attached.play()
    wait_for(lambda: attached.spectrum(8) is not None and attached.now.playlist_index == 0)
    assert attached.now.style == songs[0] and attached.now.playlist_name == "Evening"
    attached.set_continuous(False)
    assert not attached.continuous and attached.playing
    attached.set_style(Style(name="Normal mood"))
    assert attached.playlist_name is None and not attached.continuous
    attached.detach()


def test_panic_reopens_without_replacing_daemon_or_losing_current_playlist(runtime):
    from gentle_stream import client

    player = client.ensure()
    songs = (Style(name="First", bpm=140), Style(name="Current", bpm=140))
    player.set_playlist(songs, name="Retained", continuous=True)
    player.next()
    wait_for(lambda: player.spectrum(8) is not None and player.now.style == songs[1])
    player.pause()
    player.set_volume(.23)
    pid = player.pid
    player.panic()
    assert player.pid == pid and alive(pid) and not player.playing
    assert player.now.style == songs[1] and player.now.bar == 0
    assert player.playlist_name == "Retained" and player.playlist_index == 1
    assert player.continuous and player.volume == .23
    player.next()
    player.play()
    wait_for(lambda: player.spectrum(8) is not None and player.now.style == songs[0])


# ------------------------------------------------------------ ctl
@pytest.mark.parametrize("action", ["play", "pause", "toggle", "next", "stop"])
def test_ctl_without_a_player_says_nothing_is_playing(runtime, capsys, action):
    assert run_ctl(capsys, action) == (1, "", "gentle-stream: nothing is playing\n")


def test_ctl_status_without_a_player(runtime, capsys):
    assert run_ctl(capsys, "status") == (0, '{"running": false}\n', "")
    assert not runtime.exists() or not (runtime / "player.sock").exists()   # status never starts a player


def test_ctl_controls_a_running_player(runtime, capsys):
    from gentle_stream import client

    player = client.ensure()
    player.set_style(Style(name="Café Night", mood="late café", genre="jazz"))
    wait_for(lambda: player.spectrum(8) is not None and player.now.style.name == "Café Night", timeout=20)
    code, out, err = run_ctl(capsys, "status")
    assert (code, err) == (0, "") and out.endswith("\n") and out.count("\n") == 1
    status = json.loads(out)
    assert type(status["underruns"]) is int and status["underruns"] >= 0
    assert type(status["output_underflows"]) is int and status["output_underflows"] >= 0
    assert type(status["buffered_seconds"]) is float and status["buffered_seconds"] >= 0
    assert status == {"running": True, "playing": True, "name": "Café Night", "genre": "jazz",
                      "mood": "late café", "underruns": status["underruns"],
                      "output_underflows": status["output_underflows"],
                      "buffered_seconds": status["buffered_seconds"]}
    assert list(status) == ["running", "playing", "name", "genre", "mood", "underruns",
                            "output_underflows", "buffered_seconds"]
    assert run_ctl(capsys, "pause") == (0, "", "")
    assert json.loads(run_ctl(capsys, "status")[1])["playing"] is False
    assert run_ctl(capsys, "toggle") == (0, "", "")
    assert json.loads(run_ctl(capsys, "status")[1])["playing"] is True
    assert run_ctl(capsys, "next") == (0, "", "")
    assert run_ctl(capsys, "play") == (0, "", "")
    pid = player.pid
    assert run_ctl(capsys, "stop") == (0, "", "")
    assert not alive(pid) or wait_for(lambda: not alive(pid)) is None
    assert run_ctl(capsys, "status") == (0, '{"running": false}\n', "")
