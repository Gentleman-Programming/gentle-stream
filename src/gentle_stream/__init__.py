"""gentle-stream: real-time generative music in the terminal (lo-fi by default, plus nine more genres), steered by moods that Gentle Shell interprets."""
from __future__ import annotations

import argparse
import json
import sys

CONTROLS = """controls:
  space    play / pause
  n        next piece with the same mood
  /        describe a mood (feeling, concept, place...), Enter to send, Esc to leave
  m        choose the model Gentle Shell uses
  o        choose the audio output device (the music moves to it without restarting)
  s        save what is playing under a name (prompt and style)
  p        saved prompts: Enter applies one instantly, d deletes it
  + / -    volume up / down by 5%
  ↑ / ↓    volume up / down by 1%
  b        back: leave the app, the music keeps playing
  q        quit and stop the music

background playback:
  the music plays in a background player; gentle-stream attaches to it when it is already playing.
  gentle-stream ctl play|pause|toggle|next|stop|status controls it from any terminal
  (status prints one JSON line; the others exit 1 when nothing is playing).

saved prompts live in $XDG_CONFIG_HOME/gentle-stream/presets.json (default ~/.config/gentle-stream/).

audio output:
  gentle-stream devices lists the output devices (* system default, > chosen output);
  --device NAME|INDEX (or the o key) plays on one of them: an index, a full name, or part of one name.
  The choice is remembered in $XDG_CONFIG_HOME/gentle-stream/audio.json; if that device is gone,
  the music plays on the system default and the status line says so.

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


def devices() -> int:
    from . import audio_devices

    try:
        outputs = audio_devices.output_devices()
    except audio_devices.DeviceError as e:
        print(f"gentle-stream: {e}", file=sys.stderr)
        return 1
    if not outputs:
        print("gentle-stream: no audio output device found", file=sys.stderr)
        return 1
    chosen = audio_devices.load_choice()
    for d in outputs:
        mark = ">" if chosen is not None and d.name.casefold() == chosen.casefold() else " "
        print(f"{mark} {d.index:>3}  {d.name}{' *' if d.default else ''}")
    print("* system default · > chosen output (remembered)")
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
        print(json.dumps({"running": True, "playing": player.playing, "name": style.name, "genre": style.genre,
                          "mood": style.mood}, ensure_ascii=False))
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


def main(argv: list[str] | None = None) -> int:
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
    ap.add_argument("--device", metavar="NAME|INDEX",
                    help="audio output device to play on, remembered for next time (see `gentle-stream devices`)")
    sub = ap.add_subparsers(dest="command", metavar="command")
    sub.add_parser("setup-samples", help="download the FluidR3_GM soundfont (MIT) for sampled instruments, once")
    sub.add_parser("devices", help="list the audio output devices")
    ctl_parser = sub.add_parser("ctl", help="control the music playing in the background")
    ctl_parser.add_argument("action", choices=("play", "pause", "toggle", "next", "stop", "status"))
    args = ap.parse_args(argv)
    if args.command == "ctl":
        return ctl(args.action)
    if args.command == "devices":
        return devices()
    if args.command == "setup-samples":
        from . import samples

        if (sf2 := samples.soundfont_path()) is not None:
            print(f"FluidR3_GM already installed: {sf2} (license: {sf2.parent / samples.LICENSES_NAME})")
            return 0
        return setup_samples()

    from . import audio_devices
    from .app import StreamApp
    from .bridge import GentleShell
    from .client import AudioError, PlayerError, PlayerGone, ensure

    device = None
    if args.device is not None:   # checked before anything starts, so a typo never leaves a player behind
        try:
            device = audio_devices.resolve(args.device, audio_devices.output_devices())
        except audio_devices.DeviceError as e:
            print(f"gentle-stream: {e}", file=sys.stderr)
            return 1
    try:
        player = ensure()   # attach to the music already playing, or start the background player
    except AudioError as e:  # PortAudio missing, no output device, device busy...
        print(f"gentle-stream: cannot open the audio output: {e}", file=sys.stderr)
        return 1
    except PlayerError as e:
        print(f"gentle-stream: {e}", file=sys.stderr)
        return 1
    if device is not None:
        try:
            player.set_device(device.index)
        except (PlayerError, PlayerGone) as e:   # the music keeps playing on the device it was using
            print(f"gentle-stream: {e}", file=sys.stderr)
            player.detach()
            return 1
    StreamApp(player, GentleShell(binary=args.gentle_shell, model=args.model), initial_mood=args.mood).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
