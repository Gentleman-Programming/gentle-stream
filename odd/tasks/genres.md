# Feature: genres

Objective: generate other musical genres besides lo-fi, with lo-fi as the default; the genre is part of the style the mood prompt is interpreted into.
Branch: `feat/realtime-music-tui` (local, no remote). Delivery: `exception-ok` (local work-unit commits; no remote/PR exists). Test runner: `uv run pytest`.

## Specs

- **S1 — Genres, lo-fi by default.** "hagamos algo, que no sea solo lofi, que se pueda tambien generar diferentes estilos pero que por defecto sea lofi". `Style.genre` defaults to `lofi`; existing behavior, saved presets without a genre and tests keep sounding/working as lo-fi.
- **S2 — Chosen genres.** User selection: "Ambient elegante", "Chiptune / 8-bit", "Synthwave", "Jazz", plus "algo de metal o rock se podra?" (answered yes, with sampled distorted guitar from FluidR3_GM, sounding like well-produced MIDI rock rather than a recorded band). Option descriptions shown to the user:
  - Ambient elegante: "Como la música del short del Lunar Pilot: pads, celesta, sin batería, mucho espacio."
  - Chiptune / 8-bit: "Ondas cuadradas y triangulares, arpegios rápidos y batería de ruido: música de juego retro original."
  - Synthwave: "Sintes analógicos ochentosos, bajo en octavas, batería con caja grande y reverb."
  - Jazz: "Acordes extendidos, walking bass, batería con escobillas y swing marcado."
  - Rock/metal: power chords, palm mutes, distorted guitar, driving drums (double kick for metal).
- **S3 — The model picks the genre.** The Gentle Shell prompt lists the genres; the model chooses one from the mood; invalid values fall back to the current genre. Assumption A1: the user can also force a genre by naming it in the prompt (no separate genre picker unless asked).
- **S4 — Each genre is its own unit.** Instruments, drum patterns, harmony/rhythm rules and master chain per genre (e.g. chiptune without vinyl/tape wow); composition keeps the song form from composer.py. Only originally composed material; no melodies of existing works. Sample assets only from licenses already verified (FluidR3_GM MIT, VSCO 2 CE CC0).
- **S6 — More genres.** User: "mientras mete 3 estilos mas: electro, cumbia, dubsteb" and "ah y agrega electro house". Interpretation (assumption A2): `electro` = classic electro/electro-funk (808 breakbeats, robotic synth riffs), kept distinct from `electrohouse` (four-on-the-floor, dirty bass, builds and drops); `cumbia` = cumbia groove (guacharaca, tambora/congas, root-fifth bass, accordion or digital-cumbia lead); `dubstep` = 140 bpm half-time, sub + LFO wobble bass, drops. Same rules as S4/S5; target descriptions live in each placeholder docstring.
- **S5 — Nothing regresses.** Seamless bars, no clipping, > 10x real time, deterministic per seed, play/pause/next, presets, both backends.

## Tasks

| ID | Specs | Route | Status | Evidence |
|----|-------|-------|--------|----------|
| G0 | S1, S3, S4, S5 | delegated (context backstop) | done | RED (8 failed + ImportError) → GREEN; 197 passed (parent re-ran); lo-fi hash identical both backends |
| G1 ambient | S2, S4, S5 | delegated, parallel writer | done | RED vs placeholder → GREEN; genre + contract tests green |
| G2 chiptune | S2, S4, S5 | delegated, parallel writer | done | RED vs placeholder → GREEN; genre + contract tests green |
| G3 synthwave | S2, S4, S5 | delegated, parallel writer | done | RED vs placeholder → GREEN; genre + contract tests green |
| G4 jazz | S2, S4, S5 | delegated, parallel writer | done | RED vs placeholder → GREEN; genre + contract tests green |
| G5 rock/metal | S2, S4, S5 | delegated, parallel writer | done | RED vs placeholder → GREEN; genre + contract tests green |

| G6 electro | S6, S4, S5 | delegated, parallel writer | done | RED vs placeholder → GREEN; genre + contract tests green |
| G7 cumbia | S6, S4, S5 | delegated, parallel writer | done | RED vs placeholder → GREEN; genre + contract tests green |
| G8 dubstep | S6, S4, S5 | delegated, parallel writer | done | RED vs placeholder → GREEN; genre + contract tests green |
| G9 electrohouse | S6, S4, S5 | delegated, parallel writer | done | RED vs placeholder → GREEN; genre + contract tests green |

Route evidence: G1-G5 are independent units with disjoint edit surfaces (one module + one test file each), each clearly heavier than a subagent start → Writer trigger (a) parallelism; parent owns git and runs one seam check (full suite) at the end.

## Log

- L1 (2026-10-23, user): "hagamos algo, que no sea solo lofi, que se pueda tambien generar diferentes estilos pero que por defecto sea lofi"
- L2 (2026-10-23, user answer): selected "Ambient elegante, Chiptune / 8-bit, Synthwave, Jazz"; custom: "algo de metal o rock se podra?"
- L8 (seam check G6-G9): electro 9f94756, cumbia 3e0e5d5, dubstep 6a74d88, electrohouse 9f7d976; README genres table updated; 328 passed (P1-owned test files excluded while that writer runs). Delivery: pushed straight to the public main as the user asked to publish (no PR flow in this repo); slice boundary = this push.
- L7 (2026-10-23, user): "mientras mete 3 estilos mas: electro, cumbia, dubsteb"; then "ah y agrega electro house". Placeholders registered by the parent (electro, electrohouse, cumbia, dubstep); tests listing genre names updated; 302 passed.
- L6: repo published public at https://github.com/Gentleman-Programming/gentle-stream (main = 10b47c5); `uv tool install 'gentle-stream[samples] @ git+https://github.com/Gentleman-Programming/gentle-stream'` verified in a temp UV_TOOL_DIR.
- L5 (seam check): all five genres, U1 token display (3b4da86) and the FluidSynth voice-state root fix (1afccd4: every note primes a throw-away note) committed; full suite 278 passed; tree clean. Listening files: out/genre-<name>-{procedural,samples}.wav (seed 21, 120 s); nobody has listened yet (levels balanced by RMS). Measured real codex interpretation: ~2.2k tokens, ~US$0.00026.
- L4 (G0): plugin interface genres/base.py (Genre: name, description, bpm, modes, voices; instruments, start, chord, schedule, master → Master), synth.py helpers (osc polyBLEP, adsr, distort), samples Program/Hit API, contract test over every genre × backend. Rock and metal share `rock` (metal = drums busy + energy ≥ 0.7). Advisory: Style.bpm still capped at 140. Also queued U1 (user: "si dale metemos"): show tokens/cost per interpretation; measured claude-bridge ~17.7k tokens/call, codex ~7.4k.
- L3 (2026-10-23, user, earlier question): "por ejemplo si yo digo que quiero escuchar algo de mario bross lo hace?" — answered: the model only fills parameters; it never reproduces existing melodies (Nintendo copyright); chiptune gives an original retro-game feel.
