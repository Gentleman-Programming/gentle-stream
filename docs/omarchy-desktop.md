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

## Test both interfaces with Gentle Shell

Use the same preview checkout for both tests. Record its commit with
`git rev-parse --short HEAD`. The terminal player opens from `/gentle:music`;
the GTK app opens separately and uses Gentle Shell only to interpret moods.
There is no GTK launch command in Gentle Shell yet ([issue #3581][shell-desktop]).

### Terminal: `/gentle:music`

From the checkout root, in the interactive shell where `gentle-shell` already
works, build the candidate environment and start Gentle Shell with that copy
of Stream first on `PATH`. The subshell isolates the player socket without
hiding your existing Gentle Shell model configuration. `uv` may download locked
project dependencies, but the command below does not install a global tool or
a new Python interpreter.

```sh
(
  set -e
  command -v gentle-shell >/dev/null
  preview=$(mktemp -d /tmp/gs-omarchy.XXXXXX)
  mkdir -m 700 "$preview/run"
  UV_PROJECT_ENVIRONMENT="$preview/venv" \
    uv sync --frozen --no-dev --python 3.13 --no-python-downloads
  export PATH="$preview/venv/bin:$PATH"
  export XDG_RUNTIME_DIR="$preview/run"
  test "$(command -v gentle-stream)" = "$preview/venv/bin/gentle-stream"
  status=$(gentle-stream ctl status --json)
  printf '%s\n' "$status"
  "$preview/venv/bin/python" -c 'import json, sys; sys.exit(json.loads(sys.argv[1]).get("running") is not False)' "$status"
  gentle-shell
)
```

The initial status should show `"running": false`. In Gentle Shell, run
`/gentle:music`, then submit a mood such as `soft rain, lo-fi piano`. Verify
that the music changes, the new toolbar fits when resizing, and Playlists and
Settings open and close. Quit the player with `q`; `b` leaves its daemon playing.
The temporary environment is retained for inspection.

### GTK app: menu launch and the same installed launcher from a shell

1. Run `./scripts/install-omarchy-app.sh` from the same checkout. Open **Gentle
   Stream (Preview)** from the menu and confirm music starts without a prompt.
2. Submit the same mood. If the menu reports a `mise` error, record it as a
   **menu environment failure**, then close the preview. From the interactive
   terminal where `gentle-shell` works, run the installed launcher directly:

   ```sh
   data=${XDG_DATA_HOME:-"$HOME/.local/share"}
   case "$data" in /*) ;; *) data="$HOME/.local/share" ;; esac
   /usr/bin/python3 -I -B "$data/gentle-stream/omarchy/linux_desktop.py"
   ```

   This opens the same GTK app and uses its installed Python environment, but
   inherits the interactive shell's `PATH`. Submit the mood again and report
   menu and terminal-launch results separately. Close it before retrying; do not
   edit `.desktop` files, load shell startup scripts, or rewrite your `PATH`.
3. Close GTK with `Super+W` during startup and again during playback. Its audio
   should stop; a separate terminal player, if one is running, should continue.

## Verification limits

Automated checks use fake GTK/VTE, process, audio and installer boundaries. The
previous preview commit `e571d6e` was tested on Omarchy for menu launch, local
playback and close. Repeat the checks above on this candidate; the previous
smoke does not verify the updated UI or graphical mood handling.

[shell-desktop]: https://github.com/Gentleman-Programming/gentle-shell/issues/3581
