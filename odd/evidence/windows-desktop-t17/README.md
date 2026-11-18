# Windows desktop checkpoint evidence

These two captures were supplied by the human after opening the corrected Windows candidate. The red annotation in the full-window capture is part of the supplied image; it marks the toolbar for the next design pass.

| Capture | Dimensions | SHA-256 |
| --- | --- | --- |
| [Corrected mini](windows-mini.png) | 429 × 222 | `5d8155c46a6c806e7d2d51723750426b72904d236fd7a22517575faa1fd57891` |
| [Full player and toolbar annotation](windows-full-toolbar.png) | 1084 × 663 | `4342c0b48acf444a7ec8b721772bb818e4b5e0f3cd99a1391af31d1950822586` |

The corrected real-player trial published a frame and exited 0 with empty stdout and stderr. It used the curated source snapshot with the mini layout delta applied. The App file used in that run had SHA-256 `dfd1fee8d345c0013e05215d99a2f6bc9148be69f73cccc733854f6bf6046549` (raw Windows bytes). These captures are evidence of the rendered UI, not of sound output, drag behavior, DPI migration, or keyboard focus.

See the [verification summary and remaining work](../../../docs/windows-desktop-next.md). Runtime directories, profile contents, and unsanitized logs are not published here.
