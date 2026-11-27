# Save and repeat a playlist from your selected song

Open **☰ Playlist** from the command strip above the mood input, or press **p** to open **My playlist**. Press **l** to open **Default playlist** directly.
**!↺ Panic**, **▣↓ Save**, **⇄ Repeat** and **☰ Playlist** are on the left;
**← Previous**, **Ⅱ / ▸ Play/Pause** and **→ Next** stay centered; **− / + volume**
and **⛭ Settings** are on the right. The groups wrap on narrow terminals. Tab
focuses buttons; hover for shortcuts. The Repeat tooltip shows whether it is on
or off, and Play/Pause changes glyph with playback state.

**Settings** opens appearance and **Choose mood model…**; **m** opens model selection
directly. **F2** or the header's **Command palette** (**Ctrl+P**) can open Settings even
with other controls hidden. The gap between toolbar icons is configurable from
one to three cells. **Compact** gives the spectrum more space even on short terminals.
See [Appearance settings](appearance.md) for preview, visibility and colors.

## Quick path

Playlist opens with a separate title, description, My/Default tabs, breadcrumb and a hint below the song list. Save shows visible labels for the song and destination; choosing **New playlist…** reveals its playlist-name label and field together. Use **X Close** or **Esc** to dismiss either dialog without applying a song or saving. Tab, arrows, Enter and the other shortcuts still work, including in narrow terminals.

1. Press **s** (or click **Save** on the command strip) to save the currently audible song. Enter a song name
   and choose **Saved prompts**, **New playlist…**, or an existing named playlist.
   With the keyboard, Tab to **Destination**, Enter opens it, ↑/↓ highlights a
   playlist, Enter selects it, and Enter again saves. An existing selection
   focuses **Save** so the final Enter does not reopen the picker. **New playlist…**
   reveals and focuses its name field; Enter there saves. Clicking **Save** works too.
2. Open **Playlist** from the strip or press **p**. Under **My playlist**, open **Saved prompts** or a named group. Songs appear in the same panel with a `✿` marker on the left and one blank display row between entries, even when descriptions wrap. The marker and gap never become part of saved song names or selectable options. **< Back to groups** or **Backspace** returns to groups. **Left** or **y** selects My playlist; **Right** or **l** selects Default playlist.
3. Highlight a song, then choose **Repeat from selection** or press **c**. Playback begins at that song, advances after each complete composition, and wraps in alphabetical display order. **Default playlist** works the same way but cannot be edited.
4. Click **⇄ Repeat** while it is on, or press **c** outside the input to turn rotation off. Current audio is not cut or restarted. Already-buffered transitions may still be heard.

Clicking a song or pressing Enter normally applies only that style and exits continuous mode. That selection retains its full list snapshot and index, so the toolbar’s **⇄ Repeat** button can start continuous playback predictably afterward. With no selection, it asks you to select a song first. An empty group cannot start rotation. A newly interpreted mood also exits rotation through ordinary style application.

## Saving safely

- Save captures `player.now.style` when the dialog opens, not render-ahead state or a pending mood.
- The same song title replaces only inside the chosen group. Identical titles in other groups remain independent.
- Saved prompts remain in `presets.json`. Named playlists use the separate versioned `playlist-library.json` beside it, under `$XDG_CONFIG_HOME/gentle-stream` (default `~/.config/gentle-stream`). Nothing is migrated.
- **d** / Delete removes only the highlighted saved song, never a whole group. Deleting the final song retains its named group. Built-in songs are read-only.
- Validation and unreadable-file errors remain visible; failed saves keep your entered fields and do not dismiss the dialog. Repair or move a corrupt file before saving to it again.

## Playback and model feedback

**← Previous** returns to the preceding song in the selected playlist's displayed order, wrapping from the first song to the last. It works with continuous playback on or off and preserves pause/play and volume. It never means “restart this composition.” Repeated clicks each move one entry, even while paused or while the producer renders ahead. With no playlist selection it asks you to select a song in **Playlist (p)** first.

An ordinary song selection applies only that song. The first Previous request validates its remembered full playlist snapshot; an invalid snapshot rejects that request without changing playback. Later navigation reuses the validated snapshot. An ordinary newly interpreted mood clears this selection.

Click or press **− / +** to change volume by 5%, or press **Up / Down** for 1%.
The exact percentage appears between the volume buttons. The model line shows the selected model
(or Gentle Shell default) and session usage. Hover to read a long model line in full.

Audible playlist/song position is read from `player.now`, not the producer's future index. Continuous playback belongs to the daemon and continues after **b** detaches the UI. No model calls are made to apply saved styles or advance a playlist. **q** still stops playback. An older daemon rejecting continuous or Previous commands produces a visible error instead of an assumed success; the UI stays usable. Previous adds no CLI flag or keyboard shortcut.

See [runtime semantics](continuous-playback.md) and [storage format](named-playlists.md) for API and buffering details.
