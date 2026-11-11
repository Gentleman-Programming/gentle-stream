# Choose instrumental beats and rhythm fusions

Five new styles add rap beats, trap and softer electronics without vocals.
Instrumental melodies remain available: no vocals does not mean no melody.

## Quick path

1. Restart gentle-stream if it was already running older code (including its daemon).
2. Press `p`, then choose **Default playlist** and select a genre below.
   This built-in selection needs neither a model call nor saved presets.
3. Or submit a mood prompt to the existing model interpreter using an example below.
   Interpretation depends on the model; the genre hints expose all five choices.

| Genre | Sound | Spanish mood example |
|-------|-------|----------------------|
| `hiphop` | Boom-bap kicks, snare on beats 2 and 4, warm keys | “Beats de rap sin voz, boom-bap con piano para concentrarme” |
| `trap` | Halftime snare, rapid hat rolls, deep tuned synthesized 808 bass | “Trap instrumental sin voz, bajo 808 profundo y hi-hats rápidos” |
| `chill_electronic` | Gentle steady electronic pulse and mellow synth textures | “Electrónica suave sin voz, tranquila y espacial para trabajar” |
| `lofi_trap` | Warm lo-fi keys and tape with trap drums and 808 bass | “Mezcla lo-fi con trap, teclas cálidas y ritmo relajado, sin voz” |
| `electro_trap` | Robotic synth stabs and melodic lead with trap drums | “Electro con trap instrumental, sintetizadores y bajo 808, sin voz” |

## Original reggaeton groove

Choose `reggaeton` in **Default playlist** without a model call, or include the literal word
**reguetón**, **reggaeton** or **dembow** in a mood, such as “Reguetón original con guitarra
nylon, bajo redondo y ritmo para bailar”. This uses the existing single model interpretation;
the explicit genre word is routed locally and does not expand the ordinary model prompt.
The two-bar dembow, clap/snare backbeats, upbeat guitar chops and bass notes are arranged
from the shared composer's own harmony and melody, not from a referenced recording.
`chords=nylon` and `lead=nylon` select plucked-string colours (procedural or GM sampled).
They are synthetic approximations, not a recording of a guitarist. Actual provider cost,
sound quality and live playback have not been verified by an offline render.

## Growth and strength

Try “Rap electrónico trap instrumental, sensación de crecimiento y fuerza,
sintetizadores, bajo 808 y melodía, sin voz”. A concrete starting style is
`genre=electro_trap`, `bpm=132`, `mode=minor`, `energy=0.9`, `drums=busy`,
`chords=rhodes`, `lead=piano`, `lead_density=0.9`, `brightness=0.75`,
`vinyl=0`, `space=0.3`. The shared song form opens quietly, brings in percussion,
and develops higher-energy sections; it is not a continuous volume crescendo.
A reference with vocals can guide the requested rhythm without adding vocals;
these settings do not claim to reproduce or analyze a particular recording.

## Controls and boundaries

- These are **curated two-style fusions**, not arbitrary genre combinations.
  Each fusion is one composition at one tempo, with shared harmony, bass and melody;
  it never overlays two independently generated songs.
- `drums: none` removes percussion; `soft` reduces density and gain; `busy` adds
  denser hats. Trap rolls develop in energetic sections, not the quiet intro.
- `lead: none` removes melodic lead and counter-melody, not chords or bass.
  Leave a lead instrument selected when you want instrumental melodies.
- The original ten genres, default lo-fi style and preset schema are unchanged.
  New names also work through the existing style/playlist interfaces.
- Procedural synthesis works offline without sample downloads. Existing sample
  backends reuse their keys/synth voices and drum kit; trap's tuned 808 bass is
  synthesized on both backends. No speech, singing or recordings are added.

## Playback recovery

Click **Panic** or press **F4** to cut output abruptly, reopen it and restart the audible
current track from its beginning. Its musical settings (including muted lead/drums),
playlist position, repeat mode, pause and volume remain; no model or speech synthesis
call is made. This discards queued audio and effects tails, not a next-song selection.
A cut may click. Failed reopening is reported and leaves playback paused; retry Panic
then resume if needed. Recovery is not proof that every cause of stutter is fixed.

## Verification

`tests/test_instrumental_beats.py` exercises registry discovery, model hints with a
mocked interpreter, rhythm timing, controls, shared composition, deterministic
bounded rendering and switching. It uses `unittest`, without an audio device.
