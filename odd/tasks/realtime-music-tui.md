# Feature: realtime-music-tui

Objective: terminal app with an interactive TUI that generates music in real time from a mood/concept prompt, reusing the procedural lo-fi synthesis used for the YouTube videos (`lofi_mel.py`).
Branch: `feat/realtime-music-tui` (new standalone repo, no remote). Delivery: `exception-ok` (local work-unit commits only; no remote/PR exists yet). Test runner: `uv run pytest`.

## Specs

- **S1 — Terminal app with interactive TUI.** "quiero crear una app que se pueda ejecutar desde la terminal PERO la idea es que tenga una TUI interactiva". Stack chosen by the user: Python + Textual.
- **S2 — Same idea as the YouTube music, but real time.** "quiero que te fijes como hacemos la musica para los videos de youtube y copies esa idea PERO que genere musica en tiempo real". Reference: `~/.claude/skills/release-video/assets/lofi_mel.py` (numpy/scipy procedural synthesis: minor-key progressions, felt piano / rhodes / nylon / pad, sub bass, swung dusty drums, vinyl, room, tape wow). Real time = audio is synthesized continuously and streamed to the sound device while playing, never rendered as a whole file first.
- **S3 — Describe the feeling/concept.** "tu le dices como quieres que sea el sentimiento, concepto,etc". The user types free text in the TUI; it is translated into musical parameters.
- **S4 — Controls.** "eso hace un play next pausa y listo, se genera en tiempo real": play, pause, next.
- **S5 — Interpretation through Gentle Shell.** User answer: "utilizara gentle shell por detras con el modelo que elija la persona ;)". The prompt is interpreted by running `gentle-shell` non-interactively with the model the person chooses.
- Assumption A1: "next" = a new piece for the same mood (new seed/variation), like the per-short variants of `lofi_mel.py`.
- Assumption A2: the model is chosen with a `--model` flag and an in-TUI picker fed by `gentle-shell -- --list-models`.
- Assumption A3: while Gentle Shell interprets a prompt, the current music keeps playing; the new style starts at the next bar.

## Tasks

| ID | Specs | Route | Status | Evidence |
|----|-------|-------|--------|----------|
| T1 | S2 | inline | done | `56ddb3b`; 20 tests green (no RED: port written before tests, see L5) |
| T2 | S3, S5 | inline | done | `b23e274`; RED (module missing) → GREEN 29 tests; real call OK (L6) |
| T3 | S1, S4, S5 | inline | pending | — |
| T4 | S1-S5 | inline + independent verifier (Risk item 4: audio callback thread + producer thread) | pending | — |

- T1: project scaffold (uv) + streaming synthesis engine: `Style` parameters, bar-by-bar renderer with stateful effects (filters, room, wow, vinyl) so consecutive chunks are seamless; tests.
- T2: Gentle Shell bridge: prompt → JSON → validated `Style`; model listing; tests with a fake `gentle-shell` binary.
- T3: Textual TUI + audio player (sounddevice): play/pause/next, mood input, model picker, now-playing panel and level meter; Textual pilot tests with a null audio sink.
- T4: README, `gentle-stream` entry point, smoke run, independent verification.

Route evidence: no Writer trigger (units are sequential: T2/T3 depend on T1's `Style`); parent writes inline.

## Log

- L1 (2026-10-23, user): "mira. necesito que hagamos algo, quiero crear una app que se pueda ejecutar desde la terminal PERO la idea es que tenga una TUI interactiva, quiero que te fijes como hacemos la musica para los videos de youtube y copies esa idea PERO que genere musica en tiempo real, tu le dices como quieres que sea el sentimiento, concepto,etc y eso hace un play next pausa y listo, se genera en tiempo real"
- L2 (2026-10-23, user answers): stack "Python + Textual (Recomendado)"; interpretation "utilizara gentle shell por detras con el modelo que elija la persona ;)".
- L3 (evidence): `gentle-shell -- -p --no-session -nt -ne -ns -np -nc --no-mcp --no-themes --no-skill-registry --thinking off '<prompt>'` returned clean JSON in ~8.5 s; `gentle-shell -- --list-models` prints a `provider model ...` table.
- L4 (evidence): `-ne` drops extension providers (`claude-bridge`), so `--model claude-bridge/...` fails with `Model ... not found`. The bridge keeps extensions on and disables tools/skills/prompts/context/MCP instead; claude-bridge haiku answers in ~6 s, openai-codex gpt-6-luna in ~8.5 s.
- L5 (T1): engine renders ~50x faster than real time, RMS about -12/-14 dBFS, peak < 0.8. Tests were written after the port, so RED was not observed for T1.
- L6 (T2): real run, mood "una tormenta de verano en el campo, nostálgica pero con esperanza" → "Verano tras la tormenta", G mixolydian, 78 bpm, 1-4-6-5, rhodes/nylon, soft drums, 9.2 s.
