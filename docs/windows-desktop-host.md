# Windows desktop host (H1 candidate — not integrated)

This optional host wraps terminal cells, not a second HTML player. It has no
transport, player, audio, daemon, model, Gentle Shell or app imports. Normal CLI
and Linux imports do not load pywebview/pythonnet. Packaging and controller
integration belong to T15; this is **not** a working desktop launch command.

## Direction and ownership

The selected `.validation/windows-desktop/reference-mini.png` supplies the layout:
close at the left of a slim header, monospace title, expand/collapse at the right,
then the existing terminal spectrum and transport cells. Header icons are authored
SVGs. Colors come exclusively from the required theme, not the reference pink or
sample volume. There are no duplicated HTML playback/volume controls or extra
visible terminal windows.

One helper-owned frameless Form and WebView are intended to transform between
1100×700 logical-pixel resizable full mode and fixed 420×220 mini mode. Mini is
always on top; returning restores captured full bounds/topmost. Native Form border
style is never toggled. Owned edge grips provide full-mode resize. Position is
clamped to the current monitor work area; `mini_position` is a snapshot for parent
persistence, not a new settings file. DPI migration and actual held-left dragging
remain unverified.

## Controller interface

- `DesktopTheme(background, foreground, accent)`: all three must be `#RRGGBB`.
- `DesktopWindow(*, on_input, on_resize, on_action, theme, assets_dir,
  saved_mini_position=None)`: optional dependencies are loaded only by `run()`.
- `run()` runs once on the main Python thread. WinForms owns a single STA GUI
  thread. A GUI timer serializes native operations; callers never touch foreign
  windows or discover/reparent/kill HWNDs.
- `write(text)` queues ordered UTF-8-sized output, limited to 1 MiB including the
  single in-flight chunk. A rejected write raises `BufferError` without accepting
  its bytes. Resume the producer only after capacity becomes available; no VT
  payload is intentionally dropped. xterm's write callback acknowledges sequence
  completion. The GUI operation queue also has explicit bounded backpressure.
- `set_mode('full'|'mini')`, `set_theme(theme)` enqueue host changes. Mode and
  drag-layout epoch commit only after successful mode enqueue; queue overflow
  leaves them unchanged, so an explicit retry after capacity returns is delivered.
  Pre-run mode changes and repeated same-mode no-ops retain their existing behavior.
- `set_drag_regions(epoch, cols, rows, regions)` accepts at most 64 rectangles
  `(column, row, width, height)` for **allowed background/Spectrum cells only**.
  The controller must exclude controls and revoke all grants before a modal or
  cell-ownership change. Resize/mode changes revoke old grants. Stale epochs,
  malformed and out-of-grid rectangles are rejected. There is no broad terminal
  easy-drag. Header controls are excluded from header-background dragging.
- `finish_close()` is the future controller's stop/drain ACK, not a stop request.
  Native and header close request `quit` once and cancel disposal until that ACK.
- `snapshot()` returns lifecycle, geometry/identity, layout epoch and buffered byte
  counts, never terminal contents, prompts, environment or commands.

Callbacks are `on_input(text)`, `on_resize(cols, rows)` and
`on_action('ready'|'toggle'|'quit'|'host_failed')`. Callbacks must return promptly
and must not wait for the GUI thread. Tab and Shift-Tab are terminal input; header
buttons retain semantic labels and visible keyboard focus. Unicode follows the
normal terminal input/output path. Controller stop and music preservation are not
implemented here.

## Browser/process boundary (Risk item 2)

The adapter pins pywebview 6.2.1 and rejects any renderer other than
`edgechromium` before Form construction. It temporarily replaces only the process
module's `Chromium.EdgeChrome` binding with a helper-owned subclass and restores
that binding on return. It does not modify installed packages.

