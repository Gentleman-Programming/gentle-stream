import json
import os
import stat
import textwrap

import pytest

from gentle_stream.bridge import GentleShell, InterpretError, Usage
from gentle_stream.style import Style

MODELS_TABLE = """provider       model              context  max-out  thinking  images
claude-bridge  claude-haiku-5-5   1M       128K     yes       yes
openai-codex   gpt-6-luna         400K     128K     yes       yes
"""

USAGE = {"input": 1069, "output": 14, "cacheRead": 200, "cacheWrite": 16400, "reasoning": 0, "totalTokens": 17683,
         "cost": {"input": 0.0001069, "output": 7e-06, "cacheRead": 0, "cacheWrite": 0, "total": 0.0001139}}


def jsonl(*parts, usage=USAGE):
    """What `gentle-shell -- -p --mode json` prints: one event per line, the answer in the assistant message_end."""
    events = [
        {"type": "session", "version": 3, "id": "s"},
        {"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "{\"name\": \"no\"}"}]}},
        {"type": "message_update", "usage": usage, "assistantMessageEvent": {"type": "text_delta", "delta": "{"}},
        {"type": "message_end", "message": {"role": "assistant", "content": list(parts), "usage": usage,
                                            "stopReason": "stop"}},
        {"type": "agent_end", "messages": []},
    ]
    return "".join(json.dumps(e) + "\n" for e in events)


def text(t):
    return {"type": "text", "text": t}


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
    gs = GentleShell(binary=str(run(jsonl(text(json.dumps(answer))))))
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
    assert args[args.index("--mode") + 1] == "json"


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


def test_cancel_kills_a_running_gentle_shell(fake):
    import threading
    import time

    run, _ = fake
    gs = GentleShell(binary=str(run('{"name": "x"}', sleep=30)))
    errors = []

    def call():
        try:
            gs.interpret("x", Style())
        except InterpretError as e:
            errors.append(str(e))

    t = threading.Thread(target=call)
    t.start()
    time.sleep(0.5)
    t0 = time.monotonic()
    gs.cancel()
    t.join(5)
    assert not t.is_alive() and time.monotonic() - t0 < 2
    assert errors == ["cancelled"]


def test_prompt_lists_every_genre_with_its_description(fake):
    from gentle_stream.genres import REGISTRY
    run, argv = fake
    GentleShell(binary=str(run('{"name": "x"}'))).interpret("calm", Style(genre="jazz"))
    prompt = argv()[-1]
    assert '"genre"' in prompt and '"genre": "jazz"' in prompt   # the current genre is part of the context
    for name, genre in REGISTRY.items():
        assert f"{name}: {genre.description}" in prompt
    assert "lofi is the default" in prompt
    assert "never reproduce" in prompt.lower()


def test_the_model_picks_the_genre_and_invalid_ones_keep_the_current(fake):
    run, _ = fake
    s = GentleShell(binary=str(run('{"name": "Blue", "genre": "Jazz"}'))).interpret("smoky club", Style())
    assert s.genre == "jazz"
    s = GentleShell(binary=str(run('{"name": "x", "genre": "polka"}'))).interpret("x", Style(genre="rock"))
    assert s.genre == "rock"


# ------------------------------------------------------------ token usage
def test_interpret_with_usage_reads_the_text_and_usage_of_the_assistant_message(fake):
    run, _ = fake
    out = jsonl({"type": "thinking", "thinking": '{"name": "Thought"}'},
                text('Sure!\n```json\n{"name": "Fenced",'), text(' "bpm": 90}\n```'))
    s, usage = GentleShell(binary=str(run(out))).interpret_with_usage("x", Style())
    assert (s.name, s.bpm) == ("Fenced", 90.0)        # text parts joined, thinking and the user message ignored
    assert usage == Usage(input=1069, output=14, cache_read=200, cache_write=16400, total=17683, cost_usd=0.0001139)


def test_plain_text_output_still_works_with_unknown_usage(fake):
    run, _ = fake
    s, usage = GentleShell(binary=str(run('{"name": "Old Shell"}'))).interpret_with_usage("x", Style())
    assert s.name == "Old Shell" and usage is None


def test_an_assistant_message_without_json_is_an_error(fake):
    run, _ = fake
    with pytest.raises(InterpretError, match="no JSON"):
        GentleShell(binary=str(run(jsonl(text("I cannot help with that."))))).interpret("x", Style())


def test_usage_formats_compactly_and_adds_up():
    a = Usage(input=1069, output=14, cache_read=200, cache_write=16400, total=17683, cost_usd=0.0001139)
    b = Usage(input=17000, output=700, total=17700, cost_usd=0.0014)
    assert a.format() == "17.7k tokens · $0.0001"
    assert Usage(total=850).format() == "850 tokens · $0.0000"
    assert Usage(total=1_250_000, cost_usd=1.5).format() == "1.25M tokens · $1.5000"
    total = a + b
    assert (total.input, total.output, total.total) == (18069, 714, 35383)
    assert total.cost_usd == pytest.approx(0.0015139)
    assert total.format() == "35.4k tokens · $0.0015"
