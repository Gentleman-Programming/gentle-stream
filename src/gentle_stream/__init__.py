"""gentle-stream: real-time generative music in the terminal (lo-fi by default, plus nine more genres), steered by moods that Gentle Shell interprets."""
from __future__ import annotations

import argparse
import json
import sys

CONTROLS = """controls:
  space    play / pause
  r        full / miniplayer in this session (also the top-right ▾/▴ button)
           keeps the same player and music; not the separate startup --compact mode
  n        next piece with the same mood (next song during continuous playback)
  F4       Panic: immediately cut output, reopen it and restart the current track from the beginning
           keeps musical settings, playlist, pause and volume; no AI call. A hard cut may click.
           recovery does not guarantee to fix stutter; reopen failures are shown, not Playing.
  /        describe a mood (feeling, concept, place...), Enter to send, Esc to leave
  F2       Settings: read-only full-player preview beside appearance and model settings
           always available, even with hidden controls/input/footer; also in the command palette
  m        choose the model Gentle Shell uses directly
  s        save what is playing: Saved prompts, new playlist, or existing playlist
  p        Playlist (also clickable): My playlist / Default playlist
           My playlist opens groups, then songs in the same panel; Backspace goes back
           Left/Right switch playlists; Up/Down highlight entries
           click or Enter applies; d deletes only saved songs; Esc closes
  c        continuous playlist from the selected song; toggle off without restarting audio
           picker: c or Repeat from selection starts the full list at the highlighted song
  command strip: Panic/save/repeat/mood/playlists left; previous/play-pause/next centered;
                 chosen model and volume right. Tab focuses buttons; side groups scroll independently.
  ⏮ button: previous playlist song (wraps), repeat on or off; preserves pause and volume.
            Select a song in Playlist first. No new key; this does not restart the current song.
  gear opens Settings and shows the chosen model (or honest Gentle Shell default).
  Settings: valid edits preview only in the miniature; Save persists and applies.
  Preview validates the pane; Defaults resets the draft; Cancel discards unsaved edits.
  UI-only preferences live in appearance.json; visualizer changes never change audio parameters.
  / temporarily reveals hidden mood input in full mode without saving visibility.
  Miniplayer keeps spectrum, transport and volume visible; F2 always opens Settings.
  Fonts belong to the terminal.
  See docs/appearance.md for settings ranges and storage recovery.
  + / -    volume up / down by 5%
  ↑ / ↓    volume up / down by 1%
  b        back: leave the app, the music keeps playing
  q        quit and stop the music

background playback:
  the music plays in a background player; gentle-stream attaches to it when it is already playing.
  gentle-stream ctl play|pause|toggle|next|stop|status controls it from any terminal
  (status prints one JSON line with underruns, output_underflows and buffered_seconds;
   diagnostics are null with older players; the others exit 1 when nothing is playing).

native Windows desktop (optional):
  gentle-stream --desktop uses one persistent terminal/player in a frameless full/mini window.
  Install gentle-stream[desktop] and Edge WebView2 Runtime; offline terminal assets are bundled.
  Close (or b/q) stops this session's music. Settings drafts do not change the window theme.
  --desktop cannot be combined with --compact, window launcher options or subcommands.
  See docs/windows-desktop-host.md; actual Windows behavior approval remains pending.

native Windows (no WSL):
  run with Python 3.13+ and the default sounddevice output; no FFplay is needed.
  native executable or direct .mjs Gentle Shell launcher; see docs/windows-native.md.

optional Windows audio bridge (WSL only):
  GENTLE_STREAM_AUDIO_OUTPUT=windows gentle-stream streams live PCM to Windows FFplay.
  sounddevice remains the default. Stop an existing player before selecting a different output.
  See docs/windows-wsl.md for Windows Python/FFplay paths and diagnostics.

saved prompts live in $XDG_CONFIG_HOME/gentle-stream/presets.json (default ~/.config/gentle-stream/).
named playlists live separately in the same directory's playlist-library.json; no migration.
continuous playback wraps in alphabetical display order, stays in the background after b,
and uses saved styles without model calls. A normal song or mood selection exits continuous mode.

sampled instruments (optional):
  gentle-stream setup-samples downloads the FluidR3_GM soundfont (MIT) once into the user cache;
  with FluidSynth installed it replaces the procedural instruments. Nothing is downloaded otherwise."""


