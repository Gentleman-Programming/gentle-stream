"""gentle-stream: real-time generative lo-fi in the terminal, steered by moods that Gentle Shell interprets."""
from __future__ import annotations

import argparse
import sys

CONTROLS = """controls:
  space    play / pause
  n        next piece with the same mood
  /        describe a mood (feeling, concept, place...), Enter to send, Esc to leave
  m        choose the model Gentle Shell uses
  + / -    volume
  q        quit"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gentle-stream", description=__doc__, epilog=CONTROLS,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="model Gentle Shell uses to interpret moods, as provider/id "
                                    "(default: Gentle Shell's default model)")
    ap.add_argument("--mood", help="mood to interpret as soon as the app starts")
    ap.add_argument("--gentle-shell", default="gentle-shell", metavar="PATH",
                    help="gentle-shell executable (default: %(default)s)")
    args = ap.parse_args(argv)

    from .app import StreamApp
    from .bridge import GentleShell
    from .engine import Engine
    from .player import Player

    player = Player(Engine())
    try:
        player.start()
    except Exception as e:  # PortAudio missing, no output device, device busy...
        player.close()
        print(f"gentle-stream: cannot open the audio output: {e}", file=sys.stderr)
        return 1
    StreamApp(player, GentleShell(binary=args.gentle_shell, model=args.model), initial_mood=args.mood).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
