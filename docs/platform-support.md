# Install and verify the terminal player by platform

Gentle Stream runs in a terminal on Linux, macOS, Ubuntu on WSL2, and native Windows. The Windows desktop shell is a separate feature; the terminal support below does not install a graphical app on Linux or macOS. You can play the default instrumental without Gentle Shell or a model request.

## Choose your setup

| Platform | Audio prerequisite | What the test matrix covers |
| --- | --- | --- |
| Ubuntu 24.04 x64 | `sudo apt-get install libportaudio2` and a working output device | Installed console and full device-free correctness suite |
| macOS ARM64 | `brew install portaudio` and a working Core Audio output | Installed console and full device-free correctness suite on `macos-15` |
| Ubuntu 24.04 on WSL2 x64 | `sudo apt-get install libportaudio2` and configured WSLg audio; alternatively use the [Windows FFplay bridge](windows-wsl.md#optional-native-windows-audio) | Installed console and full device-free correctness suite in actual WSL2 |
| Native Windows x64 | The Windows `sounddevice` wheel includes PortAudio; follow [Native Windows setup](windows-native.md) | Focused device-free source contracts, not an installed-console job |

These are the configured CI targets, not a claim that every revision or audio device passed. Linux ARM64, Intel macOS and WSL ARM64 are not verified by this matrix. On Linux, the distro's package name and audio stack may differ. In WSL, keep the checkout under your Linux home rather than `/mnt/c`; a POSIX runtime directory is required for Unix sockets. Use `wsl --list --verbose` from Windows to check that the distro is version 2.

From the checkout, with `uv` available:

```sh
uv sync --locked --python 3.13
uv run --locked gentle-stream --help
```

For WSL, the optional [Windows Gentle Shell adapter](windows-wsl.md#connect-the-windows-gentle-shell-already-installed-on-your-device) is independent of the audio backend. Start with the default audio path, then test FFplay separately only if you use it.

## Check the terminal and actual sound

Use a test account, VM, or isolated runtime and preference directories. Do not attach to or stop an existing listening session to run this checklist.

1. In the test environment, run `uv run --locked gentle-stream ctl --json status` before launching. It should return `{"ok": true, "running": false}`. If it reports an existing session, stop the *test* and isolate it rather than stopping that player.
2. Run `uv run --locked gentle-stream` in an interactive terminal, with no `--mood` or automatic model request. Confirm that the UI draws and you hear the default instrumental on the selected output device. The moving spectrum or a `playing` status is not a substitute for hearing it.
3. With `Space`, pause and resume. Listen for the output to stop and resume, then try the volume keys. Press `b` to detach and reopen the terminal player to check that it attaches to the same test playback. Quit the test player with `q` and check that `ctl --json status` is back to `running:false`.
4. For WSL, record WSLg/PortAudio and optional Windows FFplay as **separate** tests. An audio failure on one path does not validate or disprove the other.

When reporting a result, include the exact checked-out Git SHA, OS/distro, architecture, Python version, audio backend, and which steps passed or failed. Do not include account details, credentials, or provider output. See [device-free CI](ci.md) for scope and limitations.
