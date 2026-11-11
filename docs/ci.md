# Test commands

Run genre correctness separately from hardware-sensitive render throughput. The Linux workflow runs the current suite with the same default pytest selection described below.

## Correctness (default)

```bash
uv run pytest tests/test_genres_contract.py
uv run pytest tests/test_performance_selection.py
```

The default `-m 'not performance'` filter retains finite-audio, level, seamless-boundary and seed-determinism assertions. The selection regressions use tiny synthetic bars and a controlled clock, not a real throughput measurement. Sampled-backend checks skip when FluidR3_GM or FluidSynth is unavailable; nothing is downloaded by these commands.

## Opt-in throughput

```bash
uv run pytest -m performance tests/test_genres_contract.py
```

This benchmark renders eight bars per registered genre/backend and requires strictly greater than 10× real time. Its threshold and failure message are unchanged. A failure measures that machine's throughput; it is not a correctness failure.

To inspect benchmark selection without running it:

```bash
uv run pytest --collect-only -q -m performance tests/test_genres_contract.py
```

Explicit `-m` expressions replace the default filter (for example, `-m ""` selects all markers). `-k` remains an additional keyword filter, so `-k` alone does not opt into performance tests. The broader suite remains available with `uv run pytest`; audio/device and model-provider integration are not established by the focused commands above.

## Linux CI

`.github/workflows/tests.yml` defines one correctness job on `ubuntu-latest`.
It runs on pushes and pull requests targeting `main`,
`feat/cross-platform-integration`, or `ci/chain-02-linux-tests`. Pull-request
branch filters match the **base** branch; pushing the slice branch also checks
it before a review PR exists. There are no path filters that suppress checks.

The job uses public checkout and uv setup actions pinned to commit hashes.
Its token has only `contents: read`, and checkout does not persist credentials.
There are no secrets, publishing steps, repository writebacks, privileged PR
triggers, or chained workflow triggers. macOS, WSL and native Windows jobs are
not part of this unit.

Ubuntu installs only the PortAudio shared library (`libportaudio2`); no audio
hardware is configured. uv 0.11.14 installs managed Python 3.13, matching
`.python-version` and the Python requirement in `pyproject.toml`/`uv.lock`.
The environment is synchronized from the existing lock without updating it:

```bash
uv python install 3.13
uv sync --frozen --python 3.13
uv run --frozen --no-sync pytest
```

`uv sync` includes the project's default development dependency group, including
pytest. The job checks the Python version, Linux platform and PortAudio library
before pytest; it does not open an audio device. It runs the entire current
suite with no file list, `-k` exclusion, or override of the default
`not performance` marker expression. Functional genre coverage therefore stays
selected; the strict opt-in throughput benchmark remains unchanged.

The job keeps its managed Python, virtual environment, uv and pytest caches,
HOME, XDG directories and normal test temporary state in a fresh directory under
`RUNNER_TEMP`, not in the checkout. The existing daemon fixture independently
uses short private `/tmp/gs-*` runtime directories for Unix sockets and fake
audio; its existing teardown behavior is unchanged. The workflow adds no
cleanup or artifact-upload step, and the runner is disposable. No soundfont,
media, model provider or personal library is provisioned. Existing optional
sample prerequisites may still cause skips; this is not sampled-backend or
real-device acceptance.

The job has a 30-minute timeout and cancels superseded runs of the same PR/ref.
A green hosted run is required evidence for this Linux job. Static YAML checks
or earlier local source-test results alone do not establish hosted Linux
compatibility, real-time throughput, live audio or model-provider integration.
