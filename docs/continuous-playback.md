# Play a selected playlist continuously

Continuous playback is opt-in and owned by the background player, not by UI polling. It starts **from the selected song**, plays one complete 44-bar composition per song, advances in list order, and wraps. Detaching a client leaves the sequence running; restarting the daemon does not restore it.

## UI integration (T3)

Both `Player` and `PlayerClient` expose:

```python
player.set_playlist(styles, start_index=selected_index, name=display_name, continuous=True)
player.play()
# Later:
player.set_continuous(False)
```

- `styles` is a nonempty `Sequence[Style]` snapshot, not a storage object or file. `start_index=0`, `name=""`, and `continuous=False` are the defaults. Indices are zero-based; booleans are not indices.
- Configuration preserves pause/play state. Explicitly call `play()` when enabling from the selected UI entry.
- Show audible song information from `player.now`, **not** `player.style` or the top-level playlist index. The latter describe selected/render-ahead state.
- `next()` advances exactly one entry in continuous mode, even when the producer has already buffered the next song. Repeated presses advance once each. Outside continuous mode, Next retains its existing same-mood variation behavior.
- `previous()` selects the preceding entry (wrapping), whether continuous mode is on or off, and preserves pause/play, volume, and the continuous flag. Like Next in continuous mode it navigates from consumed metadata or the last not-yet-consumed explicit selection, never speculative lookahead.
- `previous(styles=None, start_index=0, name="")` optionally supplies the ordinary picker's snapshot. When no playlist is configured, the snapshot is validated/prepared and Previous is applied in one synchronized operation, without rendering the selected song first. Failure leaves state and counters untouched. A configured playlist takes precedence over this fallback. With neither, it rejects with `Select a song in Playlist (p) before using Previous.`
- Ordinary `set_style(style)` leaves playlist rotation and retains its existing buffered style-change behavior. A normal newly generated mood should use that method.
- Disable changes future scheduling without clearing buffered audio, stopping playback, or restarting the audible song. An already rendered song boundary may still be heard; subsequent forms no longer rotate. Re-enabling without reconfiguration continues the configured snapshot. To enable **from the UI selection**, call `set_playlist(..., continuous=True)` again instead.

Empty queues and invalid indices/styles/flags reject the entire request before active state changes. All songs are validated before replacing the sequence. Advancement uses the validated instrumental snapshot only; it does not call models or read storage.

## State and wire additions

Existing fields remain available. Additive fields default safely when connecting to an older daemon:

| Field | Meaning | Old daemon default |
| --- | --- | --- |
| `continuous` | Future automatic scheduling enabled | `False` |
| `playlist_name` | Configured display identity | `None` |
| `playlist_index` | Selected/render-ahead index | `None` |
| `now.playlist_name` | Identity attached to consumed PCM | `None` |
| `now.playlist_index` | Index attached to consumed PCM | `None` |

Top-level selection may change before `style` is rendered; `style` may change before `now.style` becomes audible. Buffered chunks retain their original metadata, including after a manual style change. Do not treat a display name as a durable storage identifier.

```json
{"cmd":"set_playlist","styles":[{"name":"Selected","bpm":90}],"start_index":0,"name":"Evening","continuous":true}
{"cmd":"set_continuous","enabled":false}
{"cmd":"previous"}
{"cmd":"previous","styles":[{"name":"Selected","bpm":90}],"start_index":0,"name":"Evening"}
```

Success returns the existing `{"ok":true,"state":...}` envelope. Failure returns `{"ok":false,"error":"bad request: ..."}`; `PlayerClient` raises `PlayerError`. Playlist wire styles use the existing Style normalizer, but reject supplied fields that would require normalization instead of silently repairing them. Existing single-style wire normalization is unchanged. Old daemons reject the new commands normally; the UI should show the error rather than imply continuous playback was enabled.

## Audio boundary and pause diagnostics

The producer serializes rendering and controls with its existing render lock. The callback only consumes chunks, applies gain, and records audible metadata under the existing short queue lock; it performs no storage reads or synthesis. Natural transitions keep the engine accumulation/master-chain tails and smoothing. Manual Next and Previous share the existing short crossfade and render/enqueue boundary. Previous's optional snapshot is validated on the caller under the render lock, never the callback queue lock. On sounddevice, paused callbacks stop consuming after the existing short de-click fade; Windows output also stops callbacks while its owned sink is paused. Elapsed time and UI polls do not advance audible selection.

The legacy raw-output diagnostic preserves the delayed-pause counterexample: silent callbacks alone cannot retract PCM already sent downstream. Windows output now has a bounded private PCM/control protocol. A pause response follows termination of the owned FFplay sink and its writer, discarding that sink's buffered generation; resume creates a fresh sink without clearing the Player's music queue, playlist selection or volume. Sounddevice retains its existing de-click fade. Device-free backpressure/lifecycle tests cover this owned-output boundary; they do not measure speaker latency. A hard cut may click, native device reopening may delay resume, and no zero-hardware-latency guarantee is made. See [Windows output details](windows-wsl.md#optional-native-windows-audio).

## Verification scope

`tests/test_previous_playback.py` covers Previous's wrap, paused/off-mode selection, lazy snapshot atomicity, resident navigation, additive command/default compatibility, mixed rapid navigation, and in-flight/render-ahead producer boundaries. `tests/test_named_playlist_ui.py` covers actual Previous clicks, an older daemon rejection, and centered geometry at 60/80/120 columns. `tests/test_continuous_playback.py` covers complete-form order/wrap, selection, atomic rejection/validation, resident snapshot advancement, audible/render-ahead metadata, pause, Next at buffered boundaries, concurrent producer controls, old-daemon defaults, and unchanged off-mode audio. The daemon test covers isolated fake-output detach/reconnect. No real devices, model calls, downloads, or production player interaction are required. Public CLI help and toolbar integration remain T3-owned.
