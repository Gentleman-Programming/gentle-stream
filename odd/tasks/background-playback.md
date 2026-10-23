# Feature: background-playback

Objective: the music keeps playing after leaving the TUI, so `/gentle:music` in Gentle Shell can return to Pi with music; a background player process owns the audio and the TUI and a `ctl` command control it.
Branch: `feat/realtime-music-tui`. Delivery: local work-unit commits, pushed to the public repo by the parent. Test runner: `uv run pytest`.

## Specs

- **S1 — Keep playing.** User: "necesitamos si o si un seguir sonando" (about `/gentle:music`, after the v1 proposal said the music stops when leaving). Leaving the TUI with the "back" key leaves the music playing.
- **S2 — Player process.** A background player process (`gentle-stream daemon`, started automatically, never by hand) owns Engine + Player + audio output and listens on a per-user local socket (Unix domain socket in a private directory, 0700; `multiprocessing.connection` with an authkey stored in a 0600 file, or equivalent stdlib-only). Only one player per user; a stale socket from a crashed player is detected and replaced.
- **S3 — TUI as client.** `gentle-stream` attaches to a running player or starts one, then shows and controls it exactly as today (title, details, mood, spectrum, state, model, status, presets, token usage, all keys). Mood interpretation stays in the TUI (it spawns gentle-shell) and sends the resulting style to the player.
- **S4 — Keys.** `q` = quit the TUI and stop the music (unchanged behavior). `b` = back: quit the TUI and leave the music playing. Footer, `--help`, README document both.
- **S5 — ctl.** `gentle-stream ctl play|pause|toggle|next|stop|status`. `status` prints one JSON line `{"running": bool, "playing": bool, "name": str, "genre": str, "mood": str}` (only `running:false` when no player) and exits 0; other commands exit 0 on success, and exit 1 with `gentle-stream: nothing is playing` on stderr when no player runs. `stop` ends the player process.
- **S6 — Nothing regresses.** Existing behavior, tests, determinism and seamless audio keep working; the audio-device error stays a clean one-line error (now reported by the TUI when the player cannot open the device).
- **S7 — Gentle Shell.** In gentle-pi (`/Users/alanbuscaglia/work/gentle-pi-music`, branch `feat/gentle-music`): `/gentle:music [mood]` opens the TUI full screen (Pi's external-editor pattern); `/gentle:music next|pause|play|stop|status` run `gentle-stream ctl …`; on Pi `session_shutdown` the music is stopped (`ctl stop`).
- Assumption A1: the player is per user (not per terminal); a second TUI attaches to the same music.

## Tasks

| ID | Specs | Route | Status | Evidence |
|----|-------|-------|--------|----------|
| P1 | S1-S6 | delegated (context backstop) + independent verifier (Risk item 4: IPC, process lifecycle) | done | RED (ImportError client; 7 failed + 5 errors) → GREEN 73 passed x2; pty smoke with real audio: b keeps playing, ctl status/next/pause/play/stop as specified |
| P2 | S7 | delegated (gentle-pi writer, scope extended) | done locally (no PR yet) | gentle-pi 6e8aca60c on feat/gentle-music; 16 tests; typecheck clean; real terminal OK (L7) |

## Log

- L1 (2026-10-23, user): "no puede /gentle:music directamente ejecutar en un widget full screen gentle-stream? y listo" → v1 proposal accepted with "si" (music stops on exit).
- L2 (2026-10-23, user): "necesitamos si o si un seguir sonando"
- L7 (real terminal, pty 140x40, gentle-shell -- --no-session -e <worktree>/extensions/gentle-music.ts, gentle-stream installed from public main a0c7583): /gentle:music shows the TUI; music plays; `b` returns to Pi with music playing (ctl status running/playing); Pi takes keys again (`/gentle:music status` → "♪ Midnight Rain · lofi · playing"); `/gentle:music stop` → running:false; second run: `/quit` with music playing → Pi exits and running:false, no daemon left.
- L6 (P1 independent verifier): S1-S6 pass, no blockers; 403 passed x2; single instance under 5-9 concurrent starts; SIGKILL stale cleanup; 0700/0600; 21 malformed requests incl. pickle rejected; exact ctl outputs; audio-error line exact. Parent proved tests do not reach the real runtime dir (sentinel daemon under a temp HOME survived the full suite); the user's own player stopped cleanly at 00:05 (consistent with q). Advisories (not fixed): concurrent start without an audio device makes the loser wait 30 s; a silent peer blocks accept during the auth handshake; set_volume NaN accepted; ≤0.5 s attach window during stop; handle() catches only KeyError/TypeError/ValueError.
- L5 (2026-10-23, user): "una cosa mas que te pido...  afplay ~/work/gentle-stream/out/genre-dubstep-samples.wav que ocupe mejor la pantalla hay mucho hueco" → reproduced at 160x45 (spectrum fixed at 5 rows, empty band below); spectrum now 1fr (min 5 rows), RED → GREEN test.
- L4: incident: commit 497bc7f (pushed) captured the P1 writer's half-done __init__.py, so public main failed to start (`from .client import ...`). Hotfix 147330f restored the entry point; root cause: parent staged a file inside a running writer's surface.
- L3: decisions by the parent (super-yolo mode, no question asked): `q` keeps stopping the music (standalone behavior unchanged), new `b` key detaches; `ctl` subcommand; Pi stops the music on session shutdown to avoid orphan audio.
