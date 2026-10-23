# gentle-stream

Real-time generative lo-fi in your terminal. Tell it a feeling, a concept or a place; Gentle Shell turns it into
musical parameters and the music changes at the next bar, without stopping.

The sound comes from the same procedural synthesis used for the Gentleman Programming YouTube videos
(`lofi_mel.py`): felt piano, rhodes, nylon guitar and pads over minor/modal progressions, sub bass, dusty swung
drums, vinyl crackle, a small room and tape wow. Here it is rendered bar by bar while it plays, so it never ends
and never repeats exactly.

The TUI uses the gentlemanprogramming.com palette (a `gentleman` Textual theme): rose and pink on deep plum.

## Requirements

- [uv](https://docs.astral.sh/uv/)
- [Gentle Shell](https://github.com/Gentleman-Programming/gentle-pi) (`gentle-shell` on your `PATH`) with at least
  one signed-in model provider
- An audio output device

## Run

```sh
uv run gentle-stream                       # starts playing right away
uv run gentle-stream --mood "rainy night in a small café"
uv run gentle-stream --model claude-bridge/claude-haiku-5-5
uv tool install .                          # then just: gentle-stream
```

| Flag | Meaning |
|------|---------|
| `--model provider/id` | Model Gentle Shell uses to interpret moods (default: Gentle Shell's default). |
| `--mood TEXT` | Mood to interpret as soon as the app starts. |
| `--gentle-shell PATH` | `gentle-shell` executable to run (default: `gentle-shell`). |

## Controls

| Key | Action |
|-----|--------|
| `space` | Play / pause (smooth fade, no clicks) |
| `n` | Next piece with the same mood: new key, progression, melody and groove |
| `/` | Describe a mood, `Enter` to send, `Esc` to leave the input |
| `m` | Choose the model Gentle Shell uses (listed from `gentle-shell -- --list-models`) |
| `+` / `-` | Volume |
| `q` | Quit |

## How it works

- **Engine** (`engine.py`): schedules each bar's notes into a buffer where their tails ring into later bars, then
  passes the finished bar through a master chain (vinyl, room, tape wow, tone) whose filters and delay lines keep
  their state, so bars join seamlessly. It renders about 50 times faster than real time.
- **Player** (`player.py`): a producer thread keeps about one second of audio ahead; the audio callback only
  copies samples. `next` renders the new piece's first bar immediately and crossfades it over the old one.
  The screen shows the bar you are hearing, not the one being rendered.
- **Mood interpretation** (`bridge.py`): runs
  `gentle-shell -- -p --no-session -nt -ns -np -nc --no-mcp --no-themes --no-skill-registry --thinking off
  [--model …] "<prompt>"` and expects one JSON object with `name`, `key`, `mode`, `bpm`, `progression`,
  `chords`, `lead`, `drums`, `energy`, `brightness`, `swing`, `vinyl`, `space` and `lead_density`. Every value is
  validated and clamped; anything missing keeps the current style. Extensions stay enabled because some model
  providers (such as `claude-bridge`) are extensions. A request takes a few seconds; the music keeps playing
  meanwhile, and a failure is shown in the status line without touching the music.

## Tests

```sh
uv run pytest
```
