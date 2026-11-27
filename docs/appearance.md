# Customize appearance without changing the music

Press **F2** or click **Settings** in the command strip. F2 works even inside an
input or with the controls, mood input, visualizer, details and footer hidden.
You can also click **Command palette** in the header or press **Ctrl+P**, then
choose **Settings**. To browse playlists, click **Playlist** in the command strip
or press **p**.

Use **↑/↓** to highlight a spectrum style and **Enter** to select it for the
preview. Other fields are under **Colors**, **Spectrum and spacing**, and
**Layout**. **Tab** / **Shift+Tab** move between controls. The form scrolls while
**Models**, **Audio → Output**, and the action buttons stay visible.

- Valid edits update the synthetic example, not the player.
- **Save and Close** atomically saves the edits and applies them to the player.
  Invalid input disables Save and shows an error (for example, `Invalid accent.`).
  A write error keeps the draft open without changing the applied appearance.
- **Defaults** fills and previews the original values. Save to keep the reset.
- **X Close**, **Close**, or **Esc** prompts before discarding an edited draft.
  Choose **Keep Editing** or **Discard Changes**.

The player’s model line shows the selected model. **Models** opens
the model picker from Settings; **m** goes directly to it from the player. Opening Settings or changing appearance does not list models,
interpret moods, synthesize speech, or call playback controls. Model choice does not
change musical parameters. Submitting the separate mood input still interprets a mood.

## Audio output

Choose **Audio → Output** in Settings or press **o** from the player. Use **↑/↓**
to highlight an output and **Enter** to switch without restarting the app. **Esc**
closes the picker without changing the output if you haven't submitted a switch.
Choose **System default** to use the OS default without changing it. If the last
selected device is missing and the player falls back to the system default, the
picker reports that fallback. A failed switch is shown in the picker so you can
try another device. The picker doesn't switch the audio backend and is unavailable
with the optional WSL Windows audio bridge.

Audio output is applied separately; you don't need **Save and Close**. Closing
Settings or discarding an appearance preview won't undo an output switch.

## Bounded settings

| Setting | Choices / limits | Default |
| --- | --- | --- |
| Style | Bars, Filled Wave, Mirrored, Segmented, Dots | Bars |
| Palette | pink, analyzer green/yellow/red, mono using accent | pink |
| Theme | gentle/custom background, black, charcoal | gentle |
| Custom background | exactly `#RRGGBB`; used by gentle theme | `#140a12` |
| Accent | exactly `#RRGGBB` | `#ff76c3` |
| Minimum bar height | 5–30 terminal rows; grows into available panel space | 5 |
| Bands | 8–128 source bands; repeated across wider terminal columns | 48 |
| Refresh | 5–30 Hz | 15 |
| Smoothing | 0–1, retained fraction of previous visual frame | 0 |
| Peak hold | 0–3 seconds; zero disables | 0 |
| Toolbar icon gap | 1–3 terminal cells | 2 |
| Visibility | independent visualizer, controls, mood input, footer, track details, model line | all shown |
| Compact | tighter player layout that frees space for the visualizer, even at 80×24 | off |

At wide terminal widths, each configured band occupies additional columns rather than leaving the rest of the graph blank. Segmented bars retain their one-cell spacing. These are spectrum **visualizations**, not DSP equalizer controls. Fonts, glyph
availability and terminal transparency are terminal-owned; no unsupported font or
terminal-transparent theme is offered. Custom colors can reduce contrast: choose a
readable accent/background pair or use Defaults. The command strip keeps transport
centered and wraps its groups onto two or three rows when they no longer fit.
The exact volume percentage sits between − and +. These buttons move in 5% steps
(Up/Down in 1% steps). Play/Pause changes glyph without moving the button.
Track details and mood share one upper metadata row with full hover tooltips,
while the model, playback state and status remain separate. Short terminals can
give up spectrum rows to make room for the controls. Compare Compact layout on
and off in the example: it reduces padding, and once saved gives the player
visualizer more room, including at 80×24.
Hiding the graph shrinks the panel rather than reserving empty graph space.
**/** or **✎** temporarily reveals and focuses a hidden mood input without
changing its saved visibility. Esc restores the configured visibility.

Refresh uses one UI-thread timer. Applying preferences stops the previous timer;
no audio threads are created or replaced. Hidden visualizers still poll the existing
spectrum interface to refresh audible metadata, without rendering the hidden graph.

## Storage and recovery

`$XDG_CONFIG_HOME/gentle-stream/appearance.json` (default
`~/.config/gentle-stream/appearance.json`) is independent of presets and playlists.
No migration, credentials, model configuration or music parameters are stored here.
The JSON envelope is `{"version": 1, "preferences": {...}}`; preferences contains all
fields of `gentle_stream.appearance.Preferences`. Unknown preference fields are
preserved; missing, incorrectly typed, out-of-range or malformed known fields
reject the record as a whole. Saving merges changes with untouched values; if
an edited setting changed elsewhere, reopen Settings before trying again. Missing files use
defaults without creating a file. Unreadable/corrupt records use defaults and display
`Cannot read appearance preferences: …` in Settings, and are never overwritten.
Repair or move the file yourself before saving again. Defaults is a preview, not a
force-overwrite mechanism for corrupt data.

`PreferenceStore.load()` reports read errors via `.error`; `save(Preferences)` raises
`PreferenceError` on validation/read/write failure. Successful saves flush and fsync
a same-directory staging file, then atomically replace the record. A failed write may
retain a `.appearance-*.tmp` diagnostic staging file; it does not claim success.
`StreamApp.apply_preferences(preferences)` is the single application path for
startup and saved Settings. The draft preview and discard never use it.

## Playback safety

Changing appearance in the running TUI does not stop or restart the player. **b**
closes that interface while leaving music playing; **q** stops the player. If you
use the optional Windows audio output, do not close the terminal that started it
until you have confirmed your new playback session is healthy. A second interface
may attach to an already-running player without sending a new mood; appearance
previews do not require an audio restart. This behavior is verified in isolated
UI tests, not an acoustic or live-Windows-terminal test.
