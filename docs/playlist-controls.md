# Save and repeat a playlist from your selected song

Use the existing Textual Playlist panel and the flat black command strip directly above the mood input. No browser or separate player is needed. All keyboard shortcuts remain available; Tab focuses buttons, and tooltips describe actions and shortcuts. `↻  off` / `↻  on` and play/pause glyphs show state without relying on color. The left group is **💾 ↻  off/on ✎ ☷**; the full-strip center is **⏮ ⏸/▶ ⏭**; the paused **▶** is also blue while its button and the strip stay black and centered; the right group is **− five rising volume bars + ⚙ chosen model**. The flat black strip expands to show outlined accent-colored bars (pink by default) that fill one cell per 5% from left to right: 10%, then 15%, 20%, 25% and 30% of the total volume. Its numeric percentage sits centered beneath the bars, and the state line no longer duplicates it. At 24 rows or fewer the black strip uses two rows, with compact ascending step glyphs above the same percentage. Narrow terminals scroll each side group independently; focusing the model button brings it into view without moving the three transport buttons. At 24 rows or fewer, tighter panel spacing keeps the spectrum, state, model and status inside the panel. Long status/model lines have full hover tooltips.

The gear now opens **Settings**, including appearance and explicit **Choose mood
model…** access; **m** still opens the model picker directly. **F2** always recovers
Settings when controls, input or footer are hidden. Icon-label gaps default to two
cells (configurable from one to three); toolbar backgrounds remain flat black.
See [Appearance settings](appearance.md) for preview/save/cancel, visibility and colors.

## Quick path

Playlist opens with a separate title, description, My/Default tabs, breadcrumb and a hint below the song list. Save shows visible labels for the song and destination; choosing **New playlist…** reveals its playlist-name label and field together. Tab, arrows, Enter, Escape and the existing shortcuts still work, including in narrow terminals.

1. Press **s** (or **💾**) to save the currently audible song. Enter a song name
   and choose **Saved prompts**, **New playlist…**, or an existing named playlist.
   With the keyboard, Tab to **Destination**, Enter opens it, ↑/↓ highlights a
   playlist, Enter selects it, and Enter again saves. An existing selection
   focuses **Save** so the final Enter does not reopen the picker. **New playlist…**
   reveals and focuses its name field; Enter there saves. Clicking **Save** works too.
2. Open **Playlist** (**p** or **☷**). Under **My playlist**, open **Saved prompts** or a named group. Songs appear in the same panel with a `🌸` marker on the left and one blank display row between entries, even when descriptions wrap. The marker and gap never become part of saved song names or selectable options. **< My playlists** / Backspace returns to groups; Left/Right switches the My/Default tabs.
3. Highlight a song, then choose **Repeat from selection** or press **c**. Playback begins at that song, advances after each complete composition, and wraps in alphabetical display order. **Default playlist** works the same way but cannot be edited.
4. Click **↻ on** or press **c** outside the input to turn rotation off. Current audio is not cut or restarted. Already-buffered transitions may still be heard.

Clicking a song or pressing Enter normally applies only that style and exits continuous mode. That selection retains its full list snapshot and index, so the global **↻ off** button can start continuous playback predictably afterward. With no selection, it asks you to select a song first. An empty group cannot start rotation. A newly interpreted mood also exits rotation through ordinary style application.

## Saving safely

- Save captures `player.now.style` when the dialog opens, not render-ahead state or a pending mood.
- The same song title replaces only inside the chosen group. Identical titles in other groups remain independent.
- Saved prompts remain in `presets.json`. Named playlists use the separate versioned `playlist-library.json` beside it, under `$XDG_CONFIG_HOME/gentle-stream` (default `~/.config/gentle-stream`). Nothing is migrated.
- **d** / Delete removes only the highlighted saved song, never a whole group. Deleting the final song retains its named group. Built-in songs are read-only.
- Validation and unreadable-file errors remain visible; failed saves keep your entered fields and do not dismiss the dialog. Repair or move a corrupt file before saving to it again.

## Playback and model feedback

**⏮ Previous** returns to the preceding song in the selected playlist's displayed order, wrapping from the first song to the last. It works with continuous playback on or off and preserves pause/play and volume. It never means “restart this composition.” Repeated clicks each move one entry, even while paused or while the producer renders ahead. With no playlist selection it asks you to select a song in **Playlist (p)** first.

An ordinary song selection applies only that song. The first Previous request validates its remembered full playlist snapshot; an invalid snapshot rejects that request without changing playback. Later navigation reuses the validated snapshot. An ordinary newly interpreted mood clears this selection.

The strip also mirrors existing play/pause, Next, mood, model, save, playlist, and volume actions, plus continuous playback. Click −/+ or press −/+ to change volume by 5%; Up/Down retain their previous 1% adjustments, shown exactly in the numeric label. The bar fill advances at each completed 5% step; outlined empty bars remain visible at 0%. Only the display changes: volume and saved music are not reset. The model button shows the selected model, or the honest Gentle Shell default when none is selected; long IDs are elided with their full value in the tooltip. The model line retains session usage.

Audible playlist/song position is read from `player.now`, not the producer's future index. Continuous playback belongs to the daemon and continues after **b** detaches the UI. No model calls are made to apply saved styles or advance a playlist. **q** still stops playback. An older daemon rejecting continuous or Previous commands produces a visible error instead of an assumed success; the UI stays usable. Previous adds no CLI flag or keyboard shortcut.

See [runtime semantics](continuous-playback.md) and [storage format](named-playlists.md) for API and buffering details.
