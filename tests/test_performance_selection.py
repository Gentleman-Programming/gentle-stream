"""Exercise public pytest selection and genre assertions with tiny offline bars.

Fixtures are retained under GENTLE_STREAM_TEST_TEMP when supplied, or a fresh
system temporary directory. No real render is timed.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Loaded before collection: no soundfont discovery, audio, providers or downloads.
CONFTEST = '''
from types import SimpleNamespace
import numpy as np
import pytest
from gentle_stream import samples
samples.soundfont_path = lambda: None

@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items):
    for item in items:
        module = item.module
        if not hasattr(module, "BARS"):
            continue
        calls = []
        class Engine:
            def render_bar(self):
                calls.append(1)
                x = np.tile(np.array([[0.1, -0.1], [-0.1, 0.1]]), (4, 1))
                if MODE == "nonfinite":
                    x[0, 0] = np.nan
                elif MODE == "clipped":
                    x *= 20
                elif MODE == "silent":
                    x *= 0
                elif MODE == "nondeterministic":
                    x *= 1 if len(calls) <= 8 else 0.5
                elif MODE == "seam":
                    x[:] = 0.1 if len(calls) % 2 else -0.1
                return x
        module._engine = lambda *args, **kwargs: Engine()
        module.SR = 8
        ticks = iter((0.0, ELAPSED))
        module.time = SimpleNamespace(perf_counter=lambda: next(ticks))

@pytest.fixture(autouse=True)
def verify_inputs_unchanged():
    yield
    assert open("synthetic.json").read() == '{"songs": [], "counter": 0}'
'''


def run_case(args, *, elapsed=100.0, mode="clean"):
    base = os.environ.get("GENTLE_STREAM_TEST_TEMP")
    directory = Path(tempfile.mkdtemp(prefix="render-selection-", dir=base))
    shutil.copyfile(ROOT / "pyproject.toml", directory / "pyproject.toml")
    shutil.copyfile(ROOT / "tests/test_genres_contract.py", directory / "test_genres_contract.py")
    (directory / "conftest.py").write_text(
        f"MODE = {mode!r}\nELAPSED = {elapsed!r}\n" + CONFTEST, encoding="utf-8")
    (directory / "test_other.py").write_text(
        "import pytest\n@pytest.mark.other\ndef test_other():\n    assert True\n", encoding="utf-8")
    state = directory / "synthetic.json"
    state.write_text('{"songs": [], "counter": 0}', encoding="utf-8")
    before = state.read_bytes()
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTEST_ADDOPTS="")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(ROOT / "src"), env.get("PYTHONPATH"))))
    command = [sys.executable, "-B", "-m", "pytest", "-q", "--tb=short",
               "-p", "no:cacheprovider", "-p", "pytest_asyncio.plugin", "-o",
               "markers=performance: render speed\nother: selection sentinel", *args]
    result = subprocess.run(command, cwd=directory, env=env, capture_output=True,
                            text=True, timeout=30, executable=sys.executable)
    (directory / "result.json").write_text(json.dumps({
        "command": command, "exit": result.returncode,
        "stdout": result.stdout, "stderr": result.stderr,
    }, indent=2), encoding="utf-8")
    assert state.read_bytes() == before
    assert result.stderr == ""
    assert "warning" not in result.stdout.lower(), result.stdout
    return result


def assert_summary(result, exit_code, summary):
    assert result.returncode == exit_code, result.stdout
    assert re.search(rf"{summary} in [0-9.]+s", result.stdout), result.stdout


def test_default_runs_functional_contract_without_clock_gate():
    result = run_case(["test_genres_contract.py", "-k", "renders_cleanly and lofi and procedural"])
    assert_summary(result, 0, r"1 passed, \d+ deselected")


@pytest.mark.parametrize("elapsed,exit_code,factor", [(0.5, 0, None), (0.8, 1, "10.0"), (1.0, 1, "8.0")])
def test_explicit_performance_selection_keeps_strict_ten_x(elapsed, exit_code, factor):
    result = run_case(["-m", "performance", "test_genres_contract.py", "-k", "lofi and procedural"], elapsed=elapsed)
    assert_summary(result, exit_code, rf"1 {'passed' if exit_code == 0 else 'failed'}, \d+ deselected")
    if factor:
        assert f"only {factor}x real time" in result.stdout


@pytest.mark.parametrize("mode", ["nonfinite", "clipped", "silent", "nondeterministic", "seam"])
def test_default_still_rejects_broken_audio(mode):
    result = run_case(["test_genres_contract.py", "-k", "renders_cleanly and lofi and procedural"], mode=mode)
    assert_summary(result, 1, r"1 failed, \d+ deselected")


def test_user_marker_choice_and_keyword_selection_are_preserved():
    result = run_case(["-m", "other", "-k", "other", "test_other.py", "test_genres_contract.py"])
    assert_summary(result, 0, r"1 passed, \d+ deselected")
