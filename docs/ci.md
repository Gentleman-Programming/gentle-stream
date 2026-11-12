# Test commands

Run genre correctness separately from hardware-sensitive render throughput. These commands describe the current pytest configuration, not a restored hosted CI matrix; no workflow changes are included in this split.

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
