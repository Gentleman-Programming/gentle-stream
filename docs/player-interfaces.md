# Player interfaces

## Terminal quick start

Run `gentle-stream` after installation, or `uv run gentle-stream` from a checkout.
See [platform setup](platform-support.md) for Linux, macOS, native Windows and WSL2.
The native Windows and macOS paths are terminal paths, not native desktop apps.

| To | Do this |
| --- | --- |
| Play or pause | Press `Space`, or use the centered transport. |
| Describe a mood | Press `/`, type a description, then press `Enter`. |
| Choose a mood model | Press `m`, or choose **Choose model** in the command palette (`Ctrl+P`). |
| Save the audible song | Press `s`, or choose **Save current prompt** in the command palette. Save to Saved prompts or a playlist. |
| Recall a saved style | Press `p` for **My playlist** and **Default playlist**. See [playlist controls](playlist-controls.md) for Previous and repeat. |
| Change appearance | Press `F2` for [Settings](appearance.md). The preview is temporary; **Save and Close** applies changes. |
| Leave the terminal player | Press `b` to leave music playing in the background, or `q` to stop it and quit. |

See the [terminal screenshot](../README.md#terminal-preview) and [all controls](../README.md#controls).
On sounddevice, `o` opens this player's output picker; it does not change the OS
default. The picker is unavailable with the WSL Windows audio bridge.

## Omarchy desktop preview

The [Omarchy preview](omarchy-desktop.md) embeds the TUI in its own GTK window
and owns its player. Closing the window, `q`, or `b` stops the preview's music;
a separate CLI player is unaffected. A real Omarchy launch and audio smoke test
are still pending. This branch does not ship a native Windows or macOS desktop app.

## Packaged artwork

The wheel includes [portable launcher artwork](../README.md#launcher-artwork).
The assets do not install a desktop app or change a terminal icon.