def setup_samples() -> int:
    from . import samples

    print(f"downloading FluidR3_GM (MIT, Frank Wen / Toby Smithe) from {samples.DEB_URL}")

    def progress(done: int, total: int | None) -> None:
        mb = done / 1e6
        line = f"  {mb:6.1f} MB" if not total else f"  {100 * done // total:3d}%  {mb:6.1f} / {total / 1e6:.1f} MB"
        print("\r" + line, end="", flush=True)

    try:
        sf2 = samples.setup_samples(progress=progress)
    except samples.SetupError as e:
        print()
        print(f"gentle-stream: setup-samples failed: {e}", file=sys.stderr)
        return 1
    print()
    print(f"installed {sf2} (SHA-256 verified; license: {sf2.parent / samples.LICENSES_NAME})")
    if samples.fluidsynth_module() is None:
        print("note: FluidSynth or pyfluidsynth is missing, so the procedural instruments stay in use "
              "(brew install fluid-synth; uv sync --extra samples)")
    return 0


def ctl(action: str) -> int:
    from .client import NoPlayer, PlayerError, PlayerGone, connect

    try:
        player = connect()
    except NoPlayer:
        if action == "status":
            print(json.dumps({"running": False}))
            return 0
        print("gentle-stream: nothing is playing", file=sys.stderr)
        return 1
    except PlayerError as e:
        print(f"gentle-stream: {e}", file=sys.stderr)
        return 1
    if action == "status":
        style = player.now.style
        status = {"running": True, "playing": player.playing, "name": style.name, "genre": style.genre,
                  "mood": style.mood, "underruns": player.underruns,
                  "output_underflows": player.output_underflows, "buffered_seconds": player.buffered_seconds}
        if getattr(player, 'audio_output', None) == 'windows':
            status.update(audio_output='windows', audio_output_error=player.audio_output_error)
        print(json.dumps(status, ensure_ascii=False))
    elif action == "stop":
        player.close()
        return 0
    else:
        try:
            player.request(action)
        except PlayerGone:
            print("gentle-stream: nothing is playing", file=sys.stderr)
            return 1
    player.detach()
    return 0


