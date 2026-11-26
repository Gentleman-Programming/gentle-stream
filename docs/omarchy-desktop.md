# Omarchy desktop preview

The preview opens an app-owned GTK 3 window with an embedded VTE terminal for
Gentle Stream's controls. It does not open your terminal emulator or start,
attach to, or stop a background CLI player. Closing the window, `q`, or `b`
stops the preview's own music. A regular CLI player can keep running separately.

## Install from the preview checkout

Install these prerequisites with Arch's package manager if needed:
`gtk3 vte3 python-gobject portaudio uv`. Arch's official `vte3` package provides
the `Vte-2.91` typelib. The window uses `/usr/bin/python3` for GTK's system
bindings. Separately, make Python 3.13 available to `uv` (for example, run
`uv python install 3.13` if you do not already have it).

From the root of the checkout, run:

```sh
./scripts/install-omarchy-app.sh
```

The installer does not run `sudo`, install system packages, or automatically
download Python. It may download locked project dependencies. It installs a
separate Python environment and the rose-wave icon under
`$XDG_DATA_HOME/gentle-stream/omarchy` (default `~/.local/share/gentle-stream/omarchy`).
It registers **Gentle Stream (Preview)** under `$XDG_DATA_HOME/applications`.
Launch it from your app menu. Re-running the installer updates the same entry;
close any running preview window before updating. The installed launcher does
not need the checkout, an activated environment, or `uv` at launch.

The preview starts with local music. It does not make a model request until you
submit a mood. To use moods, make `gentle-shell` available on the menu session's
`PATH` with its usual model configuration. Local playback does not require it.

## Verification limits

Automated checks use fake GTK/VTE, process, audio and installer boundaries.
Native launch, window-manager icon behavior, and playback on a real Omarchy
session are not yet verified. To smoke-test on Omarchy, open the menu entry,
confirm music starts without a model request, then close the window while it is
opening and again during playback. Check that the preview's own audio stops and
any separately running CLI session keeps playing.