Virtual handlers are installed before Core initialization. Core-ready does **not**
call the upstream callback: native navigation/resource/popup/download/permission
and certificate guards plus settings are established before `NavigateToString`.
Only the exact pending helper HTML document is admitted once. Its UTF-8 length
and SHA-256 fingerprint (including the per-session CSP nonce) are bound before
NavigateToString. HTML data URIs must have an explicit text/html MIME type, either
UTF-8/default encoding with strict percent decoding or validated base64, and exact
decoded identity. URI length is bounded before decoding; decoded bytes must match
both expected length and digest. A bare about:blank cannot prove identity and is
canceled, as are other documents, replays and frames. Rejected attempts do not
consume the pending grant; a second load cannot rearm it. New windows are handled
without the default external-browser side effect; downloads are canceled;
permissions denied; resource responses are 403; frames and later navigation are
denied. Default dialogs, context menus, host objects, browser accelerator keys,
devtools and remote debugging are disabled. API availability is checked against
runtime CLR metadata; `CoreWebView2PermissionState.Deny` and
`Environment.CreateWebResourceResponse` were reached on this machine.

All scripts/styles are embedded, hash-verified offline assets. CSP denies network,
images, frames, forms, media and objects. There is no HTTP server or CDN. The
upstream general-purpose pywebview bridge is not injected: a private WebMessage
handler accepts only bounded terminal/controller primitives. Output is JSON DATA
to `terminal.write`, never HTML or eval. The private task-owned temporary browser
profile is not a user's default browser profile. On disposal the adapter observes
only its Core-owned browser process exit; it never kills a process. The native
process handle is opened while Core is live, before disposal, and held across
teardown. The GUI disposal callback does not wait: the finite exit wait runs only
after the GUI loop returns. Observed browser exit is recorded separately from GUI
capability success. Profiles are retained for parent-owned cleanup, never deleted
while browser exit may be uncertain.

## Optional runtime (Risk item 5)

Capability runtime: `.validation/windows-desktop/venv`, with parent-prepared
pywebview 6.2.1/pythonnet 3.2.1 and Edge WebView2. Tests run in the separate
`.validation/windows/venv` environment. Offline assets are supplied explicitly from
`.validation/windows-desktop/assets`; their provenance and MIT notices remain
there. Nothing is vendored into the package and no dependency installer runs here.

## Observed verification and limitations

Current S8 host foundation has passed its full finite native self-check; independent
B1/B2 recheck and integrated S8/S9/S12 delivery remain pending. Earlier results
below are retained history, not a claim of product completion or human approval.

Original focused tests: 13 passed. The first preimplementation run timed out at
30 seconds, so there is no original test-first RED collection result. The first
implementation run had 12 passed/1 failed (test expected an unescaped `>` although
serialization escapes both delimiters); its expectation was corrected.

The later human-authorized single additional batch has genuine RED evidence:
3 failed/13 passed in 2.33 seconds, covering initialization navigation consuming
the payload grant and startup mouse messages dereferencing an absent backend.
Post-batch GREEN: 16 passed in 0.51 seconds. The original RED timeout remains a
timeout; this later evidence does not rewrite it.

The finite no-player native command is:

```
.validation/windows-desktop/venv/Scripts/python.exe .validation/windows-desktop/host-evidence/native_probe.py
```

The original three native attempts failed (exit 1); exact evidence is under
`.validation/windows-desktop/host-evidence/`:

1. 15.468 s: `host_failed`; temporary profile cleanup raised WinError 32. The owned
   browser was not yet observed exiting. That profile path is retained in the
   first-attempt evidence for parent cleanup, not silently deleted.
2. After correction 1 (owned browser-exit wait and failure-stage evidence),
   10.750 s: native capability sequence incomplete; guards-before-payload,
   permission enum and resource API reached; owned browser exit observed.
3. After correction 2 (pinned message accessor, canceled-navigation tracking and
   safe failure diagnostics), 19.660 s: internal probe deadline; **terminal ready
   never arrived**. Guards-before-payload and owned browser exit observed; no
   underlying initialization exception was recorded.

## Single additional batch and fourth native failure

The human-authorized continuation used exactly one additional coherent batch,
not a reset of the original two corrections. No fresh native baseline was run.
Static inspection confirms installed `webview/window.py`'s generic `evaluate_js`
uses eval, but current H1 never calls that wrapper: it already uses fixed
`ExecuteScriptAsync` source plus JSON data and a private WebMessage receiver.
Thus the generic-eval hypothesis was **not established as the readiness cause**;
CSP was not weakened and unsafe-eval was not enabled.

