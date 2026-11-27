# Instrument timbres in the desktop preview

The desktop preview uses the same player and arrangement generation. These changes only affect the named procedural voices; they do not add controls or model calls.

| Genre | What changed |
| --- | --- |
| Electro | The bass has a rounder attack and less treble bite, while keeping more of the note's harmonics after the pluck. The shared bass used by Hip Hop and Reggaeton is unchanged. |
| Rock / metal | Distorted palm-muted guitar tails decay faster to leave room for the next chug. Open and clean guitars are unchanged. |
| Electrohouse | The snare's noise snap dies away faster between closely spaced roll hits. The pitched body is unchanged. |
| Synthwave | The saw lead's low-pass cutoff drops from 5 kHz to 4 kHz to soften the upper harmonics. The envelope, vibrato and echo are unchanged. |

The SoundFont paths still use the original programs, drum hits and level calibration. Deterministic offline tests check the waveform changes and use fake sample renderers to check that routing and calibration. They do not establish how these changes sound on real hardware.

Earlier local comparisons found slower guitar and snare rendering in the variant work; treat that as directional, not as a benchmark for this checkout. Shorter audible tails do not shorten the generated buffers. No real-time playback, underrun, or Linux/Omarchy audio performance was measured for this instrument pass.
