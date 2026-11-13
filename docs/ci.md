# Device-free CI on Linux, macOS, WSL and native Windows

[Tests](../.github/workflows/tests.yml) validates pull requests targeting `main`
or `feat/cross-platform-integration`, manual `workflow_dispatch` runs, and pushes
**only to `main`**. A final CI branch push does not duplicate its pull-request
run. Functional PRs are already assembled; this workflow is the final matrix,
not a per-functional-PR validation loop. Concurrency cancels obsolete runs for
the same PR/ref. Configuration and local checks are not hosted PASS evidence.

## Matrix and locked setup

| Job | Runner | Setup |
| :--- | :--- | :--- |
| Linux | `ubuntu-24.04` | APT `libportaudio2`; native uv 0.11.14 |
| macOS | `macos-15` | Homebrew `portaudio`; native uv 0.11.14 |
| WSL | `windows-latest` | Ubuntu-24.04 / WSL 2; `libportaudio2`, `python3-venv`, `ca-certificates`; Linux uv 0.11.14 |
| Native Windows | `windows-latest` | Native uv 0.12.10; managed Python 3.13; Windows sounddevice wheel |

The POSIX jobs install managed Python 3.13 from `.python-version`, then use
`uv sync --frozen --python 3.13` and `uv run --frozen --no-sync`. The default dev
group supplies pytest, pytest-asyncio and pyfluidsynth. CI never regenerates
`uv.lock`. Native Windows uses the existing reference installation command:

```text
uv sync --python 3.13 --frozen --no-install-project --no-build
```

POSIX preflight imports sounddevice and reports PortAudio's version; this can
initialize the host backend, but the tests use fake streams, not an output
device. Native preflight uses `importlib.metadata` without importing sounddevice
or fluidsynth, and asserts `sys.platform == "win32"` and Python 3.13.
OS packages and Python patch releases can change; this is not a hermetic image.

## Correctness and performance selection

Every compatible POSIX job runs the **full default correctness suite**, without
file ignores or keyword exclusions:

```bash
uv run --frozen --no-sync pytest
uv run --frozen --no-sync pytest --collect-only -q -m performance tests/test_genres_contract.py
```

The first command includes **all of `tests/test_pause_output.py`**, including its
real-pipe, owned fake-sink integration tests against the assembled output host
on Linux. It also discovers all four renamed UI modules and their 28 contracts.
There is no reduced Linux pause selection or exemption for timing assertions.

Only `performance` is deselected by default through `pyproject.toml`. Functional
shape, finite/level, seam and determinism assertions remain enabled for every
genre and available backend. The second command explicitly selects benchmarks
for **collection only**, not execution or a throughput claim.

The strict `>10×` benchmark retains its eight-bar measured render block and
measured-factor failure message. Explicit benchmark execution remains available:

```bash
uv run --frozen --no-sync pytest -m performance tests/test_genres_contract.py
```

