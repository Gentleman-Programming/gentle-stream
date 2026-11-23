# Community audio output selector
Port the original app’s user-facing output selector to the community checkout, without replacing the original app or importing unrelated features.

## Specs
S1. “agregalo” — select the app's audio output (the original uses `o`); make selection discoverable in Settings. Keep the system output unchanged and allow changing device without restarting the app.
S2. “fiajte que tiene la mia que la otra no” — compare locally, report other missing user-facing features; do not port those extras.

## Tasks
T1 | S2 | Static comparison of public CLI/TUI and genre registries | done | original fb6b61a vs community 2cabce3: original-only device selection, recorded sample pack, WAV moments and four registered genres.
T2 | S1 | Device discovery, native player switch, daemon/client state and recovery | done | 1568a87 + a3457e1; native review-d6d21f4eea8376c1 closed approved for duplicate-name restore and changed system default (68 tests). ab22d10 fixes stale-index rollback, pending mood invalidation and lost readiness JSON; seven independent local-socket/fake-sink checks passed on the frozen commit. Bounded correction 726c86e: 85 focused checks passed, but native targeted validator rejected (R3-001, R3-002, R4-001); not approved. Mixed-revision integrated suite failed one outdated diagnostic expectation; isolated 726c86e suite exited 0: 818 passed, 56 subtests passed, 40 skipped, 32 deselected. Functional checks do not supersede the validator rejection. 9a05366: rollback and Panic reopen an explicit output only when name and host API still match; a moved device needs a known host API and a unique name there; unverifiable outputs stay silent and never rewrite the preference. Startup errors keep the original cause, then each failed cleanup operation (stop, close) by operation rather than by text. 11 daemon-socket cases with fake sinks: 8 failed before, all pass. Independent verifier on 9a05366: S1 met, six hypotheses probed, no regression.
T3 | S1 | Textual selector, shortcut, Settings entry, docs and verification | done | 25cda60: narrow-header and duplicate-name regressions fixed. Independent headless UI/real IPC/fake sinks: 4 focused + 2 entry-point checks passed. Native review closed with non-blocking advisories. No physical audio or other OS verified.

## Log
L1. agregalo
L2. fiajte que tiene la mia que la otra no
L3. ok arregla y commit yb push
L4. Target: existing community feat/cross-platform-integration; no force, private profile excluded. Independent IPC diagnosis on 726c86e reproduced wrong-sink rollback when prior host API was unknown and two homonyms appeared. Also reproduced failure to restore a uniquely identifiable moved output and loss of secondary shutdown diagnostics. Do not publish this revision. Panic stale-index behavior predates this correction and is recorded separately.
L5. podes tratar de ver porque nos bloqueamos? no deberia
L6. ok apaga en este repo y sigue
L7. Native review of 726c86e ended escalated after the targeted validator rejected the single bounded correction. gentle-ai 5.0.0 offered no runnable exit from the host (status never returned recover; the intended-untracked selection rejected its own binding), reported as an occurrence on Gentleman-Programming/gentle-ai#6221. Per L6 the review switch is off for this clone only, so 9a05366 ships without native review; it was checked by the 11 residual cases, the full suite and an independent verifier instead.
L8. Known limits kept out of scope: an output whose host API is unknown both before and now is still reopened on the same index and exact name (pre-existing); a cleanup fault during ordinary shutdown after readiness still exits 1 with a traceback instead of a structured line (pre-existing). Only fake sinks were exercised: no physical playback and no other operating system.
