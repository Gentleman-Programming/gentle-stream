"""Public transport contract, with no native dependency or audio."""
import threading
import time
import types
from contextlib import suppress

import pytest

from gentle_stream import desktop_transport as transport


class FakePTY:
    def __init__(self):
        self.calls = []
        self.output = ["first", "ü"]
        self.alive = True
        self.pid = 4321
        self.owner = None
        self.partial = False

    def record(self, *call):
        current = threading.get_ident()
        if self.owner is None:
            self.owner = current
        assert self.owner == current
        self.calls.append(call)

    def spawn(self, *args, **kwargs):
        self.record("spawn", args, kwargs)
        return True

    def read(self, blocking=False):
        self.record("read", blocking)
        return self.output.pop(0) if self.output else ""

    def write(self, text):
        self.record("write", text)
        return 1 if self.partial else len(text.encode("utf-8"))

    def set_size(self, cols, rows):
        self.record("resize", cols, rows)

    def isalive(self):
        self.record("alive")
        return self.alive

    def iseof(self):
        self.record("eof")
        return not self.alive and not self.output

    def get_exitstatus(self):
        self.record("status")
        return 7

    def cancel_io(self):
        self.record("cancel")
        return True


def wait_for(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("worker did not progress")
        time.sleep(0.005)


@pytest.fixture
def session(monkeypatch):
    native = FakePTY()
    outputs, exits = [], []
    monkeypatch.setattr(transport, "_make_pty", lambda cols, rows: native)
    pty = transport.DesktopPTY(["C:/python.exe", "a b", 'c"d', "ü"], cwd="C:/work", on_output=outputs.append, on_exit=exits.append)
    yield pty, native, outputs, exits
    native.alive = False
    if pty.snapshot()["state"] != "new":
        with suppress(transport.TransportError):
            pty.close(lambda: None, timeout=2)


def test_ordered_persistent_io_and_observed_close(session):
    pty, native, outputs, exits = session
    pty.start()
    with pytest.raises(transport.TransportError):
        pty.start()
    pty.write("ab")
    pty.write("ü")
    pty.resize(93, 31)
    wait_for(lambda: any(c[0] == "resize" for c in native.calls))
    assert [c for c in native.calls if c[0] == "write"] == [("write", "ab"), ("write", "ü")]
    assert ("resize", 93, 31) in native.calls
    spawn = native.calls[0]
    assert spawn == ("spawn", ("C:/python.exe",), {"cmdline": ' "a b" c\\"d ü', "cwd": "C:/work"})
    assert pty.snapshot()["pid"] == 4321
    callbacks = []
    def shutdown():
        callbacks.append(1)
        native.output.append("tail")
        native.alive = False
    assert pty.close(shutdown, timeout=2) == 7
    assert pty.close(shutdown, timeout=2) == 7
    assert outputs == ["first", "ü", "tail"]
    assert exits == [7] and callbacks == [1]
    assert pty.snapshot()["reader_done"] is True
    assert pty.snapshot()["state"] == "exited"
    assert not pty.snapshot()["worker_alive"]
    assert not any(c[0] == "cancel" for c in native.calls)
    assert not any(k in pty.snapshot() for k in ("argv", "cwd", "output", "env"))


@pytest.mark.parametrize("value", [None, 1, "", "x" * 16385, "\ud800"])
def test_bad_input_has_no_effect(session, value):
    pty, native, _, _ = session
    pty.start()
    before = pty.snapshot()["accepted"]
    with pytest.raises((ValueError, TypeError)):
        pty.write(value)
    assert pty.snapshot()["accepted"] == before
    assert not any(c[0] == "write" for c in native.calls)


@pytest.mark.parametrize("size", [(0, 24), (80, -1), (True, 24), (80, 501), (501, 24), (2.5, 10)])
def test_bad_resize_has_no_effect(session, size):
    pty, native, _, _ = session
    pty.start()
    before = pty.snapshot()["accepted"]
    with pytest.raises(ValueError):
        pty.resize(*size)
    assert pty.snapshot()["accepted"] == before
    assert not any(c[0] == "resize" for c in native.calls)


def test_timeout_retains_session_and_shutdown_once(session):
    pty, native, _, exits = session
    pty.start()
    called = []
    for _ in range(2):
        with pytest.raises(TimeoutError):
            pty.close(lambda: called.append(1), timeout=0.02)
    assert called == [1] and exits == []
    assert pty.snapshot()["state"] == "closing"
    assert pty.snapshot()["reader_done"] is False
    assert pty.snapshot()["pid"] == native.pid
    with pytest.raises(transport.TransportError):
        pty.write("no")
    native.alive = False
    assert pty.close(lambda: called.append(2), timeout=2) == 7
    assert called == [1] and exits == [7]


def test_bounded_queue_and_output_backpressure(monkeypatch):
    native, outputs = FakePTY(), []
    monkeypatch.setattr(transport, "_make_pty", lambda *a: native)
    entered, release = threading.Event(), threading.Event()
    def consume(text):
        entered.set()
        release.wait(2)
        outputs.append(text)
    pty = transport.DesktopPTY(["python"], cwd="C:/work", on_output=consume, on_exit=lambda c: None)
    pty.start()
    assert entered.wait(2)
    try:
        for _ in range(transport._QUEUE_LIMIT):
            pty.write("x")
        before = pty.snapshot()["accepted"]
        with pytest.raises(transport.BackpressureError):
            pty.write("rejected")
        assert pty.snapshot()["accepted"] == before
        assert not any(c[0] == "write" for c in native.calls)
    finally:
        release.set()
        native.alive = False
        assert pty.close(lambda: None, timeout=2) == 7
    assert len([c for c in native.calls if c[0] == "write"]) == transport._QUEUE_LIMIT
    assert outputs == ["first", "ü"]


@pytest.mark.parametrize("argv", [[], "python", [""], ["x\0"], ["x", "\ud800"], ["x" * 32768]])
def test_bad_argv_never_spawns(monkeypatch, argv):
    calls = []
    monkeypatch.setattr(transport, "_make_pty", lambda *a: calls.append(a))
    with pytest.raises((ValueError, TypeError)):
        transport.DesktopPTY(argv, cwd="C:/work", on_output=lambda t: None, on_exit=lambda c: None)
    assert calls == []


def test_partial_write_is_explicit_uncertain_error(session):
    pty, native, _, exits = session
    native.partial = True
    pty.start()
    pty.write("abc")
    wait_for(lambda: pty.snapshot()["state"] == "failed")
    with pytest.raises(transport.TransportError):
        pty.close(lambda: None, timeout=2)
    assert exits == []
    assert pty.snapshot()["pid"] == native.pid


def test_platform_and_optional_dependency_are_explicit(monkeypatch):
    monkeypatch.setattr(transport.sys, "platform", "linux")
    with pytest.raises(transport.TransportError, match="Windows"):
        transport._make_pty(80, 24)
    monkeypatch.setattr(transport.sys, "platform", "win32")
    monkeypatch.setitem(transport.sys.modules, "winpty", None)
    with pytest.raises(transport.TransportError, match="pywinpty"):
        transport._make_pty(80, 24)


def test_production_factory_explicit_conpty(monkeypatch):
    import types
    calls = []
    monkeypatch.setattr(transport.sys, "platform", "win32")
    monkeypatch.setitem(transport.sys.modules, "winpty", types.SimpleNamespace(PTY=lambda *a, **k: calls.append((a, k)), Backend=types.SimpleNamespace(ConPTY=0)))
    transport._make_pty(80, 24)
    assert calls == [((80, 24), {"backend": 0})]


def test_shutdown_callback_is_bounded_and_not_retried(session):
    pty, native, _, _ = session
    pty.start()
    entered, release = threading.Event(), threading.Event()
    calls = []
    def shutdown():
        calls.append(1)
        entered.set()
        release.wait(0.5)
        native.alive = False
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            pty.close(shutdown, timeout=0.02)
        assert time.monotonic() - started < 0.3
        assert entered.is_set() and calls == [1]
        assert pty.snapshot()["state"] == "closing"
    finally:
        release.set()
    assert pty.close(shutdown, timeout=2) == 7
    assert calls == [1]


def test_no_input_accepted_after_eof_observation(session):
    pty, native, _, _ = session
    entered, release = threading.Event(), threading.Event()
    original = native.get_exitstatus
    def status():
        entered.set()
        release.wait(2)
        return original()
    native.get_exitstatus = status
    pty.start()
    native.alive = False
    assert entered.wait(2)
    before = pty.snapshot()["accepted"]
    try:
        with pytest.raises(transport.TransportError):
            pty.write("late")
        assert pty.snapshot()["accepted"] == before
    finally:
        release.set()
    assert pty.close(lambda: None, timeout=2) == 7
    assert not any(c[0] == "write" for c in native.calls)


def test_failed_shutdown_callback_is_explicit_and_once(session):
    pty, native, _, _ = session
    pty.start()
    calls = []
    def shutdown():
        calls.append(1)
        raise LookupError("private callback failure")
    for _ in range(2):
        with pytest.raises(transport.TransportError, match="Shutdown callback failed"):
            pty.close(shutdown, timeout=0.1)
    assert calls == [1]
    assert pty.snapshot()["state"] == "closing"
    native.alive = False


@pytest.mark.parametrize("timeout", [-1, 121, float("inf"), float("nan"), True, "1"])
def test_invalid_close_never_requests_shutdown(session, timeout):
    pty, _, _, _ = session
    pty.start()
    calls = []
    before = pty.snapshot()
    with pytest.raises(ValueError):
        pty.close(lambda: calls.append(1), timeout=timeout)
    after = pty.snapshot()
    assert calls == []
    assert after["accepted"] == before["accepted"]
    assert after["state"] == "running" and not after["shutdown_requested"]


def test_spawn_rejection_never_retries(session):
    pty, native, _, exits = session
    def reject(*args, **kwargs):
        native.record("spawn-rejected")
        return False
    native.spawn = reject
    with pytest.raises(transport.TransportError):
        pty.start()
    with pytest.raises(transport.TransportError):
        pty.start()
    assert native.calls == [("spawn-rejected",)]
    assert pty.snapshot()["accepted"] == 0 and exits == []


def test_input_and_resize_boundaries(session):
    pty, native, _, _ = session
    pty.start()
    pty.write("😀" * 16384)
    pty.resize(500, 1)
    wait_for(lambda: any(c[0] == "resize" for c in native.calls))
    assert pty.snapshot()["written"] == 65536
    assert ("resize", 500, 1) in native.calls
    native.alive = False
    assert pty.close(lambda: None, timeout=2) == 7


class WinptyError(Exception):
    """Injected public native exception type; no optional dependency needed."""


@pytest.mark.parametrize("error,normal", [
    (WinptyError("Standard out reached EOF"), True),
    (WinptyError("read failed"), False),
    (WinptyError("Standard out reached EOF", "extra"), False),
    (RuntimeError("Standard out reached EOF"), False),
    (EOFError("Standard out reached EOF"), False),
])
def test_only_typed_exact_native_eof_completes(session, monkeypatch, error, normal):
    pty, native, outputs, exits = session
    monkeypatch.setitem(transport.sys.modules, "winpty", types.SimpleNamespace(WinptyError=WinptyError))
    original_read = native.read
    def read(blocking=False):
        if native.output or native.alive:
            return original_read(blocking)
        native.record("read-error")
        raise error
    native.read = read
    # Normal drain must not depend on cancellation, which can itself raise.
    def cancel():
        raise AssertionError("normal EOF must not cancel drained I/O")
    native.cancel_io = cancel
    pty.start()
    def shutdown():
        native.output.append("tail")
        native.alive = False
    if normal:
        assert pty.close(shutdown, timeout=2) == 7
        assert exits == [7]
        assert pty.snapshot()["reader_done"]
        assert not pty.snapshot()["worker_alive"]
        assert not pty.snapshot()["io_cancelled"]
    else:
        with pytest.raises(transport.TransportError) as caught:
            pty.close(shutdown, timeout=2)
        assert caught.value.__cause__ is error
        assert exits == []
        assert not pty.snapshot()["reader_done"]
        assert pty.snapshot()["exit_status"] is None
    assert outputs == ["first", "ü", "tail"]
    assert pty.snapshot()["accepted"] == 0
    assert pty.snapshot()["written"] == 0


def test_native_eof_waits_for_flag_publication(session, monkeypatch):
    pty, native, outputs, exits = session
    monkeypatch.setitem(transport.sys.modules, "winpty", types.SimpleNamespace(WinptyError=WinptyError))
    original_read = native.read
    polls = []
    def read(blocking=False):
        if native.output or native.alive:
            return original_read(blocking)
        raise WinptyError("Standard out reached EOF")
    def eof():
        polls.append(1)
        return len(polls) >= 3
    native.read, native.iseof = read, eof
    pty.start()
    assert pty.close(lambda: setattr(native, "alive", False), timeout=2) == 7
    assert len(polls) >= 3 and outputs == ["first", "ü"] and exits == [7]
    assert not pty.snapshot()["worker_alive"]


def test_output_failure_is_explicit_without_exit_callback(monkeypatch):
    native, exits = FakePTY(), []
    monkeypatch.setattr(transport, "_make_pty", lambda *a: native)
    def reject_output(text):
        raise LookupError("private output failure")
    pty = transport.DesktopPTY(["python"], cwd="C:/work", on_output=reject_output, on_exit=exits.append)
    with suppress(transport.TransportError):
        pty.start()
    wait_for(lambda: pty.snapshot()["state"] == "failed")
    shutdown = []
    with pytest.raises(transport.TransportError):
        pty.close(lambda: shutdown.append(1), timeout=2)
    assert shutdown == [1] and exits == []
    assert not pty.snapshot()["reader_done"]
    native.alive = False