def main(argv: list[str] | None = None, *, desktop_control=None) -> int:
    # Windows consoles use UTF-8, but redirected Python output otherwise uses
    # the legacy ANSI code page, which cannot represent the documented controls.
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["daemon"]:   # the background player, started by the TUI; not listed in --help
        from .daemon import main as daemon_main

        return daemon_main(argv[1:])
    ap = argparse.ArgumentParser(prog="gentle-stream", description=__doc__, epilog=CONTROLS,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="model Gentle Shell uses to interpret moods, as provider/id "
                                    "(default: Gentle Shell's default model)")
    ap.add_argument("--mood", help="mood to interpret as soon as the app starts")
    ap.add_argument("--gentle-shell", metavar="PATH",
                    help="gentle-shell executable (default: gentle-shell); explicit overrides are incompatible "
                         "with compact/floating mode")
    ap.add_argument('--desktop', action='store_true',
                    help='optional native Windows desktop window; full/mini transforms the same player')
    ap.add_argument('--compact', action='store_true',
                    help='minimal visualizer and transport only; incompatible with --mood/--model/explicit --gentle-shell')
    ap.add_argument('--window-mode', choices=('window', 'floating'),
                    help='launch dedicated Kitty on Hyprland classic dispatch exec (0.52); floating uses compact UI')
    ap.add_argument('--window-size', metavar='WIDTHxHEIGHT',
                    help='launcher size in pixels, each 1..16384; requires --window-mode')
    ap.add_argument('--terminal', choices=('kitty',),
                    help='launcher terminal (default kitty); requires --window-mode')
    sub = ap.add_subparsers(dest="command", metavar="command")
    sub.add_parser("setup-samples", help="download the FluidR3_GM soundfont (MIT) for sampled instruments, once")
    ctl_parser = sub.add_parser("ctl", help="control the music playing in the background")
    ctl_parser.add_argument("action", choices=("play", "pause", "toggle", "next", "stop", "status"))
    args = ap.parse_args(argv)
    launcher_options = args.window_mode or args.window_size is not None or args.terminal is not None
    if args.desktop and (args.compact or launcher_options):
        ap.error('--desktop is incompatible with --compact and --window-mode')
    if args.desktop and args.command:
        ap.error('--desktop cannot be combined with subcommands')
    if args.desktop:
        if sys.platform != 'win32':
            print('gentle-stream: --desktop requires native Windows; use ordinary gentle-stream on this platform',
                  file=sys.stderr)
            return 1
        from .desktop_session import launch
        forwarded = [f'{flag}={value}' for flag, value in
                     (('--mood', args.mood), ('--model', args.model), ('--gentle-shell', args.gentle_shell))
                     if value is not None]
        return launch(forwarded)
    if args.command and (args.compact or launcher_options):
        ap.error('compact/window options cannot be combined with subcommands')
    if not args.window_mode and (args.window_size is not None or args.terminal is not None):
        ap.error('--window-size and --terminal require --window-mode')
    if args.compact and args.window_mode:
        ap.error('--compact is incompatible with --window-mode; floating selects compact automatically')
    if (args.compact or args.window_mode == 'floating') and (args.mood is not None or args.model is not None):
        ap.error('--mood/--model are incompatible with compact/floating mode (no mood interpretation); use window mode')
    # Let argparse distinguish an explicit override (even its usual default
    # value) from absence, without scanning values that merely resemble flags.
    shell_explicit = args.gentle_shell is not None
    if (args.compact or args.window_mode == 'floating') and shell_explicit:
        ap.error('explicit --gentle-shell is incompatible with compact/floating mode (no shell invocation); use window mode')
    if not shell_explicit:
        args.gentle_shell = 'gentle-shell'
    size = None
    if args.window_size is not None:
        from .window_launcher import parse_window_size

        try:
            size = parse_window_size(args.window_size)
        except ValueError as error:
            ap.error(str(error))
    if args.window_mode:
        from .window_launcher import LaunchError, launch_window

        forwarded = []
        for flag, value in (('--model', args.model), ('--mood', args.mood), ('--gentle-shell', args.gentle_shell)):
            if value is not None and (flag != '--gentle-shell' or shell_explicit):
                # Preserve leading hyphens as data in the child's argparse too.
                forwarded.append(f'{flag}={value}')
        try:
            launch_window(args.window_mode, size, args.terminal or 'kitty', forwarded)
        except LaunchError as error:
            print(f'gentle-stream: {error}', file=sys.stderr)
            return 1
        return 0
    if args.command == "ctl":
        return ctl(args.action)
    if args.command == "setup-samples":
        from . import samples

        if (sf2 := samples.soundfont_path()) is not None:
            print(f"FluidR3_GM already installed: {sf2} (license: {sf2.parent / samples.LICENSES_NAME})")
            return 0
        return setup_samples()

    from .app import StreamApp
    from .bridge import GentleShell
    from .client import AudioError, PlayerError, ensure

    if desktop_control is not None and desktop_control.stopping():
        return 0
    try:
        player = ensure()   # attach to the music already playing, or start the background player
    except AudioError as e:  # PortAudio missing, no output device, device busy...
        print(f"gentle-stream: cannot open the audio output: {e}", file=sys.stderr)
        return 1
    except PlayerError as e:
        print(f"gentle-stream: {e}", file=sys.stderr)
        return 1
    if args.compact:
        from .compact_app import CompactStreamApp

        CompactStreamApp(player).run()
    else:
        app = StreamApp(player, GentleShell(binary=args.gentle_shell, model=args.model), initial_mood=args.mood)
        app.desktop_control = desktop_control
        app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
