# Optional Windows desktop transport

`gentle_stream.desktop_transport` is an independent, lazy-loaded low-level
ConPTY adapter. Importing it does not initialize audio, an application, a
window, a daemon or a model. It does not change the existing CLI. Runtime
requires Windows and optional pywinpty (capability target: 3.1.2); missing
platform/dependency errors are explicit, without WinPTY or shell fallback.

## Controller interface

```python
pty = DesktopPTY(argv, cwd=working_directory,
                 on_output=deliver_terminal_data, on_exit=observe_exit)
pty.start()                 # exactly once; initial size 80 columns, 24 rows
pty.write(text)             # enqueue, not synchronous delivery acknowledgement
pty.resize(93, 31)          # columns FIRST, then rows; same child
status = pty.close(request_app_shutdown, timeout=10.0)
```

`argv` is a sequence of 1–128 Unicode strings; executable must be nonempty.
Arguments may be empty. NUL, lone surrogates and oversized command lines are
rejected before native creation. The Windows argument tail uses
`subprocess.list2cmdline`, prefixed by the space expected by low-level
`spawn(appname, cmdline=..., cwd=...)`; it is not a shell command. The combined
executable/argument budget is 32,760 UTF-16 code units. `cwd` is a nonempty
Unicode string of at most 32,760 code points without NUL. No environment is
constructed, read back or logged by this adapter.

`write` accepts 1–16,384 Unicode code points (including VT/control data), with
no lone surrogates. `resize` requires exact integers, not booleans, in 1–500
for both dimensions. Invalid calls leave the queue and acceptance counter
unchanged. Input and resize share one 64-command FIFO. A full queue raises
`BackpressureError` without accepting anything: callers must stop accepting
new input, then retry the same item in order. Do not silently discard VT or
input. Native write counts are interpreted as UTF-8 bytes; a short write is
an explicit delivery-uncertain failure, not silently retried.

## Threading, flow control and shutdown

One worker constructs the PTY with **explicit `Backend.ConPTY`**, spawns it,
and serializes every native call, including reads, writes, resize, liveness,
EOF and exit-status. No native calls occur on the controller,
UI or shutdown-callback threads. Each iteration handles at most one command
before reading output. Idle iterations wait 20 ms or until a queued command
wakes them; there is no idle busy loop or output queue.

`on_output(text)` runs synchronously on the I/O worker. This gives lossless
backpressure and ordered output: the next native read waits for the callback.
A UI adapter must provide its own bounded, ordered handoff. Callbacks must not
synchronously call `start` or `close`, nor block on the I/O worker. Callback
exceptions fail the transport explicitly; no successful completion is claimed.
Native `read` has no maximum-chunk parameter: the size/allocation of a single
native output chunk is **not bounded by this adapter or proven by this probe**.
The input FIFO payload is bounded by approximately 4 MiB of UTF-8 data, plus
Python/string/command overhead; native buffers have separate unverified limits.

`close` stops accepting input and invokes the supplied shutdown callback once
on a separate daemon thread. That callback must use the application's dedicated
control channel; the transport never sends `q`, Ctrl-C or other terminal input
as implicit shutdown. Callback execution and worker join share the requested
finite deadline (0–120 seconds; default 10). Concurrent/repeated `close` calls
do not invoke shutdown again. Callback failure is explicit; a callback that
stalls causes timeout and remains retained, not cancelled or retried.

Success requires observed child death, EOF, drained output and accepted
commands, an actual integer `get_exitstatus`, completed output/exit callbacks,
and a joined reader worker. `on_exit(status)` occurs once after observed
EOF/status. The EOF transition is locked against new enqueue operations.
Only public `winpty.WinptyError` with exact `args == ("Standard out reached EOF",)`
is normalized. That sentinel alone is not success: the worker polls for native
death and EOF, which may be published later; `close` retains its finite deadline.
Every other read exception remains a failure even with dead/EOF/integer-status
flags. Normal drained completion does not call `cancel_io`; cancellation is
neither needed nor reader-join proof. No cross-thread cancellation is attempted.

Timeout, failed I/O or unavailable status raises rather
than inventing an exit status. The object retains native ownership, PID and
uncertain state; do not create a replacement session as an implicit recovery.
The native API has no `close`/`terminate` method. PID is diagnostic, not authority
to kill; `fd` is not assumed to be a process HANDLE. Object deletion and daemon
thread/interpreter exit are **not cleanup evidence**. No forced termination is
implemented. An unexpected blocked native read may outlive the close deadline.

`snapshot()` contains only lifecycle state, PID, exit status, queue/counters,
worker/cancellation and shutdown-completion flags. `written` counts UTF-8 bytes;
`output_chars` counts delivered Python code points. It exposes no terminal text,
command arguments, environment, cwd or exception payloads. Nothing is logged.

## Verification and remaining integration

Deterministic tests inject only the private native factory and exercise the
public constructor/start/write/resize/close/snapshot interface. They cover
rejection without effects, ordered persistent I/O, backpressure, short writes,
shutdown deadlines/errors, EOF admission races, callbacks and optional imports.
No existing application, player, CLI or window module is imported or edited.

Run the focused suite with the existing project test environment:

```text
.validation/windows/venv/Scripts/python.exe -m pytest -q tests/test_desktop_transport.py
```

The separate finite native capability uses the desktop runtime:

```text
.validation/windows-desktop/venv/Scripts/python.exe .validation/windows-desktop/transport-evidence/native_probe.py
```

Use a 30-second foreground tool timeout. The dummy Python child has its own
12-second watchdog and invokes no project main, player, daemon or model. Its
private system-temp stop file is only a test control channel, not the future
application shutdown protocol. The probe checks space/quote/trailing-backslash/
empty/Unicode arguments, Unicode input/output, invisible console, actual child
PID continuity, columns/rows resize, idle polling, EOF/drain, observed status 7,
shutdown once and reader completion. For a venv redirector, retained query-only
process handles verify the actual interpreter's direct parent is the reported
spawn root, creation ordering and identity continuity across operations; both
handles must signal exit with status 7. PID inequality alone is not failure or
proof. Handles are never used to terminate processes. It prints safe JSON
booleans/counters (bounded native error metadata only for this controlled dummy)
and saves `post-batch-native.json` beside the probe. Failed
runs preserve their scratch directory and uncertainty; external timeout is a
failure, never a reason to retry automatically or migrate executors.

Retained history includes an initial missing-module RED, an initial 23-test
GREEN and a failed native run (status 1, 7.227 seconds); that failed evidence is
preserved separately, not overwritten or promoted to success. Interrupted
follow-up changes were not verified before the explicitly authorized additional
batch. The previous additional-batch failure is retained in `prior-additional-native.json`.
Current native proof is whatever the post-batch report actually records,
not the existence of source files, elapsed time or a successful fake suite.

**S8:** this supplies only the persistent transport part of the Windows-first
single-transforming-window foundation. Native window/input/geometry belongs
to the independent host. **S9 remains incomplete:** parent integration must
preserve the same App/player, implement a dedicated acknowledged stop channel,
and independently prove real application shutdown and the final visual.
Risk item 4 remains lifecycle/concurrency/native blocking and buffer behavior;
risk item 2 remains owned-process shutdown. Finite dummy-child success alone
cannot discharge either application-level risk. No commits or delivery approval
are implied by these capability checks.
