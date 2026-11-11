# Run gentle-stream in WSL with Windows Gentle Shell

Install Ubuntu in WSL and follow the [README prerequisites](../README.md#get-started):
Python 3.13 via `uv`, Linux `libportaudio2`, an audio output and a Windows Gentle
Shell bundle if you want mood interpretation through Windows. No Linux Gentle
Shell installation or credential copying is needed for that optional adapter.

## Connect the Windows Gentle Shell already installed on your device

From **your Ubuntu shell**, in a clone of this repository:

```bash
sudo apt-get update && sudo apt-get install -y libportaudio2
uv sync --locked
uv run gentle-stream --gentle-shell "$(pwd)/scripts/gentle-shell-windows"
```

If your clone is on a Windows drive and the checkout did not retain execute
permission, run `chmod +x scripts/gentle-shell-windows` first. The adapter reads
the Windows profile of the **current device** and looks only for that profile's
`.gentle-shell-bundle/current`, the named version's Gentle Shell entry point and
one unambiguous bundled `runtime/node-*/node.exe`. It does not scan arbitrary
profiles or copy credentials. If discovery fails or multiple Node runtimes match,
it reports an error instead of guessing. Use a separate explicit bundle setting
only for a nonstandard trusted installation (see Adapter details).

To start from a Windows terminal, open your chosen WSL distribution and run the
same Ubuntu commands there; substitute its distro/account names in your own
shortcut if you create one. This repository does not install or edit a global
Windows launcher. Mood requests use the Windows installation's configured
provider and can incur normal model usage. Starting without a mood makes no
model request.

## Optional native Windows audio

PortAudio/sounddevice remains the default. To bypass WSLg/PulseAudio for playback,
select `windows` when starting a **new** player. The music still generates live;
FFplay receives the same live instrumental stereo PCM.
No prerecorded track or model call is used by the output backend.

From Ubuntu, when **starting a new player** (replace the two executable paths
with the paths installed on that device):

```bash
GENTLE_STREAM_AUDIO_OUTPUT=windows \
GENTLE_STREAM_WINDOWS_PYTHON='/mnt/c/path/to/python.exe' \
GENTLE_STREAM_WINDOWS_FFPLAY='/mnt/c/path/to/ffplay.exe' \
uv run gentle-stream --gentle-shell "$(pwd)/scripts/gentle-shell-windows"
```

Do not stop an existing player just to change a UI preference or to try this
optional output. Switching an already-running player's audio backend requires a
separately planned normal shutdown and restart; an attempted conflicting attach
returns an error rather than replacing the session.

Both executables must be **Windows** programs, not Linux Python/FFplay. No packages
are installed automatically. Native Python uses only its standard library;
`python.exe` is discovered on PATH (not the Microsoft Store alias), or selected
through the explicit variable above. FFplay is discovered through `ffplay.exe`
on PATH or its explicit variable. WSL interop must be enabled.

**Pause cuts the owned output queue before acknowledging success.** The Windows
supervisor terminates and waits for its own FFplay child, discarding that child's
queued PCM. While paused, the stream makes no callbacks or PCM submissions.
Play/toggle resumes with a fresh owned FFplay child and the next retained Player
samples, preserving volume, position and playlist selection. It does not restart
the music daemon, clear the playlist, replay discarded output, change system
volume, or control other audio applications. Resume may take time to open the
native device again; a hard output cut can click. The default sounddevice path
retains its existing short gain fade.

This is an **owned-process stop boundary, not zero speaker latency**: Windows,
device and hardware buffers may still have an audible tail. Player position and
`now` describe callback-consumed PCM, not a measurement at the speakers. Up to an
in-flight callback block can have been consumed before the cut and is not rewound.
Device-free tests prove the owned child stopped before acknowledgment, including
under pipe backpressure; they do not measure acoustic latency.

The private transport uses bounded frames (at most 8192 PCM bytes), one outstanding
PCM acknowledgment, and pause/resume acknowledgments. Each pipe write has a
two-second deadline and each acknowledgment a three-second deadline. Invalid or
truncated messages and output failures fail visibly through the control call and
`ctl status`; no silent fallback or automatic recovery is performed. When the
native supervisor exits, `audio_output_error` includes its exit status and up to
the last 512 bytes of native stderr (including FFplay errors, when emitted).
If a control error was reported earlier, the final diagnostic replaces its old
stderr excerpt with the latest tail rather than hiding a later native failure.
If FFplay has already exited, the native host reports its exit status too. An empty
stderr or a zero exit status does not prove speaker output; pause/resume ACKs do
not confirm that FFplay has opened the device. All process operations run outside
the audio callback. EOF/failure still cleans up
the owned native child, using its native Popen handle rather than a Linux PID,
global process search or taskkill. The protocol accepts no paths or commands.

`gentle-stream ctl status` adds `audio_output: "windows"` and
`audio_output_error` for this mode. `output_underflows` is **null**, because this
transport cannot measure Windows device underflows. `underruns` still measures
the application's sample queue, not speaker quality. `backend` in daemon state
continues to describe procedural/sampled synthesis, not the output device.

To return to the normal output, stop the player and launch with the output
variable unset (or set it to `sounddevice`). Selecting a different output while
an existing player runs returns an error instead of stopping/replacing it.
A clean pre-rendered Windows test alone does not prove a WSLg root cause or
real-time stability; verify long playback and pause/resume on your own device.

## Adapter details

- Requires WSL Windows executable interop and Python 3 on Ubuntu's PATH.
- Discovers the active Windows user's profile through the current WSL interop
  session (`cmd.exe /u /d /c` with a constant environment query), converts it
  with `wslpath`, and reads only its `.gentle-shell-bundle/current` version pointer.
  For a nonstandard trusted bundle location, set
  `GENTLE_STREAM_WINDOWS_SHELL_BUNDLE` to its **absolute WSL path**; it must still
  contain the same pointer, versioned entry point and bundled runtime.
- Selects exactly one existing `runtime/node-*/node.exe` inside that bundle; a
  missing or ambiguous executable is rejected. The bundled executable receives
  the selected version's `node_modules/gentle-pi/bin/gentle-shell.mjs` as a
  Windows path, converted by WSL without string interpolation.
- Forwards all arguments (including `--`), standard streams, and exit status
  directly. `cmd.exe` is used only for the constant local profile query, never
  to interpolate arguments or run a mood. No `eval` or npm shell shim is used.
- Does not read or copy credentials, edit profiles, or replace global launchers.
- No Windows username, drive letter or Node version is compiled into the adapter.
  These paths are resolved separately on each device. This only supports Windows
  Gentle Shell bundles with the documented `current`/`runtime` layout.

**Cancellation limitation:** killing the Linux process/process group does not
guarantee that Windows descendant processes stop. A cancelled or timed-out mood
request may continue running on Windows; cancellation is not a billing guarantee.

## Verify without a model call

From Ubuntu, list models through the adapter:

```bash
./scripts/gentle-shell-windows -- --no-mcp --no-skill-registry --list-models
```

The isolated stdlib test uses a fake Node, not the Windows installation:

```bash
uv run pytest tests/test_windows_shell_adapter.py -q
```
