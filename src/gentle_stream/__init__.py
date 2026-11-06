"""gentle-stream: real-time generative music in the terminal (lo-fi by default, plus ten more genres), steered by moods that Gentle Shell interprets; `moment` renders finite pieces (epic, suspense) to WAV."""
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

moments (finite pieces rendered to a WAV file, no audio device needed):
  gentle-stream moment epic|suspense [--duration SECONDS] [--genre NAME] [--mood TEXT] [--seed N] [-o FILE]
  epic: a build, the hit at about two thirds, a final chord ringing out (announcement / trailer music);
  suspense: sustained tension, a short silence, then the reveal. See gentle-stream moment --help.

sampled instruments (optional):
  gentle-stream setup-samples downloads the FluidR3_GM soundfont (MIT, about 120 MB) once into the user cache;
  with FluidSynth installed it replaces the procedural instruments. Add --recorded to also download the
  recorded CC0 / public-domain instruments (about 760 MB, every file listed with its source and license in
  recorded/SOURCES.txt): a recording plays where one exists, the soundfont otherwise. Without --recorded the
  soundfont sounds as before. Nothing is downloaded otherwise."""


def _progress(done: int, total: int | None) -> None:
    mb = done / 1e6
    line = f"  {mb:6.1f} MB" if not total else f"  {100 * done // total:3d}%  {mb:6.1f} / {total / 1e6:.1f} MB"
    print("\r" + line, end="", flush=True)


def setup_samples(with_recorded: bool = False) -> int:
    """Install whatever is missing: the FluidR3_GM soundfont, then (opt-in) the recorded instruments."""
    from . import recorded, samples

    if (sf2 := samples.soundfont_path()) is not None:
        print(f"FluidR3_GM already installed: {sf2} (license: {sf2.parent / samples.LICENSES_NAME})")
    else:
        print(f"downloading FluidR3_GM (MIT, Frank Wen / Toby Smithe) from {samples.DEB_URL}")
        try:
            sf2 = samples.setup_samples(progress=_progress)
        except samples.SetupError as e:
            print()
            print(f"gentle-stream: setup-samples failed: {e}", file=sys.stderr)
            return 1
        print()
        print(f"installed {sf2} (SHA-256 verified; license: {sf2.parent / samples.LICENSES_NAME})")
    if not with_recorded:
        if recorded.installed(samples.cache_dir()) is None:
            print("optional: `gentle-stream setup-samples --recorded` also downloads recorded CC0 instruments "
                  "(about 760 MB)")
    elif (lib := recorded.installed(samples.cache_dir())) is not None:
        print(f"recorded instruments already installed: {lib} (sources: {lib / recorded.SOURCES})")
    else:
        print("downloading the recorded instruments (CC0 / public domain, about 760 MB)")
        try:
            lib = samples.setup_recorded(progress=_progress)
        except samples.SetupError as e:
            print()
            print(f"gentle-stream: setup-samples failed: {e}", file=sys.stderr)
            return 1
        print()
        print(f"installed the recorded instruments in {lib} (SHA-256 verified; CC0 / public domain, "
              f"every file with its source and license in {lib / recorded.SOURCES})")
    if samples.fluidsynth_module() is None:
        print("note: FluidSynth or pyfluidsynth is missing, so the procedural instruments stay in use "
              "(brew install fluid-synth; uv sync --extra samples)")
    return 0


MOMENT_HELP = """kinds:
  epic      announcement / trailer music: a build that rises into a roll, the hit at about two thirds of the
            duration, the climax and a final stinger chord that rings out
  suspense  sustained tension (pedal, ostinato, unresolved harmony), a short silence, then the reveal

The moment is rendered offline (no background player, no audio device) to a 16-bit stereo WAV, exactly
--duration seconds long and peaking at -1 dBFS; the tempo is chosen inside the genre's range so the hit lands on
a downbeat. Without --mood the kind's own style is used (epic: minor, ~120 bpm, bright; suspense: minor, slow,
dark); --mood is interpreted by Gentle Shell like in the app (--model and --gentle-shell go before `moment`), then
--genre, when given, wins. With the sampled instruments installed (setup-samples) they are used.

