# Feature: composition-engine

Objective: make the generated music compositionally richer (form, harmony, melodic development, counter-melody, bass motion) in the gentle-stream engine, keeping the same instruments and sound (option A only).
Branch: `feat/realtime-music-tui`. Delivery: `exception-ok` (local work-unit commits). Test runner: `uv run pytest`.

## Specs

- **S1 — Option A, then B only with free licenses.** User: "solo A", then "agrega b si sabemos que son de licencia libre". A (composition) first; B ("Instrumentos sampleados (soundfonts con fluidsynth), reverb por convolución, compresión y mastering") is added only with samples/impulse responses whose license is verified at the original source and allows commercial use of the resulting music (monetized YouTube). C stays out.
- **S7 — Option B (sound).** Sampled instruments, convolution reverb, compression and mastering, keeping the procedural instruments as fallback when no verified sample set is installed. Every asset records its source URL and license.
- **S2 — Song form with a tension curve.** Each piece runs through sections (e.g. intro, A, B, A', breakdown, B', outro-like release, then continues with variation since the stream is endless); each section has an energy level that decides which layers play and how dense they are.
- **S3 — Harmony with direction.** Section B uses a contrasting progression from the same mode; phrases end in cadences; occasional passing/secondary chords before the cadence; an occasional key lift (modulation) on a returning section.
- **S4 — Motif development.** Melody built as question/answer phrases from one motif per piece, developed by repetition, sequence, inversion and rhythmic augmentation; strong-beat notes are chord tones.
- **S5 — Counter-melody and moving bass.** A slower second voice in higher-energy sections, moving mostly in contrary motion to the lead; the bass approaches the next chord root with passing tones.
- **S6 — Nothing regresses.** Seamless bars, no clipping, much faster than real time, deterministic per seed, `queue_style`/`queue_next` behavior and `Style` mood mapping unchanged.
- Assumption A1: applied to gentle-stream first; porting to the video skill scripts (`~/.claude/skills/release-video/assets/lofi_mel.py`, `~/.agents/skills/elegant-short/assets/music_ambient.py`) waits for the user's listening approval.

## Tasks

| ID | Specs | Route | Status | Evidence |
|----|-------|-------|--------|----------|
| C2 | S1, S7 | delegated (context backstop) + independent verifier (Risk item 5) | done | RED (ImportError samples/mastering; engine 2 failed; main 3 failed) → GREEN; 117 tests green (parent re-ran); samples 116x, procedural 40x real time |
| C1 | S1-S6 | delegated (context backstop; disjoint from T5 writer) | done | RED (no composer module, no engine.section) → GREEN; 89 tests green (parent re-ran); 28.6x real time; peak 0.80 |

## Log

- L1 (2026-10-23, user): "bien y una cosa, para los videos cuando te pido musica o para los shorts, como e lque te pedi para el reloj el lunar pilot, fue hecho de este estilo? hay forma de que sea algo mas complejo?"
- L2 (2026-10-23, user): "solo A"
- L8 (C2 independent verifier): S1, S6, S7 PASS, no blockers; 117 passed x2, 0 skips; path traversal/symlink tar probes rejected; fallback to procedural in 5 scenarios; uv tool install without extra runs. Advisories: same-process sample renders not bit-identical for pad/brush hat (~-87 dBFS); urlopen without timeout can hang setup-samples and HTTPException shows a traceback; SIGKILL leaves .partial-* files; existing cache files are 0600 from an earlier code version; no warning when sf2 installed but pyfluidsynth missing; Debian sid/forky moved to 3.1-6.
- L7 (C2): brew fluid-synth 2.6.1; optional extra `samples = [pyfluidsynth>=1.4.0]`; `gentle-stream setup-samples` downloads https://deb.debian.org/debian/pool/main/f/fluid-soundfont/fluid-soundfont-gm_3.1-5.3_all.deb (SHA-256 pinned, matches packages.debian.org) to ~/.cache/gentle-stream with LICENSES.txt; never at startup. Master: procedural-IR convolution reverb + RMS bus compressor (stateful) + existing tanh soft clip. Advisories: balance calibrated by RMS, not by ear; Debian may drop 3.1-5.3 from the pool; with the sf2 installed, player/app tests run on the sample backend. Listening: out/*-samples.wav.
- L6 (C1): new pure planner `composer.py`; form intro 4 → A 8 → B 8 → A' 8 (+2 key lift with pivot) → breakdown 4 → B' 8 → release 4, then loops without intro with a developed motif and a new B progression. Instruments and master chain unchanged (diff checked). Advisories: contrary motion 99% may sound rigid; key returns from +2 to home at the breakdown without pivot; one form cycle is ~147 s at 72 bpm. Listening files (git-ignored): out/{midnight-rain,sunny-rhodes}-{antes,despues}.wav, seed 21, 150 s.
- L4 (2026-10-23, user): "agrega b si sabemos que son de licencia libre"
- L5 (license check, primary texts): ACCEPTED — VSCO 2 Community Edition: CC0 1.0 (https://raw.githubusercontent.com/sgossner/VSCO-2-CE/master/LICENSE), waiver "for any purpose whatsoever, including without limitation commercial". FluidR3_GM: MIT, Frank Wen / Toby Smithe (Debian copyright file https://metadata.ftp-master.debian.org/changelogs/main/f/fluid-soundfont/unstable_copyright; original musescore URL no longer exists), keep the MIT notice. REJECTED — GeneralUser GS: allows commercial music, but its author states "I cannot be 100% sure where all of the samples originated", which fails "si sabemos". OpenAIR IRs: license varies per recording, not verified. Reverb: generate our own impulse response procedurally (no third-party asset). Plan C2: fluidsynth + FluidR3_GM (and VSCO 2 CE where it has the instrument) rendered per note into the bar buffer, downloaded to a user cache with checksum and a LICENSES file, never committed; procedural instruments stay as fallback. New dependency = Risk item 5.
- L3: findings: Lunar Pilot used `music_lunar.py` (now `elegant-short/assets/music_ambient.py`: D Lydian ambient, additive pads, FM celesta, sub, shimmer, numpy reverb); channel shorts and releases use `lofi_mel.py`. Both loop 4 chords with a 2-bar motif and have no song form.