The deterministic ordering defect is fixed: the payload-navigation grant is now
armed immediately before guarded `NavigateToString`, not during base/Core
initialization. This removes a verified bad event ordering, but the native result
does **not** establish it as the original runtime root cause. Readiness no longer
waits on a compositor animation-frame grant. A first nonce-authorized script
reports only fixed bootstrap/error/CSP stages and numeric lines before loading
vendor scripts; no error messages, URI samples or terminal contents are logged.
GUI output/control work waits for terminal-ready. Startup drag/resize messages
have lifecycle and non-None backend guards. The probe uses a typed factory with
`unittest.mock.patch.object`, rather than assigning an unknown module attribute.

Attempt 4 (the sole post-batch confirmation) exited 1 after 21.910 seconds:
`watchdog deadline; owned close requested`, phase 0. It recorded renderer
`edgechromium`, guards-before-payload, permission/resource API reachability,
`owned_form_disposed=true`, `watchdog_joined=true`, and `gui_loop_returned=true`.
It did **not** record payload-navigation admission/completion, document-bootstrap,
terminal-ready or any subsequent capability phase. Crucially,
`browser_exit_observed=false`: browser descendant exit is **uncertain**, not a
cleanup PASS. No signals, process discovery, second native confirmation or source
correction followed. This attempt's temporary profile was created only inside
`.validation/windows-desktop/host-evidence`; the old external failed profile was
not inspected, modified or deleted. The temporary-directory context returned
without a reported cleanup exception, but that is not browser-exit evidence.

Current evidence is `native-result.json`; original final failure is preserved as
`native-attempt-3.json`, alongside attempts 1–2. Four native failures and three
total correction batches remain in history (two original, one newly authorized).
No terminal-ready, full-mini-full identity/geometry/topmost, actual security-event
behavior, input/output ACK or acknowledged-close PASS is claimed. Simulated
handlers and runtime API reachability do not prove browser event delivery. Actual
held-click dragging, DPI, focus, controller/daemon/music and final visual remain
pending. No H2 source was accessed or integrated.

The extra allowance is spent. Remaining work is to reconcile this attempt's
owned browser exit, then identify why guarded `NavigateToString` yields no recorded
navigation or bootstrap event. Any continuation requires explicit authorization:
same H1 writer, these same H1 paths, one diagnostic/fix batch and one focused/native
confirmation with the original watchdog/30-second tool timeout, plus AST/whitespace
checks. It must not touch the retained external profile or bypass runtime guards.
This proposal is not authorization. S8 host part remains partial; S9 is pending.

## L95 bounded continuation (in progress)

The recorded human approval authorizes at most two new cohesive batches and one
finite native confirmation per batch; previous counts above remain spent. The
first batch opens and retains the Core-owned browser process handle before
teardown, moves its exit wait off the GUI shutdown path, and retains profiles.
Fixed `html_stage` checkpoints distinguish HTML argument evaluation, load entry,
and NavigateToString call/return; navigation reports only a fixed category, never
a URI or document payload. These checkpoints diagnose readiness without weakening
CSP or exposing the general-purpose bridge. New results use exclusive
`native-continuation-N.json` artifacts; historical results remain unchanged.

Batch 1: regression RED 2 failed/15 passed; GREEN 17 passed; AST/compile and
owned whitespace passed. Its sole native confirmation exited 1 after 18.409 s,
phase 0, with `failure_stage=core_init`, `failure_type=RuntimeError` (unsuccessful
Core initialization). Form disposal, GUI return and watchdog join were observed,
but no owned browser handle was captured and browser exit remains unproven.
`native-continuation-1.json` records the retained evidence-local profile. No second
batch or native confirmation is permitted while that ownership is uncertain.
New usage: 1/2 batches, 1/2 native confirmations; historical usage remains three
correction batches and four failed native runs. Native startup, lifecycle and S9
visual delivery remain incomplete; unit success is not native capability proof.

### Remaining-batch preparation