examples:
  gentle-stream moment epic
  gentle-stream moment epic --duration 15 --genre synthwave -o launch.wav
  gentle-stream moment suspense --duration 20 --mood "a door creaks open" --seed 7"""


def _duration(text: str) -> float:
    from .moments import MAX_SECONDS, MIN_SECONDS, check_seconds

    try:
        return check_seconds(float(text))
    except ValueError:   # not a number, or out of range
        raise argparse.ArgumentTypeError(f"duration must be between {MIN_SECONDS:g} and {MAX_SECONDS:g} "
                                         "seconds") from None


def _genre(text: str) -> str:
    from .genres import NAMES

    if text not in NAMES:
        raise argparse.ArgumentTypeError(f"unknown genre {text!r} (choose from {', '.join(NAMES)})")
    return text


def moment(args) -> int:
    import secrets
    from dataclasses import replace
    from pathlib import Path

    from . import moments

    style = moments.default_style(args.kind)
    if args.mood:
        from .bridge import GentleShell, InterpretError

        try:
            style = GentleShell(binary=args.gentle_shell, model=args.model).interpret(args.mood, style)
        except InterpretError as e:
            print(f"gentle-stream: cannot interpret the mood: {e}", file=sys.stderr)
            return 1
    if args.genre:
        style = replace(style, genre=args.genre)
    seed = secrets.randbelow(1_000_000) if args.seed is None else args.seed
    path = Path(args.output or f"{args.kind}-{seed}.wav").resolve()
    m = moments.render(args.kind, args.duration, style, seed)
    try:
        moments.write_wav(path, m)
    except OSError as e:
        print(f"gentle-stream: cannot write {path}: {e.strerror or e}", file=sys.stderr)
        return 1
    print(f"wrote {path} · {args.duration:g} s · {m.style.genre} · seed {seed} · {m.backend} · "
          f"{m.plan.bpm:.1f} bpm · hit at {m.plan.hit_seconds:.2f} s")
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
    setup = sub.add_parser("setup-samples", help="download the FluidR3_GM soundfont (MIT) once; "
                                                 "--recorded adds recorded CC0 instruments")
    setup.add_argument("--recorded", action="store_true",
                       help="also download the recorded CC0 / public-domain instruments (about 760 MB)")
    sub.add_parser("devices", help="list the audio output devices")
    ctl_parser = sub.add_parser("ctl", help="control the music playing in the background")
    ctl_parser.add_argument("action", choices=("play", "pause", "toggle", "next", "stop", "status"))
    mom = sub.add_parser("moment", help="render a finite piece for a moment (epic, suspense) to a WAV file",
                         description="Render music for a moment: a finite piece with an arc and an exact duration.",
                         epilog=MOMENT_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    mom.add_argument("kind", choices=("epic", "suspense"), help="epic (announcement) or suspense (reveal)")
    mom.add_argument("--duration", type=_duration, default=30.0, metavar="SECONDS",
                     help="length in seconds, 5-300 (default: %(default)g)")
    mom.add_argument("--genre", type=_genre, metavar="NAME",
                     help="any genre of the stream (default: orchestral, or the mood's genre with --mood)")
    mom.add_argument("--mood", metavar="TEXT", help="describe the moment; Gentle Shell turns it into the style")
    mom.add_argument("--seed", type=int, metavar="N", help="the same seed renders the same piece (default: random)")
    mom.add_argument("-o", dest="output", metavar="FILE", help="output WAV (default: <kind>-<seed>.wav here)")
    args = ap.parse_args(argv)
    if args.command == "moment":
        return moment(args)
    if args.command == "ctl":
        return ctl(args.action)
    if args.command == "devices":
        return devices()
    if args.command == "setup-samples":
        return setup_samples(with_recorded=args.recorded)

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
