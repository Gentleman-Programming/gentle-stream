# Feature: audio-device

Objective: let the user choose which audio output device plays the music.
Branch: `feat/recorded-instruments` (shared tree, disjoint edit surface; parent owns git). Delivery: `exception-ok`. Test runner: `uv run pytest`.

## Specs

- **S1 — Choose the output device.** "y te subo un agregado que podes ir haciendo ya, la posibilidad de elegir en que dispositivo de audio ejecutar la musica"
- **S2 — Where to choose (assumption A1, not stated by the user).** List devices (`gentle-stream devices`), pick one with a `--device NAME|INDEX` option and from the TUI (a key that opens a device picker); the background player switches device live, without restarting the piece when possible. The last choice is remembered across runs; if the remembered device is gone, fall back to the system default and say so in the status line.
- **S3 — Nothing regresses.** Default behaviour (system default device), `ctl` actions, background player, existing tests.

## Tasks

| ID | Specs | Route | Status | Evidence |
|----|-------|-------|--------|----------|
| T1 | S1-S3 | delegated writer (context backstop at ~158k parent tokens; parallel with recorded-instruments T1) | done: verified; B1 fixed in 218a73b and rechecked (mw5f5zl5-8-t1k6: 50 passed, repro fixed, probes clean) | RED per file (imports, 5+8+5 failures) → GREEN; `uv run pytest -q` 479 passed |

## Log

- L1 (2026-11-06) "y te subo un agregado que podes ir haciendo ya, la posibilidad de elegir en que dispositivo de audio ejecutar la musica"
- L2 Seams: player.py:48-50 opens `sounddevice.OutputStream(**kw)`; daemon.py:36 `GENTLE_STREAM_AUDIO_FACTORY` test seam; daemon.py:231 audio errors.
- L3 Design handed to the writer: new `audio_devices.py` (list output devices via sounddevice.query_devices with an injectable query, resolve NAME|INDEX case-insensitive exact then unique substring, remembered choice in `$XDG_CONFIG_HOME/gentle-stream/audio.json` like presets.py:34-36); Player gets `device` + `set_device` (open the new stream first, keep the old one if that fails, then stop/close the old and start the new, under a stream lock); daemon commands `devices` and `set_device`, state gains `device` and `device_missing`; PlayerClient mirrors them; CLI `gentle-stream devices` and `--device`; TUI key `o` opens an OptionList picker modelled on ModelPicker (app.py:95-142).
- L4 Writer mw5efxr9-3-ezbo done. Remembered device matched by exact name only; --device validated before spawning the player; in-process Player does not persist. Residual risk: if reopening the previous device also fails, no stream remains and the error is raised.
- L5 Slice: branch carries 12 unpushed commits of feat/realtime-music-tui plus this work (~4.9k lines since main); delivery stays exception-ok (local work-unit commits, push/PR are the user's call).
- L6 Verifier mw5esuv5-5-7aoj on c6b7188: S1 met, S3 met (scratch 452 passed / 27 skipped), S2 unmet by B1 (switch failing on both devices left `_stream=None`; the next switch only remembered the device while the daemon answered ok). Concurrency probes clean (1 stream, 1 callback, exact audio). Fixed in 218a73b (started state from the producer thread, original error reported, verifier regression tests committed). Advisories not acted on: A1 PortAudio device list is frozen at daemon start (sounddevice init once) and --device sends an index resolved in the client; A2 a remembered but busy device stops the daemon instead of falling back; A3 open-new-first may fail on exclusive backends; A4 /tmp/gs-* dirs from the test fixture.