Parent subsequently supplied fresh scoped process reconciliation (exit 0, all
known profile/probe/marker counts zero), resolving the known-profile liveness hold
at that boundary, not retroactively proving browser exit in the failed run.
Read-only metadata verifies M: is a fixed local ReFS volume, not SUBST/network;
canonical clone and evidence paths remain unchanged. No canonical-path correction
is established. ReFS incompatibility remains unproven, and the discarded SDK
InitializationException type/HResult cannot be recovered from the last artifact.
No speculative source fix or diagnostic-only native replay followed. Total new
usage stays 1/2 batches and 1/2 confirmations. Exact preparation evidence and the
missing-proof/continuation boundary are in
`.validation/windows-desktop/host-evidence/remaining-batch-preparation.md`.

### Final authorized batch: failure reporting

The parent clarified that preserving SDK initialization errors is itself the
remaining diagnosed repair. Failed Core initialization now records null presence,
bounded CLR exception type, signed HResult and eight-digit unsigned hex, never
exception messages/stacks/Data/ToString. It remains fail-closed. The existing GUI
timer already dispatches reserved owned-close before checking guards/readiness;
a regression pins that ordering and verifies queued output stays unexecuted.
The helper records the first host-fatal stage/metadata and elapsed time when the
callback arrives, retaining FAIL and every existing native PASS requirement.
The sole final confirmation writes exclusively to `native-continuation-2.json`.
This repair does not establish the underlying initialization cause or any ReFS
restriction; no relocation, browser flags, fallback or security weakening occurs.

Final batch RED: 2 failed/17 passed; GREEN: 19 passed. AST/compile7 and owned
whitespace/diff passed. Sole final native confirmation exited 1 after 18.371 s,
phase 0/internal deadline. This run initialized Core, captured the browser handle,
armed guards and returned from NavigateToString; navigation category was `data`,
with no payload admission/bootstrap/terminal readiness recorded. No unsuccessful
SDK initialization callback was recorded, so no SDK failure type/HResult exists
for this run. Owned Form disposal, GUI return, watchdog join and actual held-owned
browser-process exit were observed. Retained profile: `gentle-stream-webview-hrofu5us`.
New allowances are exhausted: 2/2 source batches and 2/2 native confirmations;
historical three corrections/four native failures remain unchanged. No further
guard change, run, research or cleanup followed. Native capability and S9 remain
incomplete; exact final evidence is `native-continuation-2.json`.

## L107 separately authorized admission correction

The human authorized up to two additional cohesive batches, each with at most one
post-fix native confirmation. Historical three corrections/four failures and the
prior continuation's 2/2 batches/2/2 failures remain spent. The first additional
batch replaces the about:blank-only scheme assumption with exact pending-document
identity validation, not a generic data-URI grant. Guards still precede HTML; CSP,
frame/resource/popup/download/permission/certificate denial and the private bridge
remain unchanged. No profile relocation, new browser flags or installed-library
changes. The first additional result uses exclusive `native-additional-1.json`.

Additional batch 1: RED4 failed/17 passed, GREEN21 passed; scoped AST/compile3,
owned whitespace4 and diff checks passed. Its sole native confirmation exited 1
at phase 6 after 3.878 s: `full geometry not restored`. Exact document admission,
bootstrap/terminal readiness (151x42), bridge absence, input/output ACK, actual
navigation cancellation/popup handling/CSP fetch denial, and same-identity fixed
420x220 topmost frameless mini were observed. Full restoration and ACK-close were
not proven. Owned browser exit, Form disposal, GUI return and watchdog join were
observed; profile `gentle-stream-webview-_u4y5ngs` is retained unchanged.
This new geometry blocker is outside the narrowly authorized admission mismatch,
so no second correction or native replay followed. Additional usage remains 1/2
batches and 1/2 confirmations, separate from all prior spent allowances. This is
still partial S8 host proof, not S9 visual delivery or independent verification.

### L110 final remaining geometry batch

