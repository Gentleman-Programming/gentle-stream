# Play in a terminal or try the Windows desktop preview

The terminal is the entry point on this branch. The Windows desktop window and
output picker are preview features; the commands for those previews below require
a checkout that includes the corresponding code.

## Terminal quick start

From a checkout with the requirements in the [README](../README.md#get-started), run:

```sh
uv run gentle-stream
```

| To | Do this |
| --- | --- |
| Play or pause | Press `Space`. |
| Change the mood | Press `/`, type a description, then press `Enter`. |
| Change the spectrum | Press `F2`, choose **Bars**, **Filled Wave**, or **Mirrored**, then choose **Save and Close**. The preview does not change playback. |
| Save or recall an audible style | Press `s` to save it as a saved prompt, or `p` to pick one. Press `Esc` to close the dialog. |
| Leave music playing in the background | Press `b`. Press `q` instead to stop playback and quit. |

Use `Tab` and `Enter` to operate the toolbar. Settings remains reachable with
`F2` in a narrow pane. See the [terminal controls](../README.md#controls) for
more shortcuts. The shared playlist transport is included in this branch, but
this does not enable the newer terminal playlist controls or **My playlist** and
**Default playlist** tabs on its own.

## Native Windows desktop preview

The desktop preview requires native Windows, the `desktop` extra, and Microsoft
Edge WebView2 Runtime. If `gentle-stream --help` in the preview checkout lists
`--desktop`, run:

```powershell
uv run --extra desktop gentle-stream --desktop
```

Use `F2` or the Settings gear for **Customization** and **Models**. In the native
preview's **Playlists** dialog, **My playlist** shows saved prompts and personal
groups. **Default playlist** is read-only. Switch tabs with Left/Right and use
`Backspace` to return to the groups. **X Close** and `Esc` close the current dialog;
the main window's **Close** stops playback.

**Output switching is not released.** In a preview that exposes **Settings → Audio**,
`o` opens the same selector. Moving the highlight does not switch devices. A switch
is attempted only when confirmed; closing the dialog does not undo that attempt.
Device identity and recovery are still under review. Do not rely on this preview
to move private playback between speakers and headphones.

## Packaged artwork

The Python wheel includes the rose-spectrum PNG and a multi-size Windows ICO.
The native window can use the ICO when the preview includes its window integration.
The wheel is not an installer: it creates no branded app bundle or OS shortcut,
and it cannot change the icon of an already-running host terminal.
