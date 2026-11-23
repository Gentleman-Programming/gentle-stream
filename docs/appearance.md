# Customize appearance without changing the music

Press **F2** or click **Settings** in the command strip. F2 works even inside an
input or with the controls, mood input, visualizer, details and footer hidden.
You can also click **Command palette** in the header or press **Ctrl+P**, then
choose **Settings**. To browse playlists, click **Playlist** in the command strip
or press **p**.

Settings groups fields into **Appearance**, **Visualizer** and **Layout**. Use
**Tab** / **Shift+Tab** to move between controls. On small terminals the form
scrolls while the model picker and action buttons stay in view.

- **Preview** validates the whole form and applies it immediately without saving.
- **Save & close** validates and atomically saves the whole form, applies it, then
  returns to the player. If validation or the write fails, Settings stays open and
  shows the error (for example, `Invalid accent.`). The edits stay in the form;
  the saved and currently applied appearance are unchanged.
- **Defaults** fills the form with the original values and previews them without
  saving. Use **Save & close** to keep the reset.
- **Close** or **Esc** discards unsaved appearance edits and restores the appearance
  from when Settings opened, including after a preview.

The player’s model line shows the selected model. **Choose mood model…** opens
the model picker from Settings; **m** goes directly to it from the player. Opening Settings or changing appearance does not list models,
interpret moods, synthesize speech, or call playback controls. Model choice does not
change musical parameters. Submitting the separate mood input still interprets a mood.

## Bounded settings

| Setting | Choices / limits | Default |
| --- | --- | --- |
| Style | gentle block bars, Winamp-inspired segmented bars, minimal dots | gentle |
| Palette | pink, analyzer green/yellow/red, mono using accent | pink |
| Theme | gentle/custom background, black, charcoal | gentle |
| Custom background | exactly `#RRGGBB`; used by gentle theme | `#140a12` |
| Accent | exactly `#RRGGBB` | `#ff76c3` |
| Minimum bar height | 5–30 terminal rows; grows into available panel space | 5 |
| Bands | 8–128 source bands; repeated across wider terminal columns | 48 |
| Refresh | 5–30 Hz | 15 |
| Smoothing | 0–1, retained fraction of previous visual frame | 0 |
| Peak hold | 0–3 seconds; zero disables | 0 |
| Icon-label gap | 1–3 terminal cells | 2 |
| Visibility | independent visualizer, controls, mood input, footer, track details, model line | all shown |
| Compact | tighter player layout that frees space for the visualizer, even at 80×24 | off |

At wide terminal widths, each configured band occupies additional columns rather than leaving the rest of the graph blank. Segmented bars retain their one-cell spacing. These are spectrum **visualizations**, not DSP equalizer controls. Fonts, glyph
availability and terminal transparency are terminal-owned; no unsupported font or
terminal-transparent theme is offered. Custom colors can reduce contrast: choose a
readable accent/background pair or use Defaults. The command strip shares the
header and footer background and uses one row at all terminal sizes. A single
horizontal volume gauge sits between −/+ to
the left of Settings: eight pink `▰/▱` segments with the exact numeric percentage
to their right. The volume controls still move in 5% steps (Up/Down in 1% steps);
because there are only eight segments, not every numeric step changes a segment.
Transport controls stay centered, and the side groups scroll independently.
The paused Play glyph alone uses blue without changing its button background or
position. Track details and mood share one upper metadata row with full hover
tooltips, while the model, playback state and status remain separate. At 24 rows
or fewer the same one-row gauge remains visible. At a fixed terminal size, use
**Preview** to compare Compact layout on and off: it reduces padding and margins
so the visualizer has more room, including at 80×24.
Hiding the graph shrinks the panel rather than reserving empty graph space. **/** temporarily reveals and focuses a hidden mood input without changing its
saved visibility; applying preferences again restores the configured visibility.

Refresh uses one UI-thread timer. Applying preferences stops the previous timer;
no audio threads are created or replaced. Hidden visualizers still poll the existing
spectrum interface to refresh audible metadata, without rendering the hidden graph.

## Storage and recovery

`$XDG_CONFIG_HOME/gentle-stream/appearance.json` (default
`~/.config/gentle-stream/appearance.json`) is independent of presets and playlists.
No migration, credentials, model configuration or music parameters are stored here.
The JSON envelope is `{"version": 1, "preferences": {...}}`; preferences contains all
fields of `gentle_stream.appearance.Preferences`. Unknown, missing, incorrectly typed,
out-of-range or malformed fields reject the record as a whole. Missing files use
defaults without creating a file. Unreadable/corrupt records use defaults and display
`Cannot read appearance preferences: …` in Settings, and are never overwritten.
Repair or move the file yourself before saving again. Defaults is a preview, not a
force-overwrite mechanism for corrupt data.

`PreferenceStore.load()` reports read errors via `.error`; `save(Preferences)` raises
`PreferenceError` on validation/read/write failure. Successful saves flush and fsync
a same-directory staging file, then atomically replace the record. A failed write may
retain a `.appearance-*.tmp` diagnostic staging file; it does not claim success.
`StreamApp.apply_preferences(preferences, persist=False)` is the single application
path shared by Settings, startup, preview, save and discard.

## Playback safety

Changing appearance in the running TUI does not stop or restart the player. **b**
closes that interface while leaving music playing; **q** stops the player. If you
use the optional Windows audio output, do not close the terminal that started it
until you have confirmed your new playback session is healthy. A second interface
may attach to an already-running player without sending a new mood; appearance
previews do not require an audio restart. This behavior is verified in isolated
UI tests, not an acoustic or live-Windows-terminal test.
