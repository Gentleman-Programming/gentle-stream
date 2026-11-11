"""Device-free native contracts; also exercise the shared transport on POSIX."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tests.test_daemon import runtime, run_ctl, wait_for


@pytest.mark.skipif(os.name != "nt", reason="Windows private file creation boundary")
def test_windows_private_file_creation_contract(runtime, capsys):
    import ctypes as C
    from ctypes import wintypes as W
    from gentle_stream import _windows_runtime as win, daemon, platform_runtime

    paths = daemon.prepare()
    target = runtime / "synthetic-private-file"
    fd = win.create_private_file(target)
    try:
        assert not os.get_inheritable(fd)
        os.write(fd, b"synthetic-only")
    finally:
        os.close(fd)
    # Public validation checks the actual owner and every ACE, not POSIX mode bits.
    platform_runtime.check_file(target)
    owner, dacl, descriptor = W.LPVOID(), W.LPVOID(), W.LPVOID()
    assert win.GetSecurity(str(target), 1, 5, C.byref(owner), None,
                           C.byref(dacl), None, C.byref(descriptor)) == 0
    try:
        control, revision = W.WORD(), W.DWORD()
        win.checked(win.GetControl(descriptor, C.byref(control), C.byref(revision)))
        assert win.sid_string(owner) == win.user_sid()
        assert control.value & 0x1000  # SE_DACL_PROTECTED at creation
        assert dacl and C.cast(dacl, C.POINTER(win.ACL)).contents.count == 1
        ace = W.LPVOID()
        win.checked(win.GetAce(dacl, 0, C.byref(ace)))
        assert C.cast(ace, C.POINTER(W.BYTE))[0] == 0  # ACCESS_ALLOWED_ACE
        assert win.sid_string(ace.value + 8) == win.user_sid()
    finally:
        win.LocalFree(descriptor)
    with pytest.raises(FileExistsError):
        win.create_private_file(target)
    assert target.read_bytes() == b"synthetic-only"
    platform_runtime.check_file(target)
    # The daemon uses the same creator and reuses its original 32 bytes.
    key = daemon.authkey(paths)
    assert len(key) == 32 and daemon.authkey(paths) == key
    platform_runtime.check_file(paths.key)
    assert sorted(p.name for p in runtime.iterdir()) == ["authkey", target.name]
    assert capsys.readouterr() == ("", "")


def test_cli_help_is_utf8_when_redirected(runtime, capsys):
    from gentle_stream import main

    with pytest.raises(SystemExit) as exit:
        main(["--help"])
    assert exit.value.code == 0
    expected = capsys.readouterr()
    proc = subprocess.run([sys.executable, "-m", "gentle_stream", "--help"],
                          capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, expected.out, expected.err)
    assert not runtime.exists()  # help does not create runtime state


def test_bridge_reads_utf8_model_output_without_changing_arguments(monkeypatch, capsys):
    from gentle_stream import bridge
    from gentle_stream.style import Style

    original = subprocess.Popen
    calls = []
    output = json.dumps({"name": "Café 雨"}, ensure_ascii=False).encode("utf-8").hex()
    def launch(argv, **kw):
        calls.append(argv)
        return original([sys.executable, "-c", f"import sys; sys.stdout.buffer.write(bytes.fromhex('{output}'))"], **kw)
    monkeypatch.setattr(bridge.subprocess, "Popen", launch)
    style, usage = bridge.GentleShell(binary="fake-native-model").interpret_with_usage("calm", Style())
    assert style.name == "Café 雨" and usage is None
    assert len(calls) == 1 and calls[0][:2] == ["fake-native-model", "--"]
    assert capsys.readouterr() == ("", "")


def test_cli_attach_singleton_auth_and_stop(runtime, capsys):
    from multiprocessing.connection import AuthenticationError, Client
    from gentle_stream import client, daemon, platform_runtime

    assert run_ctl(capsys, "status") == (0, '{"running": false}\n', "")
    first = client.ensure(timeout=15)
    first.pause()
    first.set_volume(0.27)
    before = first.request("state")
    second = client.ensure(timeout=15)
    assert second.pid == first.pid
    second.detach()
    duplicate = subprocess.run([sys.executable, "-m", "gentle_stream", "daemon"],
                               capture_output=True, text=True, timeout=15)
    assert (duplicate.returncode, duplicate.stdout, duplicate.stderr) == (0, "", '{"running": true}\n')
    paths = daemon.prepare()
    with pytest.raises((AuthenticationError, EOFError, OSError)):
        Client(platform_runtime.address(paths), family=platform_runtime.FAMILY, authkey=b"invalid")
    # Rejected authentication cannot pause, stop, change volume, or reset counters.
    after = first.request("state")
    for name in ("pid", "playing", "volume", "underruns", "output_underflows"):
        assert after[name] == before[name]
    assert run_ctl(capsys, "play") == (0, "", "")
    assert first.request("state")["playing"]
    assert run_ctl(capsys, "stop") == (0, "", "")
    first.detach()
    wait_for(lambda: _no_player(client))
    assert run_ctl(capsys, "status") == (0, '{"running": false}\n', "")


def offline_audio(**kwargs):
    """Fail visibly if any model launch is attempted by the isolated fake daemon."""
    from gentle_stream.bridge import GentleShell
    from tests.test_daemon import FakeAudio

    def forbidden(*args, **kw):
        Path(os.environ["GENTLE_STREAM_TEST_MODEL_MARKER"]).write_text("unexpected model call")
        raise AssertionError("Panic must not call a model")

    GentleShell._run = forbidden
    return FakeAudio(**kwargs)


def test_public_ipc_panic_preserves_native_session(runtime, monkeypatch, capsys):
    from gentle_stream import client
    from gentle_stream.style import Style

    marker = runtime.parent / "model-called"
    monkeypatch.setenv("GENTLE_STREAM_TEST_MODEL_MARKER", str(marker))
    monkeypatch.setenv("GENTLE_STREAM_AUDIO_FACTORY", "tests.test_native_windows:offline_audio")
    player = client.ensure(timeout=15)
    songs = (Style(name="First", bpm=140),
             Style(name="Retained", bpm=140, lead="none", drums="none"))
    player.set_playlist(songs, start_index=1, name="Recovery", continuous=True)
    wait_for(lambda: player.spectrum(8) is not None and player.now.style == songs[1])
    player.pause()
    player.set_volume(.23)
    before = player.request("state")
    player.panic()
    after = player.request("state")
    assert player.pid == before["pid"] and not player.playing and player.volume == .23
    assert player.now.style == songs[1] and player.now.bar == 0
    assert player.now.piece == before["now"]["piece"]
    assert player.continuous and player.playlist_name == "Recovery" and player.playlist_index == 1
    for name in ("pid", "playing", "volume", "underruns", "output_underflows"):
        assert after[name] == before[name]
    player.detach()
    attached = client.connect()
    assert attached.pid == before["pid"] and attached.now.style == songs[1]
    assert not attached.playing and attached.volume == .23
    attached.next()
    attached.play()
    wait_for(lambda: attached.spectrum(8) is not None and attached.now.style == songs[0])
    attached.close()
    assert not marker.exists()
    assert capsys.readouterr() == ("", "")


def _no_player(client):
    try:
        player = client.connect()
    except client.NoPlayer:
        return True
    player.detach()
    return False


def test_rejected_json_leaves_paused_state_unchanged(runtime, capsys):
    from gentle_stream import client

    player = client.ensure(timeout=15)
    player.pause()
    before = player.request("state")
    for raw, error in ((b"not JSON", "bad request: not JSON"),
                       (b"\x80\x04N.", "bad request: not JSON"),
                       (b"\xff", "bad request: not JSON"),
                       (b"[]", "bad request: 'cmd'"),
                       (b'{"cmd":"set_volume","volume":"bad"}',
                        "bad request: could not convert string to float: 'bad'")):
        player._conn.send_bytes(raw)
        answer = json.loads(player._conn.recv_bytes())
        assert answer == {"ok": False, "error": error}
        after = player.request("state")
        for name in ("pid", "playing", "volume", "underruns", "output_underflows"):
            assert after[name] == before[name]
    player.close()
    assert capsys.readouterr() == ("", "")


def test_stale_lock_and_key_recover_without_key_rotation(runtime):
    from gentle_stream import client, daemon

    paths = daemon.prepare()
    key = daemon.authkey(paths)
    paths.lock.write_text("99999999")
    first = client.ensure(timeout=15)
    first.close()
    second = client.ensure(timeout=15)
    assert second.playing and daemon.authkey(paths) == key
    second.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows crashed-process lock/pipe recovery")
def test_native_crash_releases_singleton_without_rotating_key(runtime, monkeypatch, capsys):
    from gentle_stream import client, daemon

    original = subprocess.Popen
    owned = []
    def launch(argv, **kw):
        proc = original(argv, **kw)
        owned.append(proc)
        return proc
    monkeypatch.setattr(subprocess, "Popen", launch)
    first = client.ensure(timeout=15)
    old_pid = first.pid
    key = daemon.authkey(daemon.prepare())
    assert len(owned) == 1
    # The Windows venv redirector may have a different PID from its interpreter.
    # This PID is authenticated state from the test's own isolated fake player.
    import signal
    os.kill(old_pid, signal.SIGTERM)
    owned[0].wait(timeout=5)
    first.detach()
    assert run_ctl(capsys, "status") == (0, '{"running": false}\n', "")
    recovered = client.ensure(timeout=15)
    assert recovered.pid != old_pid and recovered.playing
    assert daemon.authkey(daemon.prepare()) == key
    recovered.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL and reparse-point boundary")
def test_windows_runtime_rejects_unprotected_directory(runtime, capsys):
    from gentle_stream import daemon

    runtime.mkdir(parents=True)
    with pytest.raises(daemon.RuntimeDirError):
        daemon.prepare()
    code, out, err = run_ctl(capsys, "status")
    assert (code, out, err) == (1, "", f"gentle-stream: {runtime} is not private to this user\n")
    assert list(runtime.iterdir()) == []  # rejection creates no key, lock or player


def blocked_audio(**kwargs):
    """Report the interpreter PID then block before creating any audio device."""
    Path(os.environ["GENTLE_STREAM_TEST_START_PID"]).write_text(str(os.getpid()))
    time.sleep(60)


@pytest.mark.skipif(os.name != "nt", reason="Windows anonymous readiness pipe deadline")
def test_native_startup_deadline_terminates_only_owned_daemon(runtime, monkeypatch, capsys):
    from gentle_stream import client, processes

    marker = runtime.parent / "startup.pid"
    monkeypatch.setenv("GENTLE_STREAM_TEST_START_PID", str(marker))
    monkeypatch.setenv("GENTLE_STREAM_AUDIO_FACTORY", "tests.test_native_windows:blocked_audio")
    with pytest.raises(client.PlayerError) as error:
        client.ensure(timeout=10)
    assert str(error.value) == f"the player did not start in time (see {runtime / 'player.log'})"
    assert marker.exists() and marker.read_text().isdigit()
    wait_for(lambda: not processes.is_alive(int(marker.read_text())), timeout=5)
    assert capsys.readouterr() == ("", "")
    assert run_ctl(capsys, "status") == (0, '{"running": false}\n', "")


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object descendant containment")
def test_owned_process_tree_cancellation_does_not_kill_bystander(tmp_path):
    from gentle_stream import processes

    child_file = tmp_path / "child.pid"
    code = ("import subprocess,sys,time,pathlib; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)")
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    proc = None
    try:
        proc = processes.spawn([sys.executable, "-c", code, str(child_file)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        wait_for(lambda: child_file.exists() and child_file.read_text().isdigit(), timeout=10)
        child = int(child_file.read_text())
        processes.kill_tree(proc)
        proc.communicate(timeout=5)
        wait_for(lambda: not processes.is_alive(child), timeout=5)
        assert bystander.poll() is None
    finally:
        if proc is not None:
            processes.kill_tree(proc)
            processes.release(proc)
        bystander.terminate()
        bystander.wait(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object descendant timeout")
def test_bridge_timeout_owns_tree_and_preserves_error(tmp_path, monkeypatch):
    from gentle_stream import bridge, processes

    child_file = tmp_path / "timeout-child.pid"
    script = tmp_path / "model.py"
    script.write_text("import subprocess,sys,time,pathlib\n"
                      "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
                      f"pathlib.Path({str(child_file)!r}).write_text(str(p.pid))\n"
                      "time.sleep(60)\n")
    original = subprocess.Popen
    def launch(argv, **kw):
        return original([sys.executable, str(script)], **kw)
    monkeypatch.setattr(bridge.subprocess, "Popen", launch)
    with pytest.raises(bridge.InterpretError, match="^gentle-shell timed out after 2s$"):
        bridge.GentleShell(binary="fake-model", timeout=2)._run(["unchanged"])
    assert child_file.exists()
    wait_for(lambda: not processes.is_alive(int(child_file.read_text())), timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object descendant cancellation")
def test_bridge_cancel_owns_descendants(tmp_path, monkeypatch):
    from gentle_stream import bridge, processes

    child_file = tmp_path / "cancel-child.pid"
    script = tmp_path / "model.py"
    script.write_text("import subprocess,sys,time,pathlib\n"
                      "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
                      f"pathlib.Path({str(child_file)!r}).write_text(str(p.pid))\n"
                      "time.sleep(60)\n")
    original = subprocess.Popen
    def launch(argv, **kw):
        return original([sys.executable, str(script)], **kw)
    monkeypatch.setattr(bridge.subprocess, "Popen", launch)
    shell = bridge.GentleShell(binary="fake-model", timeout=20)
    errors = []
    def call():
        try:
            shell._run(["unchanged"])
        except bridge.InterpretError as error:
            errors.append(str(error))
    thread = threading.Thread(target=call)
    thread.start()
    try:
        wait_for(lambda: child_file.exists() and child_file.read_text().isdigit(), timeout=10)
        shell.cancel()
        thread.join(timeout=5)
        assert not thread.is_alive() and errors == ["cancelled"]
        wait_for(lambda: not processes.is_alive(int(child_file.read_text())), timeout=5)
    finally:
        shell.cancel()
        thread.join(timeout=5)
