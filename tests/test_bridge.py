import json
import os
import stat
import textwrap

import pytest

from gentle_stream.bridge import GentleShell, InterpretError
from gentle_stream.style import Style

MODELS_TABLE = """provider       model              context  max-out  thinking  images
claude-bridge  claude-haiku-5-5   1M       128K     yes       yes
openai-codex   gpt-6-luna         400K     128K     yes       yes
"""


@pytest.fixture
def fake(tmp_path):
    """A fake gentle-shell: records its argv and prints FAKE_STDOUT / FAKE_STDERR, exiting with FAKE_CODE."""
    log = tmp_path / "argv.json"
    script = tmp_path / "gentle-shell"
    script.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json, os, sys, time
        json.dump(sys.argv[1:], open({str(log)!r}, "w"))
        time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
        sys.stdout.write(os.environ.get("FAKE_STDOUT", ""))
        sys.stderr.write(os.environ.get("FAKE_STDERR", ""))
        sys.exit(int(os.environ.get("FAKE_CODE", "0")))
        """))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    def run(stdout="", stderr="", code=0, sleep=0):
        os.environ.update(FAKE_STDOUT=stdout, FAKE_STDERR=stderr, FAKE_CODE=str(code), FAKE_SLEEP=str(sleep))
        return script

    yield run, (lambda: json.loads(log.read_text()))
    for k in ("FAKE_STDOUT", "FAKE_STDERR", "FAKE_CODE", "FAKE_SLEEP"):
        os.environ.pop(k, None)


def test_interpret_turns_the_answer_into_a_style(fake):
    run, _ = fake
    answer = {"name": "Neon Rain", "key": "E", "mode": "minor", "bpm": 68, "progression": [1, 6, 3, 7],
              "chords": "rhodes", "lead": "nylon", "drums": "soft", "energy": 0.3}
    gs = GentleShell(binary=str(run(json.dumps(answer))))
    s = gs.interpret("lluvia de neón en Tokio a las 3am", Style(space=0.8))
    assert (s.name, s.key, s.bpm, s.chords, s.lead, s.drums) == ("Neon Rain", "E", 68.0, "rhodes", "nylon", "soft")
    assert s.mood == "lluvia de neón en Tokio a las 3am"   # the user's words, not the model's
    assert s.space == 0.8                                  # missing fields keep the current style


def test_command_runs_non_interactive_with_the_chosen_model(fake):
    run, argv = fake
    GentleShell(binary=str(run('{"name": "x"}')), model="claude-bridge/claude-haiku-5-5").interpret("calm", Style())
    args = argv()
    assert args[0] == "--" and "-p" in args and "--no-session" in args and "-nt" in args
    assert args[args.index("--model") + 1] == "claude-bridge/claude-haiku-5-5"
    assert "calm" in args[-1] and '"progression"' in args[-1]


def test_without_a_model_gentle_shell_uses_its_default(fake):
    run, argv = fake
    GentleShell(binary=str(run('{"name": "x"}'))).interpret("calm", Style())
    assert "--model" not in argv()


def test_json_inside_prose_or_a_code_fence_is_extracted(fake):
    run, _ = fake
    out = 'Sure! Here it is:\n```json\n{"name": "Fenced", "bpm": 90}\n```\nEnjoy.'
    s = GentleShell(binary=str(run(out))).interpret("x", Style())
    assert (s.name, s.bpm) == ("Fenced", 90.0)


def test_a_failing_gentle_shell_reports_its_error(fake):
    run, _ = fake
    gs = GentleShell(binary=str(run(stderr='Error: Model "nope" not found.', code=1)))
    with pytest.raises(InterpretError, match='Model "nope" not found'):
        gs.interpret("x", Style())


def test_an_answer_without_json_is_an_error(fake):
    run, _ = fake
    with pytest.raises(InterpretError, match="no JSON"):
        GentleShell(binary=str(run("I cannot help with that."))).interpret("x", Style())


def test_a_missing_binary_is_an_error():
    with pytest.raises(InterpretError, match="not found"):
        GentleShell(binary="/nonexistent/gentle-shell").interpret("x", Style())


def test_a_slow_answer_times_out(fake):
    run, _ = fake
    with pytest.raises(InterpretError, match="timed out"):
        GentleShell(binary=str(run('{"name": "x"}', sleep=3)), timeout=0.5).interpret("x", Style())


def test_list_models_reads_the_provider_model_table(fake):
    run, argv = fake
    models = GentleShell(binary=str(run(MODELS_TABLE))).list_models()
    assert models == ["claude-bridge/claude-haiku-5-5", "openai-codex/gpt-6-luna"]
    assert "--list-models" in argv()
