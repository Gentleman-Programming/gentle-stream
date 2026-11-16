"""Optional Windows ConPTY transport; no UI, shell, audio or model imports."""

from __future__ import annotations

import math
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence

_QUEUE_LIMIT = 64
_INPUT_LIMIT = 16384
_POLL_SECONDS = 0.02


class TransportError(RuntimeError):
    """The session cannot safely fulfil the requested operation."""


class BackpressureError(TransportError):
    """Nothing was accepted; retry later without reordering input."""


def _make_pty(cols: int, rows: int):
    if sys.platform != "win32":
        raise TransportError("Desktop transport requires Windows")
    try:
        import winpty
    except ImportError as exc:
        raise TransportError("Desktop transport requires optional pywinpty") from exc
    return winpty.PTY(cols, rows, backend=winpty.Backend.ConPTY)


def _text(value: str, limit: int, *, empty: bool = False) -> None:
    if not isinstance(value, str):
        raise TypeError("Expected text")
    if (not empty and not value) or len(value) > limit:
        raise ValueError("Text length outside transport bounds")
    try:
        value.encode("utf-16-le")
    except UnicodeEncodeError as exc:
        raise ValueError("Invalid Unicode text") from exc


class DesktopPTY:
    """One persistent child, one native-I/O worker, bounded FIFO commands.

    Output callbacks execute on the worker and provide lossless backpressure.
    They must return promptly, never synchronously call start/close, and marshal
    UI work themselves. No automatic termination is available in this API.
    """

    def __init__(self, argv: Sequence[str], *, cwd: str,
                 on_output: Callable[[str], None], on_exit: Callable[[int], None]):
        if isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence) or not 1 <= len(argv) <= 128:
            raise ValueError("Expected 1..128 argv entries")
        for item in argv:
            _text(item, 32766, empty=True)
            if "\0" in item:
                raise ValueError("NUL in argv")
        if not argv[0]:
            raise ValueError("Empty executable")
        command = " " + subprocess.list2cmdline(list(argv[1:])) if len(argv) > 1 else None
        if len((command or "").encode("utf-16-le")) // 2 + len(argv[0].encode("utf-16-le")) // 2 > 32760:
            raise ValueError("Command line too large")
        _text(cwd, 32760)
        if "\0" in cwd:
            raise ValueError("NUL in cwd")
        if not callable(on_output) or not callable(on_exit):
            raise TypeError("Callbacks must be callable")
        self._appname, self._command, self._cwd = argv[0], command, cwd
        self._on_output, self._on_exit = on_output, on_exit
        self._lock = threading.Lock()
        self._commands = queue.Queue(maxsize=_QUEUE_LIMIT)
        self._wake = threading.Event()
        self._started = threading.Event()
        self._thread = None
        self._shutdown_thread = None
        self._shutdown_done = threading.Event()
        self._shutdown_error = None
        self._native = None  # Retained even after failures; deletion is not exit proof.
        self._state = "new"
        self._pid = None
        self._status = None
        self._error = None
        self._reader_done = False
        self._shutdown_requested = False
        self._accepted = self._written = self._output_chars = self._read_calls = 0
        self._io_cancelled = False

    def snapshot(self) -> dict:
        with self._lock:
            return {"state": self._state, "pid": self._pid, "exit_status": self._status,
                    "reader_done": self._reader_done, "accepted": self._accepted,
                    "written": self._written, "output_chars": self._output_chars,
                    "pending": self._commands.qsize(), "shutdown_requested": self._shutdown_requested,
                    "shutdown_done": self._shutdown_done.is_set(), "read_calls": self._read_calls,
                    "io_cancelled": self._io_cancelled,
                    "worker_alive": self._thread is not None and self._thread.is_alive()}

    def start(self) -> None:
        with self._lock:
            if self._state != "new":
                raise TransportError("Transport can only start once")
            self._state = "starting"
            self._thread = threading.Thread(target=self._run, name="desktop-conpty", daemon=True)
            self._thread.start()
        if not self._started.wait(5):
            raise TimeoutError("ConPTY startup uncertain; session retained")
        with self._lock:
            if self._error is not None:
                raise TransportError("ConPTY startup failed; session retained") from self._error

    def _enqueue(self, command: tuple) -> None:
        with self._lock:
            if self._state != "running":
                raise TransportError("Transport is not accepting input")
            try:
                self._commands.put_nowait(command)
            except queue.Full as exc:
                raise BackpressureError("Transport queue full") from exc
            self._accepted += 1
            self._wake.set()

    def write(self, text: str) -> None:
        _text(text, _INPUT_LIMIT)
        self._enqueue(("write", text))

    def resize(self, cols: int, rows: int) -> None:
        if any(type(n) is not int or not 1 <= n <= 500 for n in (cols, rows)):
            raise ValueError("Columns and rows must be integers in 1..500")
        self._enqueue(("resize", cols, rows))

    def close(self, request_shutdown: Callable[[], None], timeout: float = 10.0) -> int:
        if not callable(request_shutdown):
            raise TypeError("Shutdown callback must be callable")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0 or timeout > 120:
            raise ValueError("Timeout must be finite and in 0..120 seconds")
        deadline = time.monotonic() + timeout
        with self._lock:
            thread = self._thread
            if thread is None or threading.current_thread() in (thread, self._shutdown_thread):
                raise TransportError("Close requires an external caller and started session")
            if not self._shutdown_requested and self._state not in ("exited", "draining"):
                self._shutdown_requested = True
                if self._state != "failed":
                    self._state = "closing"
                self._shutdown_thread = threading.Thread(
                    target=self._request_shutdown, args=(request_shutdown,),
                    name="desktop-shutdown", daemon=True,
                )
                self._shutdown_thread.start()
            shutdown_thread = self._shutdown_thread
        self._wake.set()
        # Callback execution is bounded by the same deadline as reader completion.
        # A stalled callback remains retained and is never implicitly retried.
        if shutdown_thread is not None:
            shutdown_thread.join(max(0, deadline - time.monotonic()))
            if shutdown_thread.is_alive():
                raise TimeoutError("Shutdown callback uncertain; session retained")
            with self._lock:
                if self._shutdown_error is not None:
                    raise TransportError("Shutdown callback failed; session retained") from self._shutdown_error
        thread.join(max(0, deadline - time.monotonic()))
        if thread.is_alive():
            raise TimeoutError("ConPTY exit/drain uncertain; session retained")
        with self._lock:
            if self._error is not None or not self._reader_done or self._status is None:
                raise TransportError("ConPTY completion unconfirmed; session retained") from self._error
            return self._status

    def _request_shutdown(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception as exc:
            with self._lock:
                self._shutdown_error = exc
        finally:
            self._shutdown_done.set()

    def _run(self) -> None:
        try:
            native = self._native = _make_pty(80, 24)
            if not native.spawn(self._appname, cmdline=self._command, cwd=self._cwd):
                raise TransportError("ConPTY spawn rejected")
            with self._lock:
                self._pid = native.pid
                if self._state == "starting":
                    self._state = "running"
            self._started.set()
            eof_seen = False
            while True:
                self._wake.clear()
                # One command per read gives both directions bounded fairness.
                try:
                    command = self._commands.get_nowait()
                except queue.Empty:
                    command = None
                if command is not None:
                    if command[0] == "write":
                        written = native.write(command[1])
                        # pywinpty reports UTF-8 bytes, not Python code points.
                        if written != len(command[1].encode("utf-8")):
                            raise TransportError("ConPTY partial write; delivery uncertain")
                        with self._lock:
                            self._written += written
                    else:
                        native.set_size(command[1], command[2])
                with self._lock:
                    self._read_calls += 1
                try:
                    text = "" if eof_seen else native.read(blocking=False)
                except Exception as exc:
                    # The pinned binding maps all native errors to WinptyError.
                    # Death/EOF flags alone cannot distinguish a failed read.
                    import winpty

                    if not isinstance(exc, winpty.WinptyError) or exc.args != ("Standard out reached EOF",):
                        raise
                    # The sentinel can precede flag publication. Keep polling
                    # on this worker; close's deadline still bounds the caller.
                    eof_seen = True
                    text = ""
                if text:
                    self._on_output(text)
                    with self._lock:
                        self._output_chars += len(text)
                drained = False
                if not text and not native.isalive() and native.iseof():
                    with self._lock:
                        if self._commands.empty():
                            # Enqueue and this transition share the lock: no input
                            # can be accepted after the final empty-queue check.
                            self._state = "draining"
                            drained = True
                if drained:
                    status = native.get_exitstatus()
                    if type(status) is not int:
                        raise TransportError("ConPTY exit status unavailable")
                    self._on_exit(status)
                    with self._lock:
                        self._status = status
                        self._reader_done = True
                        self._state = "exited"
                    return
                if not text and self._commands.empty():
                    self._wake.wait(_POLL_SECONDS)
        except Exception as exc:
            with self._lock:
                self._error = exc
                self._state = "failed"
        finally:
            self._started.set()