Explicit `-m` replaces the default expression; user `-m`/`-k` behavior is retained.
Synthetic selection probes use tiny offline bars and a ten-second child timeout,
exclude `trap` when selecting the single `lofi` case, and do not time real renders.
[Upstream #3](https://github.com/Gentleman-Programming/gentle-stream/issues/3)
reports constrained-host performance; no threshold override is applied here.
Existing sampled-backend prerequisite skips remain: no soundfont, FluidSynth
shared library or sample download is provisioned. Read actual skip/deselection
counts; unknown functional failures must fail visibly, not become new skips.
No retries, `continue-on-error`, or blanket test exclusions are configured.

## Native Windows selection and local portability

The native job runs the reference portable/native contracts with the assembled
UI names. Its main pytest selection is:

```text
tests/test_native_windows.py tests/test_daemon.py tests/test_bridge.py tests/test_voice_free_delivery.py tests/test_music_panic.py tests/test_ui_toolbar_contracts.py tests/test_ui_save_contracts.py tests/test_ui_picker_contracts.py tests/test_ui_transport_contracts.py tests/test_genres_contract.py tests/test_performance_selection.py
```

The four `test_ui_*_contracts.py` modules replace the reference's old
`test_named_playlist_ui.py`: toolbar 8, save 6, picker 8, transport 6 tests.
The bridge fixture already uses Python rather than a POSIX shebang on Windows.
Daemon tests retain existing skips for POSIX permissions/socket inodes and
catchable signals; native contracts cover authenticated IPC, singleton/stale
recovery, private files, owned process trees and panic/session preservation.
The fixture owns `Popen` handles, waits/terminates only its children, tolerates
only `ProcessLookupError` at the exit race, and retains runtime evidence.
Mocked regressions cover live teardown, exit race/already exited, and unrelated
`PermissionError` propagation without signaling real processes or deleting data.

A separate native pytest step runs **only**:

```text
tests/test_pause_output.py::test_host_pause_delivers_stop_with_full_pending_queue
```

That portable PR17 regression mocks child creation and pipes before host entry.
The remainder of that module requires POSIX executable fake sinks and SIGTERM
semantics; it is not claimed compatible with native Windows. Benchmark collection
runs separately on native Windows too. This is intentionally not a native
full-suite claim, nor inherited proof from earlier slice runs.

### Plain `uv run pytest` on native Windows

Beyond the selective CI matrix steps, running plain `uv run pytest` (or `pytest`)
directly on native Windows passes cleanly without requiring CI environment
variables or manual file filtering:

- **Screenshot UTF-8 encoding**: `tests/test_appearance.py` writes screenshot SVG
  artifacts with explicit `encoding='utf-8'`, preventing `UnicodeEncodeError` under
  Windows default locale encodings (such as `cp1252`).
- **Runtime temp directory fallback**: The `runtime` fixture in `tests/test_daemon.py`
  defaults to `tempfile.gettempdir()` when `GENTLE_STREAM_TEST_TEMP` is unset or
  empty, so local Windows runs automatically use the OS temporary directory instead
  of failing on `/tmp`.
- **WSL and POSIX skips**: WSL-host POSIX pipe and adapter modules
  (`tests/test_windows_audio.py`, `tests/test_windows_audio_diagnostics.py`,
  `tests/test_windows_audio_independent.py`, and `tests/test_windows_shell_adapter.py`)
  declare module-level `pytest.mark.skipif(sys.platform == "win32", ...)` skips.
  In `tests/test_pause_output.py`, the 15 POSIX fake-sink and pipe tests skip
  cleanly on `win32` via the `owned_output` fixture, while the portable PR17
  regression (`test_host_pause_delivers_stop_with_full_pending_queue`) remains
  active and passing on native Windows.

## Scratch, WSL and permissions

Native Windows creates a unique owned directory under `RUNNER_TEMP`. POSIX jobs
use a fresh short `/tmp/gs-ci-XXXXXX` root, as in the reference, to respect macOS
AF_UNIX path limits. Each job **creates** its own temp subdirectory and exports
`GENTLE_STREAM_TEST_TEMP` to it; tests do not assume a pre-existing custom POSIX
runtime root. HOME, XDG state, managed Python, venv, dependency caches and pytest
cache/basetemp are isolated outside the checkout. Daemon and selection evidence
is retained; runner disposal reclaims scratch, not workflow cleanup commands.
Do not run this provisioning script on a personal machine.

WSL uses pinned `setup-wsl` with `wsl-bash {0}`. It verifies Linux, a Microsoft
kernel and Ubuntu 24.04. The checkout is copied into the owned Linux scratch
filesystem before tests, avoiding DrvFS socket, mode-bit and executable issues.
A bootstrap venv installs the official binary `uv==0.11.14` wheel; Windows-host
setup-uv is not used for WSL. Environment exports happen inside Linux.

Repository permissions are only `contents: read`; checkout has
`persist-credentials: false` and `clean: false`. There are no custom credentials,
privileged PR events, uploads, publishing or write-back. Three POSIX matrix jobs
have a 30-minute limit and maximum parallelism three; native Windows has a
15-minute limit. `fail-fast: false` retains each platform's failure evidence.
Dependency caches are not persisted between runs.

## Existing action pins

| Action | Reference | Commit |
| :--- | :--- | :--- |
| `actions/checkout` | v4 | `11d5960a326750d5838078e36cf38b85af677262` |
| `astral-sh/setup-uv` | v6 | `d0cc045d04ccac9d8b7881df0226f9e82c39688e` |
| `Vampire/setup-wsl` | v6 | `887f39deb6c0976365e546926fe66f41b77d65ff` |

These pins and uv versions are reused from the public reference, not upgraded.
Top-level pins do not pin all transitive dependencies or OS packages.
See [workflow syntax](https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions)
and [uv sync](https://docs.astral.sh/uv/concepts/projects/sync/).

## Required hosted readback

Before completion, verify Linux, macOS, WSL and native Windows actually execute
for the final candidate commit. Read setup output and pytest counts, including
the complete Linux real-pipe pause module and the separate native mocked queue
case. WSL unavailability must fail visibly, never fall back under a WSL label.
No current hosted result is asserted by this document.

Device-free results do not prove audible output, acoustic pause latency, device
underflows, real-terminal rendering, provider access, sampled instruments or
real Windows/WSL audio interop. Keep such evidence separate from CI status.
