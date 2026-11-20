"""gentle-stream: real-time generative music in the terminal (lo-fi by default, plus nine more genres), steered by moods that Gentle Shell interprets."""
from __future__ import annotations

import argparse
import json
import sys

CONTROLS = """controls:
  space    play / pause
  n        next piece with the same mood (next song during continuous playback)
  F4       Panic: immediately cut output, reopen it and restart the current track from the beginning
           keeps musical settings, playlist, pause and volume; no AI call. A hard cut may click.
           recovery does not guarantee to fix stutter; reopen failures are shown, not Playing.
  /        describe a mood (feeling, concept, place...), Enter to send, Esc to leave
  F2       Settings: live appearance, visibility, and explicit mood model selection
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
  Settings: Preview is temporary; Save persists; Defaults previews defaults; Cancel restores last save.
  UI-only preferences live in appearance.json; visualizer changes never change audio parameters.
  / temporarily reveals hidden mood input without saving visibility. Fonts belong to the terminal.
  See docs/appearance.md for settings ranges and storage recovery.
  + / -    volume up / down by 5%
  ↑ / ↓    volume up / down by 1%
  b        back: leave the app, the music keeps playing
  q        quit and stop the music

background playback:
  the music plays in a background player; gentle-stream attaches to it when it is already playing.
  gentle-stream ctl play|pause|toggle|next|stop|status|start|volume|mood controls it from any terminal.
  status without --json prints one JSON line with underruns, output_underflows and buffered_seconds
  (null with older players). start starts or attaches; other actions exit 1 when no player is running.
  volume takes a whole percent 0-100; mood takes text. Add --json for one stable machine envelope.

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


MOOD_MAX = 200   # Style clamps mood text to 200 characters
_PLAIN_CTL_ACTIONS = ("play", "pause", "toggle", "next", "stop", "status")


def _parse_volume(value: list[str]) -> int | None:
    """The ctl volume percent (a whole number 0-100), or None when the arguments are invalid."""
    if len(value) != 1:
        return None
    try:
        percent = int(value[0])
    except (TypeError, ValueError):
        return None
    return percent if 0 <= percent <= 100 else None


def _snapshot(state: dict, style: dict | None = None) -> dict:
    """One stable machine snapshot: the current parameters (the rendered style, overridable for a queued mood)."""
    style = state["style"] if style is None else style
    return {"ok": True, "running": True, "playing": bool(state["playing"]),
            "volume": round(float(state["volume"]) * 100), "name": style["name"], "genre": style["genre"],
            "mood": style["mood"]}


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def ctl(action: str, as_json: bool = False, value: list[str] | None = None,
        gentle_shell: str = "gentle-shell", model: str | None = None) -> int:
    """Control the background player; with `--json`, print one machine envelope and exit non-zero on error."""
    value = list(value or [])

    def fail(code: str, message: str, legacy: str | None = None) -> int:
        if as_json:
            print(json.dumps(_error(code, message), ensure_ascii=False))
        else:
            print(f"gentle-stream: {legacy or message}", file=sys.stderr)
        return 1

    def ok(snapshot: dict, legacy: dict | None = None) -> int:
        if as_json:
            print(json.dumps(snapshot, ensure_ascii=False))
        elif legacy is not None:
            print(json.dumps(legacy, ensure_ascii=False))
        return 0

    # Local validation: bad volume, mood or extra arguments never reach the player or the model.
    mood = None
    if action == "volume":
        percent = _parse_volume(value)
        if percent is None:
            return fail("invalid_request", "volume must be a whole number from 0 to 100")
    elif action == "mood":
        mood = " ".join(value).strip()
        if not mood:
            return fail("invalid_request", "mood needs some text")
        if len(mood) > MOOD_MAX:
            return fail("invalid_request", f"mood is too long ({MOOD_MAX} characters max)")
    elif value:
        return fail("invalid_request", f"{action} takes no arguments")

    player = None
    try:
        from .client import NoPlayer, PlayerError, PlayerGone, connect, ensure
        player = ensure() if action == "start" else connect()
        if action == "status" and not as_json:
            style = player.now.style
            status = {"running": True, "playing": player.playing, "name": style.name, "genre": style.genre,
                      "mood": style.mood, "underruns": player.underruns,
                      "output_underflows": player.output_underflows, "buffered_seconds": player.buffered_seconds}
            if getattr(player, 'audio_output', None) == 'windows':
                status.update(audio_output='windows', audio_output_error=player.audio_output_error)
            print(json.dumps(status, ensure_ascii=False))
            player.detach()
            return 0
        if action in ("status", "start"):
            state = player.request("state")
            now = state["now"]["style"]
            legacy = {"running": True, "playing": bool(state["playing"]), "name": now["name"],
                      "genre": now["genre"], "mood": now["mood"]}
            player.detach()
            return ok(_snapshot(state), legacy)
        if action == "stop":
            player.close()
            return ok({"ok": True, "running": False})
        if action == "mood":
            from .bridge import GentleShell, InterpretError
            try:
                style = GentleShell(binary=gentle_shell, model=model).interpret(mood, player.style)
            except InterpretError as e:
                player.detach()
                return fail("unavailable", "mood interpretation is unavailable", str(e))
            wanted = style.to_dict()
            state = player.request("set_style", style=wanted)
            player.detach()
            return ok(_snapshot(state, wanted))
        if action == "volume":
            state = player.request("set_volume", volume=percent / 100)
        else:
            state = player.request(action)
        player.detach()
        return ok(_snapshot(state))
    except NoPlayer:
        if action == "status":
            return ok({"ok": True, "running": False}, {"running": False})
        return fail("nothing_playing", "nothing is playing")
    except PlayerGone as e:
        if not as_json and action in _PLAIN_CTL_ACTIONS:
            if player is None:
                return fail("unavailable", "the player is unavailable", str(e))
            if action in ("status", "stop"):
                raise
        return fail("nothing_playing", "nothing is playing")
    except PlayerError as e:
        if not as_json and action in _PLAIN_CTL_ACTIONS and player is not None:
            raise
        return fail("unavailable", "the player is unavailable", str(e))
    except Exception:
        # A malformed answer, a wrong state shape, or anything else unexpected must never leak a
        # traceback, path or secret into the machine envelope; legacy keeps the raw error.
        if as_json:
            if player is not None:
                player.detach()
            return fail("unavailable", "unexpected error from the player")
        raise


def main(argv: list[str] | None = None) -> int:
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
    ap.add_argument("--gentle-shell", default="gentle-shell", metavar="PATH",
                    help="gentle-shell executable (default: %(default)s)")
    sub = ap.add_subparsers(dest="command", metavar="command")
    sub.add_parser("setup-samples", help="download the FluidR3_GM soundfont (MIT) for sampled instruments, once")
    ctl_parser = sub.add_parser("ctl", help="control the music playing in the background")
    ctl_parser.add_argument("action",
                            choices=("play", "pause", "toggle", "next", "stop", "status", "start", "volume", "mood"))
    ctl_parser.add_argument("--json", action="store_true", dest="as_json",
                            help="print one JSON envelope (ok/error) for machines")
    ctl_parser.add_argument("value", nargs="*", metavar="ARG",
                            help="volume: a whole percent 0-100; mood: the text to interpret")
    args = ap.parse_args(argv)
    if args.command == "ctl":
        if not args.as_json and args.action in _PLAIN_CTL_ACTIONS and args.value:
            ap.error("unrecognized arguments: " + " ".join(args.value))
        return ctl(args.action, args.as_json, args.value, args.gentle_shell, args.model)
    if args.command == "setup-samples":
        from . import samples

        if (sf2 := samples.soundfont_path()) is not None:
            print(f"FluidR3_GM already installed: {sf2} (license: {sf2.parent / samples.LICENSES_NAME})")
            return 0
        return setup_samples()

    from .app import StreamApp
    from .bridge import GentleShell
    from .client import AudioError, PlayerError, ensure

    try:
        player = ensure()   # attach to the music already playing, or start the background player
    except AudioError as e:  # PortAudio missing, no output device, device busy...
        print(f"gentle-stream: cannot open the audio output: {e}", file=sys.stderr)
        return 1
    except PlayerError as e:
        print(f"gentle-stream: {e}", file=sys.stderr)
        return 1
    StreamApp(player, GentleShell(binary=args.gentle_shell, model=args.model), initial_mood=args.mood).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
