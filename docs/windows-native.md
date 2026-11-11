# Run directly on Windows — no WSL

Use native Windows Python 3.13+ and the default **sounddevice** output. The daemon,
TUI and controls run on Windows; neither WSL nor FFplay is needed. These setup
instructions are for your own machine; automated checks use fake audio/model processes.

## Quick path (PowerShell)

With [uv](https://docs.astral.sh/uv/) installed, from a clone:

```powershell
uv sync --frozen
uv run python -m gentle_stream --help
uv run python -m gentle_stream
```

`uv` provisions the project-required Python version. Windows sounddevice wheels
include PortAudio; select a working Windows audio output device. No system-wide
Python changes, administrator privileges or optional samples are required.
Starting without `--mood` makes no model call. Press `b` to detach and keep playing,
run again to attach, or press `q` to stop. From the same account/environment:

```powershell
uv run python -m gentle_stream ctl status
uv run python -m gentle_stream ctl pause
uv run python -m gentle_stream ctl stop
```

Stop only a session you intend to end. These commands never stop another audio app.

For recovery without replacing the daemon, click **Panic** or press **F4** in the TUI
(also available in the command palette). It hard-cuts and reopens the native output,
restarting the audible current track from its beginning with the same seed, musical
settings, playlist, pause and volume; no model call is made. Ordinary native pause
still fades. Panic may click and is not a guaranteed fix for stutter. Reopen failures
are shown as paused with an error; retry Panic, then resume if needed. An older daemon
rejects Panic visibly; stop and start it to load the new code. The CLI `ctl` actions
are unchanged; Panic is exposed through the TUI and `PlayerClient.panic()`.

## Mood interpretation

Configure Gentle Shell and its provider separately. A native `.exe` launcher works
through `--gentle-shell PATH`. For a JavaScript installation, put **node.exe** on
your session's PATH and point directly to its trusted `gentle-shell.mjs` entry point:

```powershell
uv run python -m gentle_stream --gentle-shell 'C:\path\to\gentle-shell.mjs'
```

Use the actual installation path, not the literal placeholder. Batch/`.cmd` shims
(including npm shims) are deliberately rejected: passing a mood through `cmd.exe`
could reinterpret metacharacters. No shell interpolation is used. Missing node,
launch errors and unsupported Job Object assignment fail visibly without a fallback
launch. No credentials are copied or discovered by gentle-stream.

Prompts, model arguments, JSONL parsing and usage accounting are unchanged: one
model call per mood; model listing and playback controls do not call a model.
Cancellation/timeout terminates only that call's owned process tree, including
launcher descendants. It cannot undo tokens already processed by the provider.

## Runtime and security

| Boundary | Native Windows behavior |
| --- | --- |
| Runtime | `%LOCALAPPDATA%\gentle-stream\run`; XDG runtime/cache overrides remain supported |
| Isolation | Runtime directory created atomically with a protected, current-user-only DACL; unsafe existing directories, owners, ACLs and reparse paths are refused, never silently repaired |
| IPC | Per-user/per-runtime named pipe, authenticated with the existing random key; byte-framed JSON only, never pickle |
| Key | New 32-byte `authkey` created exclusively with an explicit current-user owner and protected user-only DACL at creation, using a noninheritable handle; existing keys are validated and reused, never repaired or replaced |
| Singleton | Nonblocking Windows byte-range lock on `player.lock`; PID text is diagnostic, not termination authority |
| Startup | One readiness JSON line over an anonymous stdout pipe; audio opens before ready; a temporary Job Object owns the whole venv-launcher tree until ready, and kills it on timeout/failure; failures are reported to the caller and `player.log` |
| Recovery | OS releases a dead player's lock/pipe; next start reuses the private key, not a stale PID |
| Model ownership | Suspended launch, assignment to a kill-on-close Job Object, then resume through the retained process handle; failure aborts the suspended child |

The account boundary is not a sandbox against another process running as you or
an administrator. Do not place runtime overrides on shared, network or non-ACL
filesystems. Keep each override stable to reconnect to the same player. Runtime
errors fail closed; inspect the reported path rather than relaxing its ACL.
Windows process resume uses the system `NtResumeProcess` API because CPython closes
the primary thread handle; unsupported environments fail before model execution.

Saved prompts/appearance keep the existing XDG paths (default `~/.config/gentle-stream`);
sample caches keep `~/.cache/gentle-stream`. Optional FluidSynth/sample setup is
separate; without it the procedural instruments remain available.

## Native versus WSL

`GENTLE_STREAM_AUDIO_OUTPUT=windows` / `WindowsPCMStream` is the **WSL-only** PCM
interop backend, not the native daemon backend. Leave that variable unset for
native Windows. Native sounddevice retains ordinary fade/pause behavior.
[Windows/WSL](windows-wsl.md) remains available for people who prefer WSL; its
cross-OS model cancellation limitation does not describe native Job Object ownership.

## Device-free verification

[CI](ci.md) runs the native daemon/bridge contracts with fake audio and model
executables. Checks cover startup, attach, singleton, authentication refusal,
stale runtime recovery, controls, stop and owned model-tree cancellation.
They do not prove audible output, provider access, optional samples or a
real terminal rendering. Validate those separately on your intended device.
