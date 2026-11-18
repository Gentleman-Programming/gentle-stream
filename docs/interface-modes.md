# Full player and session miniplayer

Click the top-right **▾ / ▴** button or press **r** to switch the current
`StreamApp` between full player and miniplayer. Typing `r` in a mood input or
Settings dropdown remains ordinary typing/search. The button stays available
even if appearance preferences hide controls, input, visualizer or footer.

The miniplayer shows the spectrum and previous, play/pause, next, minus, volume
meter and plus. Hover for shortcut hints; Tab/Enter also operates the buttons.
**F2** always opens Settings. `/` does not reveal the mood input in miniplayer.
Return to full mode to describe a mood. Pending interpretations continue normally.

Switching is session-only: no app, player, Shell, worker or refresh timer is
recreated, no preferences are saved, and playback/volume/playlist/model/session
state is retained. Settings Save may legitimately replace the appearance timer,
but keeps the selected mode. Returning to full restores the **current** saved
visibility, density and height preferences. Appearance's Compact switch remains
a spacing preference, not a content-mode selector.

At short/narrow sizes the full body scrolls and the command strip reflows into
three rows with independently scrollable side groups. At normal sizes the
centered transport and 13-cell meter are unchanged. Miniplayer bounds its graph
to the available rows. Settings uses a scrollable stacked form/preview on narrow
screens; at short heights its essential form and action row take precedence over
descriptive text. Focus scrolls fields into view. The read-only Settings preview
always depicts the full player, including a static header toggle, never another
interactive player. Layout supports 40×8, 60×12, 80×24, 120×30 and 140×40;
this does not resize the OS terminal or configure a compositor.

## Optional native Windows window

`gentle-stream --desktop` hosts this same App/player/session in one persistent
ConPTY. The existing button/r also transforms one frameless Form: fixed,
always-on-top mini; remembered session position; full geometry restored on return.
Held-left dragging works on granted background/Spectrum cells, never controls or
Settings/modal cells. Applied appearance updates the native border/canvas;
Settings drafts do not. Closing (including b/q) stops the music and awaits actual
child exit/drain before acknowledging normal Form close. Fatal renderer failure
fails closed immediately while the supervisor records bounded child cleanup.

This is additive Windows-only opt-in, incompatible with `--compact`, window
launcher options or subcommands. It requires the pinned desktop extra and
WebView2; ordinary terminal/Linux imports remain independent. See
[Windows desktop](windows-desktop-host.md) for commands and lifecycle limits and
the [checkpoint](windows-desktop-next.md) for evidence and open checks.

## Separate startup compact app

`--compact` and Hyprland `--window-mode floating` still launch the independent
`CompactStreamApp`, without a Shell or mood/settings actions. They do not gain
this session toggle. See [Hyprland](hyprland.md) for launcher requirements and
[appearance](appearance.md) for draft preview and persistence semantics.
