# Feature: recorded-instruments

Objective: play real recorded instrument samples (CC0 / public domain, one SOURCES entry per file) instead of the generic FluidR3_GM soundfont where a recording exists, with several takes per sound; then an orchestral genre, finite "moments" (epic announcement music) and more genres.
Branch: `feat/recorded-instruments` (local, from `feat/realtime-music-tui`). Delivery: `exception-ok` (local work-unit commits; push/PR are the user's call). Test runner: `uv run pytest`.

## Specs

- **S1 — Same pattern as the video foley.** "te acuerdas los cambios que hicimos para las ediciones de videos con la musica mejorada con sonidos sin licencia? podemos implementar lo mismo en este repo?" User chose option 1: "Instrumentos sampleados reales: reemplazar/mejorar las voces sintetizadas (piano, batería, bajo…) por samples grabados CC0, con SOURCES.txt y licencia por archivo." Pattern from cartoon-engine: a recorded sound wins over the voice with the same name, several takes per sound chosen by seed, provenance (URL + license) logged per file.
- **S2 — Download, not copy.** "las descargas grandes no, usa las que ya tenemos y las copiamos" was withdrawn after the existing CC0 library turned out to hold no instruments: "no entonces hacemos la descarga que dijiste". The proposal accepted: the library is downloaded by `setup-samples` like today (pinned SHA-256), never committed (README.md:288 policy), only CC0 / public domain, licence verified at the original source, NC/ND rejected.
- **S2b — One download (L9).** "Todo junto": one `setup-samples` downloads FluidR3 plus the whole recorded library (~550–800 MB). Covered (verified CC0): piano, nylon, jazz guitar, distorted guitar, electric bass, organ, accordion, vibraphone, harp, tenor sax, drum kit, latin percussion, strings, brass, timpani. Kept on FluidR3 (no verified CC0): rhodes, celesta, choir, alto sax, timbales, taiko. Excluded: Karoryfer Swirly/Unruly (size, licence unverified at origin), dsmolken double-bass (earlier CC-BY).
- **S3 — Fallback order.** Recorded sample → FluidR3_GM → procedural voice. Nothing that works today breaks when the recorded library is missing.
- **S4 — First stage: all genres.** "todos lso generos" (answer to "¿arranco solo con lofi como primera etapa, o querés que la primera tanda cubra todos los géneros…?"). Every acoustic/recordable voice of the 10 genres gets a recorded version where a CC0/PD source exists; synth voices (polysynth, saw lead, synth bass, 808) and uncovered instruments keep FluidR3/procedural.
- **S5 — More genres and moments (confirmed L8).** "y fijate la posiblidad de meter mas generos musicales a ver que te parece, y ademas algo asi como musica para momentos, como por ejemplo si quiero hacer un anuncio la famosa musica epica". Proposed order: (1) recorded library, (2) orchestral genre, (3) "moments" mode: finite piece with an arc and chosen duration, exported to WAV, orthogonal to genre, (4) more genres (boom bap, bossa nova, tango). L8: order chosen "Orquestal → momentos → géneros nuevos (recomendado)"; moments selected: "Anuncio épico" ("Construye, llega al golpe y cierra; la música épica de tráiler.") and "Suspenso / revelación" ("Tensión sostenida y un remate al revelar algo.").
- **S6 — Nothing regresses.** Seamless bars, no clipping, > 10x real time, deterministic per seed and independent of history, both backends, presets.

## Tasks

| ID | Specs | Route | Status | Evidence |
|----|-------|-------|--------|----------|
| T0 | S1, S2 | delegated research (web, read-only) | done | task mw5e5i6y-2-huqm; sources in Log L9 |
| T1 recorded core: library format, install (download, SHA, SOURCES), decoding, layered renderer, levels, takes | S1, S2, S2b, S3, S6 | delegated writer (context backstop; parallel with audio-device T1) | done, uncommitted | RED ImportError → GREEN 31 passed; `uv run pytest -q` 479 passed; differential vs HEAD (10 genres × FluidR3/procedural × 6 bars, seed 7, no library) identical hashes. Risk: item 5 (soundfile dep) |
| T1b manifest per source (VCSL, VSCO, Meta, FreePats) via SFZ parsing, GM mapping for every genre (absorbs T2 mapping) | S2b, S4, S6 | delegated writer (context backstop) | in progress | |
| T2 recorded voices for all genres (takes by seed, fallback) | S1, S3, S4, S6 | inline | pending | |
| T3 orchestral genre | S5, S6 | delegated writer (context backstop; parallel with T1b, disjoint surfaces) | done | RED 22 failed → GREEN 98 passed; `uv run pytest -q` 510 passed; samples 108x / procedural 55x real time, peak 0.87 |
| T4 moments mode (epic announcement, suspense/reveal) + WAV export | S5 | tbd | pending | |
| T5 new genres: boom bap, bossa nova, tango | S5 | tbd | pending | |

## Log

- L1 (2026-11-06) "te acuerdas los cambios que hicimos para las ediciones de videos con la musica mejorada con sonidos sin licencia? podemos implementar lo mismo en este repo?"
- L2 user picked "1" (recorded instruments).
- L3 "y fijate la posiblidad de meter mas generos musicales a ver que te parece, y ademas algo asi como musica para momentos, como por ejemplo si quiero hacer un anuncio la famosa musica epica"
- L4 "las descargas grandes no, usa las que ya tenemos y las copiamos" → checked cartoon-engine/sfx-lib (61 MB, all CC0/PD): foley and ambience only, no instruments.
- L5 "no entonces hacemos la descarga que dijiste"
- L6 Exploration: sampled mode today = FluidR3_GM via pyfluidsynth (samples.py), backend chosen in engine.py:45-51, genres get `sampled` in `instruments(rng, sr, sampled)`. VSCO 2 CE (CC0) was already licence-verified in composition-engine.md L5. Research T0 launched for instrument coverage and pinned URLs.
- L7 "todos lso generos" → S4 rewritten (A1 dropped); T0 research scope extended to every genre's acoustic instruments.
- L8 Order: "Orquestal → momentos → géneros nuevos (recomendado)". Moments: "Anuncio épico", "Suspenso / revelación" (not intro/outro, not countdown). Added T5.
- L9 Research: VCSL (CC0, pin commit c1ea7bcc…), VSCO 2 CE (CC0), Meta Open Sound piano + nylon OGG (65 MB, release tag), FreePats github.com/freepats/* (CC0): jazz/dist guitar, bass YR, drawbar organ, accordion HN, world percussion, timpani. User chose "Todo junto" over packages (~550–800 MB).
- L10 T1 design: recorded instruments slot in under `SampleInstruments` as a `Layered` renderer with the same `note(chan, bank, program, key, vel, hold, tail)` signature as `samples.Renderer` (samples.py:226-244): a GM (bank, program) or drum (kit, key) covered by the recorded library renders from recordings, anything else from FluidR3. So every genre's `play(Program)` / `hit(Hit)` and the existing level matching (`_levels`, `_program_level`, `_hit_level`) and caches work unchanged. Takes: chosen from the note's unbucketed velocity (deterministic per seed, independent of history). Files decoded at install time (soundfile, setup only) to mono 44.1 kHz 16-bit WAV under `~/.cache/gentle-stream/recorded/`; runtime reads with scipy.io.wavfile. A committed manifest lists every file (url, sha256, root key, key and velocity range, take, source, license); SOURCES.txt generated at install; the index file is written last as the completion marker.
- L11 (2026-11-06) "cuando lo tengas todo me decis y lo pruebo": user tests at the end; continue through T5 without intermediate check-ins unless a real decision is needed.
- L12 T1 done (writer mw5efxrb-4-6j1y). Manifest schema: sources{id:{name,url,license CC0-1.0|public-domain,license_url,ref}}, instruments[{name,gm[[bank,program]],tail,zones[{url,sha256,source,root,lokey,hikey,lovel,hivel,take}]}], drums[{name,gm[[kit|"*",key]],zones}]. `samples.setup_recorded()` exists; CLI wiring pending (parent). Known limits: recorded library only layers over FluidR3 (needs FluidSynth); no sustain loops (held notes longer than the recording end early); `engine.backend` still "samples". Scratch to remove: /var/folders/k1/2nnhpdfx0wq8k6w8n2nqx9_h0000gn/T/gprCyJOV/gaPKZbnk/tmp.GGIjKlAGhZ
- L13 T3 done (writer mw5etn4o-7-dpsz): `orchestral` genre registered; the model sees it through `bridge.genre_lines()` (reads REGISTRY). FluidR3 Contrabass 43 is silent at MIDI 60 where `_program_level` measures, so basses use cello 42; timpani rms lowered to 0.12. Listening WAVs: /var/folders/k1/2nnhpdfx0wq8k6w8n2nqx9_h0000gn/T/gprCyJOV/gaamXUJV/tmp.g9xxT68kbS (remove after use).
