# Hyprland windows and compact player

Bare `gentle-stream` still opens the full UI in your current terminal.
Its top-right button or **r** switches full/mini content within the same session,
without resizing the OS window or restarting playback. This is distinct from the
standalone startup compact surface described below; see [interface modes](interface-modes.md).

```sh
gentle-stream --window-mode window --window-size 1000x700
gentle-stream --window-mode floating --window-size 360x180 --terminal kitty
gentle-stream --compact
```

The optional launcher supports **Kitty only**, without new dependencies. It requires
`kitty` and `hyprctl` on PATH and a running Hyprland session. Dimensions are pixels,
`WIDTHxHEIGHT`, with each positive integer bounded to 16384. Size and terminal
options require `--window-mode`; launcher/compact options cannot accompany
`ctl` or `setup-samples`. `--compact` and `--window-mode` are mutually exclusive.

## Supported compositor API

This implementation targets the **Hyprland 0.52.0 classic dispatcher API**:
<https://wiki.hypr.land/0.52.0/Configuring/Dispatchers/>.
It invokes `hyprctl dispatch exec '[float; size W H] kitty ...'` for floating mode,
with arguments serialized using Python `shlex.join` at the shell boundary. Normal
window mode omits `float`, and omitted dimensions leave sizing to the compositor.
It does not detect or adapt to newer `exec_cmd`/Lua rule APIs. A compositor that
removed the classic API is unsupported: dispatcher errors are shown, not retried
using guessed syntax. No compositor configuration, pinning, or always-on-top rules
are written. Existing user rules may still affect placement.

Floating windows use class/title `gentle-dot`; full windows use `gentle-stream`.
Launcher dispatch happens before audio creation. A successful dispatch means the
compositor accepted the request, not proof the terminal or audio started; child
startup errors appear in the launched terminal. Real placement needs a Hyprland
smoke test (not available on the Windows development host).

## Compact surface

Compact mode is a separate visualizer above **one row**: previous, play/pause,
next, minus, five-cell volume bar, plus. Hover reveals shortcuts and exact volume.
Space toggles playback, `n` advances, `+`/`-` change volume by 5%, and Up/Down by
1%. Previous has no keyboard shortcut; it uses the existing playlist selection
from the player, including wrap behavior, and reports rejection as a notification
if none exists. Errors do not silently claim success. Repeated clicks work.
`b` detaches while music continues; `q` stops it. A remote stop exits the app.

Direct `--compact` works in any terminal and in Textual headless tests, without
Hyprland or Kitty. It applies saved theme/background/accent and visualizer
preferences using the full UI's color semantics, but never saves its forced layout. The spectrum is bounded by available rows; saved visibility and minimum
height cannot hide the compact controls or push them offscreen. A terminal of at
least 20 columns and two rows is recommended. Full UI layout is unchanged.

There are no hidden mood/model/settings actions in compact mode. `--mood` and
`--model` are **incompatible with `--compact` or floating mode** and are rejected
before audio or compositor invocation. Use normal/window mode to interpret a mood.
An explicit `--gentle-shell` is also incompatible with compact/floating mode,
including `--gentle-shell=gentle-shell`: there is no shell invocation to configure.
Omitting that option keeps compact/floating mode valid. These checks happen before
creating audio or invoking the compositor; normal/full-window overrides still work.

The launcher forwards full-mode `--model`, `--mood`, and `--gentle-shell` values as
single `--flag=value` tokens, serialized with `shlex.join`: leading hyphens remain
data for the child parser, and shell metacharacters never become executable syntax.
For example, `--window-mode window --mood=-n` preserves the literal mood `-n`.
Direct parser syntax is unchanged; use `=` for values starting with option-like
hyphens. Compact mode never instantiates a shell client or invokes models. Launch
flags are not forwarded, so the child cannot recursively launch windows.