The scope amendment uses the existing last additional batch, not a new allowance.
Read-only screen metadata reports primary bounds1366x768/work area1366x720.
The deterministic regression reproduces the asymmetry: a 1100x700 initial Form at
cascade origin104,104 is captured before clamping, whereas full restoration clamps
it to y20. The first GUI tick now applies the existing safety clamp to the actual
owned Form before its first snapshot or queued mode capture. No expected probe
baseline, mode dimensions, DPI scaling, saved mini position, constraints, restore
clamp or equality assertion is weakened. The regression uses public mode commands
and checks actual fake-Form geometry, identity, mini fixed/topmost behavior, full
restore and remembered mini position. This reproduces a concrete defect; the
previous failed run did not record its actual full bounds, which remain unknown.
The final confirmation records only bounded expected/actual full geometry and
uses exclusive `native-additional-2.json`; every prior artifact is preserved.

Final remaining batch RED1 failed/21 passed; GREEN22 passed. Scoped AST/compile3,
owned whitespace4 and diff passed. The sole final native confirmation exited 1
after11.497 s: `owned browser exit not observed`. Exact full geometry equality
passed with expected/actual26,26,1084,661, unchanged Form/WebView/HWND identity and
restored topmost=false. All earlier terminal/ACK/mini/security phases passed;
close was canceled before ACK, then Form disposal/GUI return/watchdog join were
observed with lifecycle ready,quit. The held owned-browser exit wait returned
false: browser exit is uncertain, never cleanup success. Profile
`gentle-stream-webview-ensemud1` is preserved. ADDITIONAL2/2 source batches and2/2
confirmations are exhausted; historical3 corrections/4 failures and prior2/2
batches/2/2 failures remain spent. No further fix/probe/research/cleanup followed.
The component remains partial and cannot proceed to verification/integration.

## L115 combined B1/B2 continuation

The human authorized up to two new combined cohesive batches, one finite native
confirmation per batch, without resetting prior spent counts. B1 reorders mode
commit/invalidation after the existing bounded send under the existing lock; no
queue-capacity change, coalescing or recovery mechanism. Durable public-call
regression fills256 theme operations, verifies exact rejection/state preservation,
drains through fake GUI ticks, and proves explicit mode retry reaches the GUI.

B2 read-only inspection of pinned SDK1.0.3856.49 confirms WebView2.Dispose calls
its controller-close/unsubscribe path; Core/Environment/resource-response wrappers
have no public Dispose. Private SDK methods were inspected as metadata only, never
invoked. Acknowledged owned-close now detaches exactly our six Core handlers,
disposes the owned control on the live GUI/parent before Form close, and releases
the adapter/Form control roots. The upstream FormClosed cleanup repeats as an
idempotent no-op. No forced GC, private SDK-field mutation, longer wait or process
signal is used. The held Core-owned process handle and original post-GUI bounded
exit observation remain the sole browser-exit proof, not disposal/reference flags.
This repairs concrete lifetime ordering/root retention; the prior native failure
alone does not establish a leak or the underlying browser-exit cause.

The first combined result uses exclusive `native-combined-1.json`. Native PASS
requirements remain unchanged. S12 requires a later real integrated player-window
human trial and approval; this component's dummy terminal probe cannot satisfy it.

Combined batch 1 RED2 failed/23 passed; GREEN25 passed. AST/compile6 and owned
whitespace7/scoped diff passed. The sole full native confirmation PASSED exit0
in10.660 s: ready151x42, private bridge absent, input/output ACK, actual navigation
cancellation/popup handling/CSP fetch denial, same Form/WebView/HWND identity,
fixed420x220 topmost frameless mini, full geometry equality26,26,1084,661 and restored
topmost=false. Native close canceled before ACK, then Form disposed, GUI returned,
watchdog joined and the held owned-browser process actually exited. No wait was
extended or disposal flag substituted for process observation. Retained profile:
`gentle-stream-webview-di639lv3`. COMBINED1/2 batches and1/2 confirmations used;
all prior7 correction batches/8 failed native confirmations remain spent. No second
combined batch is needed for current own checks. Parent owns targeted B1/B2
independent recheck, integration, later runtime proof and S12 human trial. The mapper
runtime incident remains separate; this worker inferred no exit/clearance from it.
