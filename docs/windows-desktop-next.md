# Windows desktop player: evidence and follow-up

The opt-in Windows desktop player has a working local candidate. It switches between full and mini without replacing the player window. The latest real trial opened, rendered and exited with code 0. This is a feature-branch checkpoint, not a release, merge, or complete Windows acceptance report.

## Current UI

| Corrected mini | Full window: toolbar highlighted for redesign |
| --- | --- |
| [Open screenshot](../odd/evidence/windows-desktop-t17/windows-mini.png) | [Open screenshot](../odd/evidence/windows-desktop-t17/windows-full-toolbar.png) |

The mini capture shows one header, the spectrum, and separate playback and volume groups. The full capture identifies a different problem: small, scattered controls across a long strip, duplicate window/app chrome, and weak grouping of the actions. These are design inputs, not a selected redesign.

## Verification snapshot

- The isolated native transition/close probe passed with a simulated player: 21.804 s inside the probe, 25.583 s including the wrapper. The recorded milestones were host ready at 7.725 s, Python child entry at 8.420 s, NumPy imported at 9.822 s, App import at 17.927 s, and App run entry at 18.043 s. This is **one successful sample**, not an SLO or the cause of any earlier timeout.
- The offline wheel passed before the final mini layout changes: 69 members and 5 pinned asset/license hashes verified. The latest real trial used a byte-checked snapshot with the updated App, not a rebuilt release wheel.
- The mini change reproduced the desktop-only defect in two checks while the two terminal checks passed. After the fix, 33 focused tests passed; all four layout tests passed again after the typed Spectrum lookup. Before/after Textual geometry matched for desktop full and ordinary terminal full/mini at 40×8 and 60×12.
- Independent continuity/drag-grant test passed. The separate independent mini-layout run and the PNG rasterization timed out. The two human screenshots provide native visual evidence; they do not replace the timed-out checks.
- The corrected real trial produced a frame and exited 0 with empty stdout/stderr. The earlier real trial exited 1 with a generic child/browser cleanup warning; its cause and exact cleanup outcome remain unknown.

## What remains

1. **Measure performance first.** Measure cold and warm start-to-visible-window, start-to-first-frame and start-to-audible playback separately. Compare host/WebView startup, child imports and initialization; measure idle CPU/memory and resize/mode-switch responsiveness. Use repeated comparable samples, then agree on targets.
2. **Optimize the largest measured cost.** The roughly 8.1 s interval between the NumPy and App import milestones is worth investigating, not proof of the earlier failure. Keep failure timeouts, actual process exit/drain, and playback continuity intact.
3. **Design the full player and mini together.** Draft comparable full/mini directions. Give playback, volume, creative commands, and settings clear roles; sort out title ownership and the highlighted full toolbar first. Cover narrow dimensions, DPI, actual glyph contrast, focus, shortcuts, and long text. Keep the redesign as a design task until the user chooses a direction.
4. **Close verification debt.** Diagnose the earlier exit 1 without guessing. Confirm audible playback, held-left drag over eligible cells only, DPI changes, focus/typing, restore geometry and controlled close on the real player. Mini group widths below 37 columns are unverified. Reconcile incomplete independent verification, rerun packaging for the actual delivered source, and record the evidence before treating it as release-ready.
5. **Keep separate work separate.** The quarantined alternate Footer and incomplete runner recovery are not included in the Windows player checkpoint. Native Hyprland remains independently unverified.

Tracked next-stage work: [Windows performance and UI ODD task](../odd/tasks/windows-performance-ui.md). Current implementation and findings: [original ODD log](../odd/tasks/theme-preview-hyprland.md).
