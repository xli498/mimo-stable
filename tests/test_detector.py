#!/usr/bin/env python3
"""Behavior contract tests for the dependency-free loop detector.

Run directly with the standard library (``python3 tests/test_detector.py``)
or via pytest when it is installed.
"""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

# Keep the direct ``python tests/test_detector.py`` entry point working in the
# same way as pytest collection from the repository root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mimo_stable.events import normalize_event, normalize_events
from mimo_stable.detect_loop import LoopDetector
from mimo_stable.policy import decide
from mimo_stable.runtime import inspect_events


def test_event_normalization_contract():
    assert normalize_event("plain output").text == "plain output"
    assert normalize_event({"type": "text", "text": " hello ", "timestamp": 1.5}) == normalize_event({"type": "text", "text": "hello", "timestamp": 1.5})
    tool = normalize_event({"type": "tool_call", "name": "exec", "arguments": {"b": 2, "a": 1}})
    assert '"name":"exec"' in tool.text
    assert '"_fingerprint":' in tool.text
    assert '"a":1' not in tool.text and '"b":2' not in tool.text
    assert normalize_event({"type": "assistant", "content": "answer"}).text == "answer"
    assert normalize_event({"type": "assistant", "content": [{"type": "text", "text": "answer"}]}).text == "answer"
    assert len(normalize_events(["one", {"type": "text", "text": "two"}])) == 2
    for bad in ({"type": "unknown", "text": "x"}, {"type": "text", "text": ""}, {"type": "text", "text": "x", "timestamp": True}, {"type": "text", "text": "x", "timestamp": float("nan")}, {"type": "tool_call", "name": "exec", "arguments": []}, {"type": "tool_call", "name": "exec", "arguments": "not-json"}, {"type": "tool_call", "name": "exec", "arguments": {"bad": object()}}):
        try:
            normalize_event(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid event accepted: {bad!r}")


def test_runtime_facade_contract():
    result = inspect_events(["same", "same", "same"], text_mode="instant")
    assert result["loop_detected"] is True
    assert result["policy"]["action"] == "stop_and_escalate"
    result = inspect_events(
        [
            {"type": "tool_call", "name": "read", "arguments": {"path": "a"}},
            {"type": "tool_call", "name": "read", "arguments": {"path": "b"}},
        ],
        text_mode="instant",
    )
    assert result["loop_detected"] is False
    assert result["policy"]["action"] == "continue"
    try:
        inspect_events([{"type": "unknown", "text": "x"}])
    except ValueError:
        pass
    else:
        raise AssertionError("unknown event was silently accepted")
    for kwargs in (
        {"repeat_threshold": 1},
        {"repeat_threshold": 2.5},
        {"repeat_threshold": True},
        {"time_threshold": -1},
        {"time_threshold": 0.5},
        {"time_threshold": True},
        {"similarity_threshold": 1.1},
        {"similarity_threshold": True},
        {"similarity_threshold": float("nan")},
        {"text_mode": "unknown"},
        {"expected_language": "en"},
        {"retry_count": True},
        {"max_tool_calls": True},
        {"max_tool_calls": 0},
        {"max_tool_calls": -1},
        {"max_tool_calls": 1.5},
    ):
        try:
            inspect_events([], **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid runtime option accepted: {kwargs!r}")
    try:
        decide({"loop_detected": False}, retry_count=True)
    except ValueError:
        pass
    else:
        raise AssertionError("policy accepted boolean retry_count")
    try:
        decide({"loop_detected": False, "details": []})
    except ValueError:
        pass
    else:
        raise AssertionError("policy accepted non-object details")
    try:
        inspect_events(iter(["x"]))
    except ValueError:
        pass
    else:
        raise AssertionError("non-list event collection accepted")
    for kwargs in ({"repeat_threshold": True}, {"time_threshold": 0.5}, {"similarity_threshold": float("nan")}, {"text_mode": "unknown"}, {"max_tool_calls": True}, {"max_tool_calls": 0}, {"max_tool_calls": 1.5}):
        try:
            LoopDetector(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"detector accepted invalid configuration: {kwargs!r}")


def test_new_signals_contract():
    def tool(name, arguments):
        return {"type": "tool_call", "name": name, "arguments": arguments}

    # A 2-step tool-call cycle needs two full repetitions: three calls are not enough.
    partial = inspect_events(
        [tool("exec", {"cmd": "ls"}), tool("exec", {"cmd": "pwd"}), tool("exec", {"cmd": "ls"})],
        text_mode="instant",
    )
    assert partial["loop_detected"] is False, partial
    cycle = inspect_events(
        [tool("exec", {"cmd": "ls"}), tool("exec", {"cmd": "pwd"}), tool("exec", {"cmd": "ls"}), tool("exec", {"cmd": "pwd"})],
        text_mode="instant",
    )
    assert cycle["loop_detected"] is True, cycle
    assert cycle["details"]["type"] == "tool_call_cycle", cycle
    assert cycle["details"]["period"] == 2, cycle
    # A read/write mix is productive repair work, not a loop.
    productive = inspect_events(
        [tool("read", {"path": "a.txt"}), tool("write", {"path": "b.txt", "text": "draft"}), tool("read", {"path": "a.txt"}), tool("write", {"path": "b.txt", "text": "draft"})],
        text_mode="instant",
    )
    assert productive["loop_detected"] is False, productive
    # Calls that differ only in volatile metadata are the same call.
    volatile = inspect_events(
        [tool("read", {"path": "a.txt", "request_id": f"req-{i}"}) for i in range(3)],
        text_mode="instant",
    )
    assert volatile["loop_detected"] is True, volatile
    assert volatile["details"]["type"] == "identical_tool_calls", volatile
    # A long block that repeats its own phrases is degenerate on its own.
    sentence = "The quarterly report shows steady growth across all regions today."
    long_block = " ".join([sentence] * 6)
    ngram = inspect_events([long_block], text_mode="instant")
    assert ngram["loop_detected"] is True, ngram
    assert ngram["details"]["type"] == "repeated_ngram_within_block", ngram
    assert ngram["details"]["occurrences"] >= 5, ngram
    short_block = " ".join([sentence] * 3)
    assert inspect_events([short_block], text_mode="instant")["loop_detected"] is False
    # The tool-call budget ceiling is opt-in and counts every call.
    many = [tool("read", {"path": f"file{i}.txt"}) for i in range(12)]
    assert inspect_events(many, text_mode="instant")["loop_detected"] is False
    capped = inspect_events(many, text_mode="instant", max_tool_calls=10)
    assert capped["loop_detected"] is True, capped
    assert capped["details"]["type"] == "tool_call_budget_exceeded", capped
    assert capped["details"]["total_tool_calls"] == 11, capped
    print("new signals tests passed")


def test_must_regressions_m1_to_m6():
    """Regression tests for review Must items M1-M6 (PR #3 review 2026-11-09).

    Each case pins the flipped behavior of the review's section-7
    reproducers: inputs that used to misfire must no longer fire, and inputs
    that used to be missed must now fire. Fixture files live in
    fixtures/must_*.log; the API half mirrors the same inputs through
    inspect_events so CLI and structured API agree.
    """
    DETECTOR = ROOT / "scripts" / "detect_loop.py"

    def cli_fixture(name, extra_args=()):
        p = subprocess.run(
            [sys.executable, str(DETECTOR), "--json", "--timeout", "60",
             *extra_args, "--log", str(ROOT / "fixtures" / name)],
            capture_output=True, text=True)
        return p.returncode, json.loads(p.stdout)

    def cli_stdin(text, extra_args=()):
        p = subprocess.run(
            [sys.executable, str(DETECTOR), "--json", "--text-mode", "instant",
             *extra_args],
            input=text, capture_output=True, text=True)
        return p.returncode, json.loads(p.stdout)

    def tool(name, arguments):
        return {"type": "tool_call", "name": name, "arguments": arguments}

    # M1: only a timestamp nested inside the written payload differs.
    rc, data = cli_fixture("must_m1_business_timestamp.log")
    assert rc == 0 and data["loop_detected"] is False, data
    writes = [tool("write", {"path": "audit.json", "content":
              [{"timestamp": x, "value": 42}]}) for x in (100, 101)]
    assert inspect_events(writes)["loop_detected"] is False
    # ... while a top-level volatile key still does not hide a repeat.
    volatile = inspect_events(
        [tool("read", {"path": "a.txt", "request_id": f"req-{i}"})
         for i in range(3)],
        text_mode="instant")
    assert volatile["loop_detected"] is True, volatile
    assert volatile["details"]["type"] == "identical_tool_calls", volatile

    # M2: the 5x phrase covers only 25 of 100 words; a 2x template must not
    # top the coverage up to the 0.5 bar.
    rc, data = cli_fixture("must_m2_ngram_mixed_coverage.log")
    assert rc == 0 and data["loop_detected"] is False, data
    phrase = "alpha bravo charlie delta echo"
    head = " ".join(phrase + f" marker{i}" for i in range(5))
    template = " ".join(f"field{i}" for i in range(20))
    tail = " ".join(f"unique{i}" for i in range(30))
    assert inspect_events(
        [" ".join([head, template, template, tail])],
        text_mode="instant")["loop_detected"] is False

    # M3: a nested tool-shaped example is not a second call (CLI == API),
    # and two function-style calls on two lines of one block count as two.
    rc, data = cli_fixture("must_m3_nested_example.log",
                           ("--max-tool-calls", "1"))
    assert rc == 0 and data["loop_detected"] is False, data
    assert data["total_tool_calls"] == 1, data
    nested = tool("read", {"path": "config.json", "example":
                  {"name": "send", "parameters": {"message": "example"}}})
    api = inspect_events([nested], max_tool_calls=1)
    assert api["loop_detected"] is False and api["total_tool_calls"] == 1, api
    rc, data = cli_fixture("must_m3_multiline_calls.log",
                           ("--max-tool-calls", "1"))
    assert rc == 1 and data["loop_detected"] is True, data
    assert data["details"]["type"] == "tool_call_budget_exceeded", data
    assert data["details"]["total_tool_calls"] == 2, data

    # M4: A-B-A-B-C in one JSON-array block still reports the cycle.
    rc, data = cli_fixture("must_m4_cycle_masked_by_trailing_call.log")
    assert rc == 1 and data["loop_detected"] is True, data
    assert data["details"]["type"] == "tool_call_cycle", data
    assert data["details"]["period"] == 2, data
    calls = [tool("exec", {"cmd": x}) for x in ("A", "B", "A", "B", "C")]
    one_block = inspect_events([json.dumps([
        {"name": e["name"], "parameters": e["arguments"]} for e in calls])])
    assert one_block["loop_detected"] is True, one_block
    assert one_block["details"]["type"] == "tool_call_cycle", one_block
    assert one_block["details"]["period"] == 2, one_block

    # M5: function-style reads differing only in request_id are identical,
    # with the same params hash as the JSON-record form.
    rc, data = cli_fixture("must_m5_function_volatile_keys.log")
    assert rc == 1 and data["loop_detected"] is True, data
    assert data["details"]["type"] == "identical_tool_calls", data
    reads = [tool("read", {"path": "a.txt", "request_id": str(i)})
             for i in range(3)]
    func_text = "\n\n".join(
        "read(" + json.dumps(e["arguments"]) + ")" for e in reads) + "\n\n"
    json_text = "\n\n".join(
        json.dumps({"name": e["name"], "parameters": e["arguments"]})
        for e in reads) + "\n\n"
    _, func_data = cli_stdin(func_text)
    _, json_data = cli_stdin(json_text)
    assert func_data["loop_detected"] is True, func_data
    assert func_data["details"]["type"] == "identical_tool_calls", func_data
    assert func_data["details"]["params_hash"] == \
        json_data["details"]["params_hash"], (func_data, json_data)

    # M6: a cycle across plain result blocks is still reported (this pins the
    # documented behavior behind the corrected README wording).
    rc, data = cli_fixture("must_m6_cycle_across_results.log")
    assert rc == 1 and data["loop_detected"] is True, data
    assert data["details"]["type"] == "tool_call_cycle", data
    assert data["details"]["period"] == 2, data
    print("Must M1-M6 regression tests passed")


def test_must_m5_paren_in_string():
    """M5 boundary (PR #3 re-review): a ")" inside a quoted JSON string
    value must not truncate function-style arguments.

    Three writes to different paths that each contain ")" must agree
    between CLI and structured API (loop_detected=false, total_tool_calls=3);
    the same holds when the ")" follows an escaped quote. Truly identical
    repeated writes to one such path must still fire on both channels.
    Fixture: fixtures/must_m5_paren_in_string.log.
    """
    DETECTOR = ROOT / "scripts" / "detect_loop.py"

    def cli_stdin(text):
        p = subprocess.run(
            [sys.executable, str(DETECTOR), "--json"],
            input=text, capture_output=True, text=True)
        return p.returncode, json.loads(p.stdout)

    def tool(name, arguments):
        return {"type": "tool_call", "name": name, "arguments": arguments}

    # Fixture (log file, --timeout 60 like the other must_* fixtures).
    p = subprocess.run(
        [sys.executable, str(DETECTOR), "--json", "--timeout", "60",
         "--log", str(ROOT / "fixtures" / "must_m5_paren_in_string.log")],
        capture_output=True, text=True)
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    data = json.loads(p.stdout)
    assert data["loop_detected"] is False, data
    assert data["total_tool_calls"] == 3, data

    # CLI stdin and structured API agree on the same three calls.
    func_text = "\n\n".join(
        'write({"path":"audit)part%d","request_id":"%d"})' % (i, i)
        for i in (1, 2, 3)) + "\n\n"
    rc, func_data = cli_stdin(func_text)
    assert rc == 0 and func_data["loop_detected"] is False, func_data
    assert func_data["total_tool_calls"] == 3, func_data
    json_text = "\n\n".join(
        json.dumps({"name": "write", "parameters": {
            "path": "audit)part%d" % i, "request_id": str(i)}})
        for i in (1, 2, 3)) + "\n\n"
    rc, json_data = cli_stdin(json_text)
    assert rc == 0 and json_data["loop_detected"] is False, json_data
    assert json_data["total_tool_calls"] == 3, json_data
    api = inspect_events(
        [tool("write", {"path": "audit)part%d" % i, "request_id": str(i)})
         for i in (1, 2, 3)], text_mode="instant")
    assert api["loop_detected"] is False and api["total_tool_calls"] == 3, api

    # Escaped quote immediately before ")": still three distinct calls.
    esc_paths = ['audit\\")part1', 'audit")part2', 'audit")part3']
    esc_text = "\n\n".join(
        "write(%s)" % json.dumps({"path": path, "request_id": str(i)})
        for i, path in enumerate(esc_paths, start=1)) + "\n\n"
    rc, esc_data = cli_stdin(esc_text)
    assert rc == 0 and esc_data["loop_detected"] is False, esc_data
    assert esc_data["total_tool_calls"] == 3, esc_data
    esc_api = inspect_events(
        [tool("write", {"path": path, "request_id": str(i)})
         for i, path in enumerate(esc_paths, start=1)], text_mode="instant")
    assert esc_api["loop_detected"] is False, esc_api
    assert esc_api["total_tool_calls"] == 3, esc_api

    # Positive control: identical writes to one ")" path still fire.
    same_text = "\n\n".join(
        'write({"path":"audit)part1","request_id":"%d"})' % i
        for i in (1, 2, 3)) + "\n\n"
    rc, same_data = cli_stdin(same_text)
    assert rc == 1 and same_data["loop_detected"] is True, same_data
    assert same_data["details"]["type"] == "repeated_side_effect_tool_call", same_data
    same_api = inspect_events(
        [tool("write", {"path": "audit)part1", "request_id": str(i)})
         for i in (1, 2, 3)], text_mode="instant")
    assert same_api["loop_detected"] is True, same_api
    assert same_api["details"]["type"] == "repeated_side_effect_tool_call", same_api

    # Extraction layer: full arguments, no truncation; nested parens outside
    # strings count; an unclosed call contributes no call at all.
    detector = LoopDetector()
    extracted = detector._extract_tool_calls(
        'write({"path":"audit)part1","request_id":"1"})')
    assert extracted == [{"name": "write",
                          "params": json.dumps({"path": "audit)part1"},
                                               sort_keys=True)}], extracted
    assert detector._extract_tool_calls("exec(foo(bar))") == [
        {"name": "exec", "params": "foo(bar)"}]
    assert detector._extract_tool_calls(
        'write({"path":"audit)part1","request_id":"1"}') == []

    # Prose guard still holds for write mid-line.
    prose = ("I will write (the summary) later.\n\n"
             "I will write (the appendix) later.\n\n"
             "I will write (the checklist) later.\n\n")
    rc, prose_data = cli_stdin(prose)
    assert rc == 0 and prose_data["loop_detected"] is False, prose_data
    assert prose_data["total_tool_calls"] == 0, prose_data
    print("Must M5 paren-in-string regression tests passed")


def test_must_m5_empty_args():
    """Empty-args Must (PR #3 re-review 2): a zero-argument function-style
    call (``browser()``) must count exactly like the structured API counts
    ``arguments: {}`` -- same total, same budget verdict -- on both
    channels. Covers the headline repro (two empty calls, budget 1), the
    single-call no-fire case, the budget boundary (total == limit does not
    fire), mixed empty/parameterized counting, and the identical-call rule
    for repeated empty calls. Fixture: fixtures/must_m5_empty_args.log.
    """
    DETECTOR = ROOT / "scripts" / "detect_loop.py"

    def cli_stdin(text, extra_args=()):
        p = subprocess.run(
            [sys.executable, str(DETECTOR), "--json", *extra_args],
            input=text, capture_output=True, text=True)
        return p.returncode, json.loads(p.stdout)

    def tool(name, arguments):
        return {"type": "tool_call", "name": name, "arguments": arguments}

    def assert_agree(text, events, extra_args=(), api_kwargs=None):
        rc, data = cli_stdin(text, extra_args)
        api = inspect_events(events, **(api_kwargs or {}))
        assert data["loop_detected"] == api["loop_detected"], (data, api)
        assert data["total_tool_calls"] == api["total_tool_calls"], (data, api)
        assert data.get("details", {}).get("type") == \
            api.get("details", {}).get("type"), (data, api)
        return rc, data, api

    # Headline repro from the re-review: fixture (log file, --timeout 60
    # like the other must_* fixtures) plus the exact stdin form.
    p = subprocess.run(
        [sys.executable, str(DETECTOR), "--json", "--timeout", "60",
         "--max-tool-calls", "1",
         "--log", str(ROOT / "fixtures" / "must_m5_empty_args.log")],
        capture_output=True, text=True)
    assert p.returncode == 1, (p.returncode, p.stdout, p.stderr)
    data = json.loads(p.stdout)
    assert data["loop_detected"] is True, data
    assert data["details"]["type"] == "tool_call_budget_exceeded", data
    assert data["total_tool_calls"] == 2, data
    rc, data, api = assert_agree(
        "browser()\n\nbrowser()\n\n",
        [tool("browser", {}), tool("browser", {})],
        ("--max-tool-calls", "1"), {"max_tool_calls": 1})
    assert rc == 1 and data["loop_detected"] is True, data
    assert data["details"]["type"] == "tool_call_budget_exceeded", data
    assert data["total_tool_calls"] == 2, data

    # Single empty call does not fire the budget; two calls at a limit of
    # 2 sit exactly on the boundary and do not fire either.
    rc, data, api = assert_agree(
        "browser()\n\n", [tool("browser", {})],
        ("--max-tool-calls", "1"), {"max_tool_calls": 1})
    assert rc == 0 and data["loop_detected"] is False, data
    assert data["total_tool_calls"] == 1, data
    rc, data, api = assert_agree(
        "browser()\n\nbrowser()\n\n",
        [tool("browser", {}), tool("browser", {})],
        ("--max-tool-calls", "2"), {"max_tool_calls": 2})
    assert rc == 0 and data["loop_detected"] is False, data
    assert data["total_tool_calls"] == 2, data

    # Mixed empty and parameterized calls count together: one empty plus
    # one parameterized call breaches a limit of 1, and stays under 2.
    mixed_text = "browser()\n\nread({\"path\":\"a.txt\"})\n\n"
    mixed_events = [tool("browser", {}), tool("read", {"path": "a.txt"})]
    rc, data, api = assert_agree(
        mixed_text, mixed_events,
        ("--max-tool-calls", "1"), {"max_tool_calls": 1})
    assert rc == 1 and data["loop_detected"] is True, data
    assert data["details"]["type"] == "tool_call_budget_exceeded", data
    assert data["total_tool_calls"] == 2, data
    rc, data, api = assert_agree(
        mixed_text, mixed_events,
        ("--max-tool-calls", "2"), {"max_tool_calls": 2})
    assert rc == 0 and data["loop_detected"] is False, data
    assert data["total_tool_calls"] == 2, data
    rc, data, api = assert_agree(
        "read({\"path\":\"a.txt\"})\n\nbrowser()\n\nbrowser()\n\n",
        [tool("read", {"path": "a.txt"}),
         tool("browser", {}), tool("browser", {})],
        ("--max-tool-calls", "2"), {"max_tool_calls": 2})
    assert rc == 1 and data["loop_detected"] is True, data
    assert data["details"]["type"] == "tool_call_budget_exceeded", data
    assert data["total_tool_calls"] == 3, data

    # Repeated empty calls follow the same identical-call rule as repeated
    # parameterized calls (third identical call fires), on both channels.
    rc, data, api = assert_agree(
        "browser()\n\nbrowser()\n\nbrowser()\n\n",
        [tool("browser", {}), tool("browser", {}), tool("browser", {})],
        ("--max-tool-calls", "2"), {"max_tool_calls": 2})
    assert rc == 1 and data["loop_detected"] is True, data
    assert data["details"]["type"] == "identical_tool_calls", data
    assert data["total_tool_calls"] == 3, data

    # Extraction layer: browser() and browser({}) are the same signature.
    detector = LoopDetector()
    assert detector._extract_tool_calls("browser()") == [
        {"name": "browser", "params": "{}"}]
    assert detector._extract_tool_calls("browser()") == \
        detector._extract_tool_calls("browser({})")
    assert detector._extract_tool_calls("browser(  )") == [
        {"name": "browser", "params": "{}"}]
    print("Must M5 empty-args regression tests passed")


def test_must_m5_empty_args_prose():
    """Line-start prose Must (PR #3 re-review 3): a zero-argument
    function-style name at the start of a line followed by explanatory
    prose on the same line (``write() denotes ...``) is documentation
    mentioning the tool, not a tool call, and must not be counted. The
    complete-call-record boundary is that only whitespace may follow
    the closing ")" on the same line. Covers the headline repro
    (verbatim, via CLI stdin and via the fixture), mid-sentence
    mentions, prose for every recognized tool name, and the guard that
    a standalone ``browser()`` still counts (empty-args Must must not
    regress): two standalone calls at a budget of 1 still breach it.
    Fixture: fixtures/must_m5_empty_args_prose.log.
    """
    DETECTOR = ROOT / "scripts" / "detect_loop.py"

    def cli_stdin(text, extra_args=()):
        p = subprocess.run(
            [sys.executable, str(DETECTOR), "--json", *extra_args],
            input=text, capture_output=True, text=True)
        return p.returncode, json.loads(p.stdout)

    # Headline repro from the re-review, verbatim: two different prose
    # lines, default mode, no budget, no real calls -> no loop, total 0.
    prose = (
        "write() denotes the writer interface in this documentation.\n\n"
        "write() can also be used as a label in the manual.\n\n"
    )
    rc, data = cli_stdin(prose)
    assert rc == 0 and data["loop_detected"] is False, (rc, data)
    assert data["total_tool_calls"] == 0, data
    # Same input as a log fixture (--timeout 60 like the other must_*
    # fixtures): still no loop and no calls at all.
    p = subprocess.run(
        [sys.executable, str(DETECTOR), "--json", "--timeout", "60",
         "--log", str(ROOT / "fixtures" / "must_m5_empty_args_prose.log")],
        capture_output=True, text=True)
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    data = json.loads(p.stdout)
    assert data["loop_detected"] is False, data
    assert data["total_tool_calls"] == 0, data

    # Mid-sentence mentions of a zero-argument call are prose too, and
    # so is a line-start mention for every recognized tool name.
    mid = "Please call browser() to fetch the page.\n\n"
    rc, data = cli_stdin(mid)
    assert rc == 0 and data["loop_detected"] is False, (rc, data)
    assert data["total_tool_calls"] == 0, data
    for tool_name in ("read", "write", "exec", "edit", "browser",
                      "web_fetch"):
        rc, data = cli_stdin(
            "%s() is described in this manual.\n\n" % tool_name)
        assert rc == 0 and data["loop_detected"] is False, (tool_name, data)
        assert data["total_tool_calls"] == 0, (tool_name, data)
    # An indented line-start mention is prose as well.
    rc, data = cli_stdin("  write() denotes the writer interface.\n\n")
    assert rc == 0 and data["loop_detected"] is False, (rc, data)
    assert data["total_tool_calls"] == 0, data

    # Extraction layer: prose lines extract nothing; a standalone call
    # on its own line still extracts, even with trailing spaces or
    # prose on the following line.
    detector = LoopDetector()
    assert detector._extract_tool_calls(prose) == []
    assert detector._extract_tool_calls(
        "write() denotes the writer interface in this documentation.") == []
    assert detector._extract_tool_calls(
        "write() can also be used as a label in the manual.") == []
    assert detector._extract_tool_calls("browser()") == [
        {"name": "browser", "params": "{}"}]
    assert detector._extract_tool_calls("browser()   \n\n") == [
        {"name": "browser", "params": "{}"}]
    assert detector._extract_tool_calls(
        "browser()\ndenotes the writer interface.\n\n") == [
        {"name": "browser", "params": "{}"}]
    assert detector._extract_tool_calls(
        "write() denotes the writer interface.\n\nbrowser()\n\n") == [
        {"name": "browser", "params": "{}"}]

    # Guard against regressing the empty-args fix: two standalone
    # browser() calls at a budget of 1 still breach the budget, CLI
    # and structured API in agreement.
    rc, data = cli_stdin("browser()\n\nbrowser()\n\n",
                         ("--max-tool-calls", "1"))
    assert rc == 1 and data["loop_detected"] is True, (rc, data)
    assert data["details"]["type"] == "tool_call_budget_exceeded", data
    assert data["total_tool_calls"] == 2, data
    api = inspect_events(
        [{"type": "tool_call", "name": "browser", "arguments": {}},
         {"type": "tool_call", "name": "browser", "arguments": {}}],
        max_tool_calls=1)
    assert api["loop_detected"] is True, api
    assert api["details"]["type"] == "tool_call_budget_exceeded", api
    assert api["total_tool_calls"] == 2, api
    print("Must M5 empty-args prose regression tests passed")


def test_must_m5_with_args_prose():
    """Line-start prose Must (PR #3 re-review 4): the complete-call-record
    boundary now also applies to function-style calls *with* arguments.
    A line-start name with arguments followed by explanatory prose on the
    same line (``write({"path": ...}) denotes ...``) is documentation
    mentioning the tool, not a tool call, and must not be counted -- the
    same boundary as the zero-argument branch (only whitespace may follow
    the closing ")" on the same line). Covers the headline repro
    (verbatim, via CLI stdin, via the fixture, and via the structured
    API), mid-sentence mentions, prose for every recognized tool name,
    punctuation variants after the closing parenthesis, and the guards
    that a standalone with-arguments call still counts and that the
    zero-argument prose fix and the empty-args budget counting have
    not regressed.
    Fixture: fixtures/must_m5_with_args_prose.log.
    """
    DETECTOR = ROOT / "scripts" / "detect_loop.py"

    def cli_stdin(text, extra_args=()):
        p = subprocess.run(
            [sys.executable, str(DETECTOR), "--json", *extra_args],
            input=text, capture_output=True, text=True)
        return p.returncode, json.loads(p.stdout)

    # Headline repro from the re-review, verbatim: two different prose
    # lines, default mode, no budget, no real calls -> no loop, total 0.
    # The two lines differ only in the volatile request_id, which is
    # ignored when comparing calls, so before the fix they were reported
    # as the same repeated side-effecting call (rc=1, total=2).
    prose = (
        'write({"path":"audit-part","request_id":"1"}) denotes the writer '
        "interface in this documentation.\n\n"
        'write({"path":"audit-part","request_id":"2"}) can also be used as '
        "a label in the manual.\n\n"
    )
    rc, data = cli_stdin(prose)
    assert rc == 0 and data["loop_detected"] is False, (rc, data)
    assert data["total_tool_calls"] == 0, data
    # Same input as a log fixture (--timeout 60 like the other must_*
    # fixtures): still no loop and no calls at all.
    p = subprocess.run(
        [sys.executable, str(DETECTOR), "--json", "--timeout", "60",
         "--log", str(ROOT / "fixtures" / "must_m5_with_args_prose.log")],
        capture_output=True, text=True)
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    data = json.loads(p.stdout)
    assert data["loop_detected"] is False, data
    assert data["total_tool_calls"] == 0, data
    # Same two prose lines through the structured API: also no loop and
    # no calls (the API negative was missing for the zero-argument prose
    # fix; it is asserted here for the with-arguments variant).
    api = inspect_events(
        ['write({"path":"audit-part","request_id":"1"}) denotes the writer '
         "interface in this documentation.",
         'write({"path":"audit-part","request_id":"2"}) can also be used as '
         "a label in the manual."])
    assert api["loop_detected"] is False, api
    assert api["total_tool_calls"] == 0, api

    # Mid-sentence mentions of a with-arguments call are prose too, and
    # so is a line-start mention for every recognized tool name.
    mid = 'Please call write({"path":"audit-part"}) to save the file.\n\n'
    rc, data = cli_stdin(mid)
    assert rc == 0 and data["loop_detected"] is False, (rc, data)
    assert data["total_tool_calls"] == 0, data
    for tool_name in ("read", "write", "exec", "edit", "browser",
                      "web_fetch"):
        rc, data = cli_stdin(
            '%s({"path":"x"}) is described in this manual.\n\n' % tool_name)
        assert rc == 0 and data["loop_detected"] is False, (tool_name, data)
        assert data["total_tool_calls"] == 0, (tool_name, data)
    # Punctuation right after the closing parenthesis is still prose on
    # the same line: semicolon, period, comma, and Chinese full stop.
    for suffix in (";", ".", ",", "\u3002"):
        rc, data = cli_stdin(
            'write({"path":"audit-part"})%s see the manual.\n\n' % suffix)
        assert rc == 0 and data["loop_detected"] is False, (suffix, data)
        assert data["total_tool_calls"] == 0, (suffix, data)
    # An indented line-start mention is prose as well.
    rc, data = cli_stdin(
        '  write({"path":"audit-part"}) denotes the writer interface.\n\n')
    assert rc == 0 and data["loop_detected"] is False, (rc, data)
    assert data["total_tool_calls"] == 0, data

    # Extraction layer: prose lines extract nothing; a standalone
    # with-arguments call on its own line still extracts, even with
    # trailing spaces or prose on the following line, and a prose line
    # followed by a real call extracts only the real call.
    detector = LoopDetector()
    assert detector._extract_tool_calls(prose) == []
    assert detector._extract_tool_calls(
        'write({"path":"audit-part","request_id":"1"}) denotes the writer '
        "interface in this documentation.") == []
    assert detector._extract_tool_calls(
        'write({"path":"audit-part","request_id":"2"}) can also be used as '
        "a label in the manual.") == []
    assert detector._extract_tool_calls('write({"path":"audit-part"})') == [
        {"name": "write", "params": '{"path": "audit-part"}'}]
    assert detector._extract_tool_calls(
        'write({"path":"audit-part"})   \n\n') == [
        {"name": "write", "params": '{"path": "audit-part"}'}]
    assert detector._extract_tool_calls(
        'write({"path":"audit-part"})\ndenotes the writer interface.\n\n'
        ) == [{"name": "write", "params": '{"path": "audit-part"}'}]
    assert detector._extract_tool_calls(
        'write({"path":"audit-part"}) denotes the writer interface.\n\n'
        'write({"path":"audit-part"})\n\n') == [
        {"name": "write", "params": '{"path": "audit-part"}'}]

    # Guard: a standalone with-arguments call still counts. Two identical
    # standalone writes trip the side-effect rule (before the budget rule),
    # so the expected type is repeated_side_effect_tool_call with total 2.
    rc, data = cli_stdin(
        'write({"path":"audit-part"})\n\n'
        'write({"path":"audit-part"})\n\n',
        ("--max-tool-calls", "1"))
    assert rc == 1 and data["loop_detected"] is True, (rc, data)
    assert data["details"]["type"] == "repeated_side_effect_tool_call", data
    assert data["total_tool_calls"] == 2, data

    # Guards against regressing the zero-argument prose fix and the
    # empty-args budget counting.
    zero_prose = (
        "write() denotes the writer interface in this documentation.\n\n"
        "write() can also be used as a label in the manual.\n\n"
    )
    rc, data = cli_stdin(zero_prose)
    assert rc == 0 and data["loop_detected"] is False, (rc, data)
    assert data["total_tool_calls"] == 0, data
    rc, data = cli_stdin("browser()\n\nbrowser()\n\n",
                         ("--max-tool-calls", "1"))
    assert rc == 1 and data["loop_detected"] is True, (rc, data)
    assert data["details"]["type"] == "tool_call_budget_exceeded", data
    assert data["total_tool_calls"] == 2, data
    print("Must M5 with-args prose regression tests passed")


def test_must_m7_tool_shape_in_prose():
    """Tool-shape JSON inside prose Must (PR #3 re-review 6, M7): the
    line-start prose exclusion must also cover a tool-shaped JSON object
    nested inside the excluded mention's arguments. Before the fix, the
    JSON scan recorded the inner ``send`` of
    ``write({"name": "send", ...}) denotes ...`` and, once the outer
    ``write`` was excluded as prose, the overlap dedup kept the inner
    ``send`` -- so two prose lines were misreported as a repeated
    side-effecting ``send`` call (CLI rc=1, API pause_and_review,
    total=2). Covers the headline repro (verbatim, via CLI stdin, via
    the fixture, and via the structured API), the closer-to-real
    ``config.json``/``content`` nesting variant, the extraction layer
    (prose extracts nothing; a standalone outer record extracts only
    the outer ``write``), and the guards that real standalone calls
    still count and still alarm.
    Fixture: fixtures/must_m7_tool_shape_in_prose.log.
    """
    DETECTOR = ROOT / "scripts" / "detect_loop.py"

    def cli_stdin(text, extra_args=()):
        p = subprocess.run(
            [sys.executable, str(DETECTOR), "--json", *extra_args],
            input=text, capture_output=True, text=True)
        return p.returncode, json.loads(p.stdout)

    # Headline repro from the re-review, verbatim: two different prose
    # lines whose arguments contain a tool-shaped JSON object, default
    # mode, no budget, no real calls -> no loop, total 0. The two lines
    # differ only in the volatile request_id inside the nested
    # parameters, which is ignored when comparing calls, so before the
    # fix they were reported as the same repeated side-effecting call
    # (rc=1, total=2, tool=send).
    prose = (
        'write({"name":"send","parameters":{"message":"example",'
        '"request_id":"1"}}) denotes the writer interface in this '
        "documentation.\n\n"
        'write({"name":"send","parameters":{"message":"example",'
        '"request_id":"2"}}) can also be used as a label in the '
        "manual.\n\n"
    )
    rc, data = cli_stdin(prose)
    assert rc == 0 and data["loop_detected"] is False, (rc, data)
    assert data["total_tool_calls"] == 0, data
    # Same input as a log fixture (--timeout 60 like the other must_*
    # fixtures): still no loop and no calls at all.
    p = subprocess.run(
        [sys.executable, str(DETECTOR), "--json", "--timeout", "60",
         "--log", str(ROOT / "fixtures" / "must_m7_tool_shape_in_prose.log")],
        capture_output=True, text=True)
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    data = json.loads(p.stdout)
    assert data["loop_detected"] is False, data
    assert data["total_tool_calls"] == 0, data
    # Same two prose lines through the structured API: also no loop and
    # no calls.
    api = inspect_events(
        ['write({"name":"send","parameters":{"message":"example",'
         '"request_id":"1"}}) denotes the writer interface in this '
         "documentation.",
         'write({"name":"send","parameters":{"message":"example",'
         '"request_id":"2"}}) can also be used as a label in the '
         "manual."])
    assert api["loop_detected"] is False, api
    assert api["total_tool_calls"] == 0, api

    # Closer-to-real file-writing prose: the tool-shaped JSON is nested
    # one level deeper, inside a "content" object. Same expectation on
    # both channels.
    variant = (
        'write({"path":"config.json","content":{"name":"send",'
        '"parameters":{"message":"example","request_id":"1"}}}) denotes '
        "the writer interface in this documentation.\n\n"
        'write({"path":"config.json","content":{"name":"send",'
        '"parameters":{"message":"example","request_id":"2"}}}) can also '
        "be used as a label in the manual.\n\n"
    )
    rc, data = cli_stdin(variant)
    assert rc == 0 and data["loop_detected"] is False, (rc, data)
    assert data["total_tool_calls"] == 0, data
    api = inspect_events(
        [line for line in variant.split("\n\n") if line.strip()])
    assert api["loop_detected"] is False, api
    assert api["total_tool_calls"] == 0, api

    # Extraction layer: prose lines extract nothing; a standalone outer
    # record extracts only the outer write (never the inner send), even
    # with trailing spaces; a prose line followed by a real call
    # extracts only the real call.
    detector = LoopDetector()
    assert detector._extract_tool_calls(prose) == []
    assert detector._extract_tool_calls(variant) == []
    assert detector._extract_tool_calls(
        'write({"name":"send","parameters":{"message":"example",'
        '"request_id":"1"}})') == [
        {"name": "write",
         "params": '{"name": "send", "parameters": {"message": "example", '
                   '"request_id": "1"}}'}]
    assert detector._extract_tool_calls(
        'write({"name":"send","parameters":{"message":"example",'
        '"request_id":"1"}})   \n\n') == [
        {"name": "write",
         "params": '{"name": "send", "parameters": {"message": "example", '
                   '"request_id": "1"}}'}]
    assert detector._extract_tool_calls(
        'write({"name":"send","parameters":{"message":"example",'
        '"request_id":"1"}}) denotes the writer interface.\n\n'
        'write({"name":"send","parameters":{"message":"example",'
        '"request_id":"1"}})\n\n') == [
        {"name": "write",
         "params": '{"name": "send", "parameters": {"message": "example", '
                   '"request_id": "1"}}'}]

    # Guards: real standalone calls still count. Two identical
    # standalone writes trip the side-effect rule (CLI and API, default
    # mode), and the tool-call budget still fires for two standalone
    # calls under a budget of 1.
    rc, data = cli_stdin(
        'write({"path":"audit-part"})\n\n'
        'write({"path":"audit-part"})\n\n')
    assert rc == 1 and data["loop_detected"] is True, (rc, data)
    assert data["details"]["type"] == "repeated_side_effect_tool_call", data
    assert data["total_tool_calls"] == 2, data
    api = inspect_events(
        ['write({"path":"audit-part"})', 'write({"path":"audit-part"})'])
    assert api["loop_detected"] is True, api
    assert api["details"]["type"] == "repeated_side_effect_tool_call", api
    assert api["total_tool_calls"] == 2, api
    rc, data = cli_stdin("browser()\n\nbrowser()\n\n",
                         ("--max-tool-calls", "1"))
    assert rc == 1 and data["loop_detected"] is True, (rc, data)
    assert data["details"]["type"] == "tool_call_budget_exceeded", data
    assert data["total_tool_calls"] == 2, data
    print("Must M7 tool-shape-in-prose regression tests passed")


def test_must_m7_unbalanced_multiline_prose():
    """M7 unbalanced-paren variant (PR #3 re-review 7, residual Must):
    a line-start prose mention whose parentheses never balance, with a
    tool-shaped JSON object that is itself complete but spans several
    lines (``write({\\n "name": "send", ...\\n}`` with no closing
    ``)``). Before this fix, the prose exclusion span stopped at the
    end of the header's line, while the fully-inside filter required
    the inner ``send`` candidate to lie wholly inside that span -- so
    the multiline candidate escaped the exclusion and two prose
    segments were misreported as a repeated side-effecting ``send``
    call (CLI rc=1, API pause_and_review, total=2, tool=send). The
    fix stretches the exclusion span to cover a JSON candidate that
    starts on the mention's line. Covers the re-review's verbatim
    repro (via CLI stdin, via the fixture, and via the structured
    API), the extraction layer, and the guards that a real call on a
    later line is still counted and that bare JSON records still
    alarm.
    Fixture: fixtures/must_m7_unbalanced_multiline_prose.log.
    """
    DETECTOR = ROOT / "scripts" / "detect_loop.py"

    def cli_stdin(text, extra_args=()):
        p = subprocess.run(
            [sys.executable, str(DETECTOR), "--json", *extra_args],
            input=text, capture_output=True, text=True)
        return p.returncode, json.loads(p.stdout)

    def segment(request_id):
        return (
            'write({\n'
            '  "name":"send",\n'
            '  "parameters":{"message":"example",'
            f'"request_id":"{request_id}"'
            "}\n"
            "}\n"
        )

    # Verbatim repro from the re-review: two unbalanced prose
    # segments, differing only in the volatile request_id inside the
    # nested parameters -> no loop, total 0, on both channels.
    prose = segment("1") + "\n" + segment("2") + "\n"
    rc, data = cli_stdin(prose)
    assert rc == 0 and data["loop_detected"] is False, (rc, data)
    assert data["total_tool_calls"] == 0, data
    p = subprocess.run(
        [sys.executable, str(DETECTOR), "--json", "--timeout", "60",
         "--log", str(ROOT / "fixtures"
                      / "must_m7_unbalanced_multiline_prose.log")],
        capture_output=True, text=True)
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    data = json.loads(p.stdout)
    assert data["loop_detected"] is False, data
    assert data["total_tool_calls"] == 0, data
    api = inspect_events([segment("1"), segment("2")])
    assert api["loop_detected"] is False, api
    assert api["total_tool_calls"] == 0, api

    # Extraction layer: each unbalanced prose segment extracts
    # nothing; an unbalanced segment followed on later lines by a
    # real standalone call extracts only the real call.
    detector = LoopDetector()
    assert detector._extract_tool_calls(segment("1")) == []
    assert detector._extract_tool_calls(segment("2")) == []
    assert detector._extract_tool_calls(prose) == []
    assert detector._extract_tool_calls(
        segment("1") + "\n" + 'write({"path":"audit-part"})\n') == [
        {"name": "write", "params": '{"path": "audit-part"}'}]

    # Guards: bare JSON records are unaffected by the prose span
    # (they have no line-start function-style header). Two identical
    # standalone send records still alarm, and a prose segment plus
    # one real JSON record counts exactly the real record.
    rc, data = cli_stdin(
        '{"name":"send","parameters":{"message":"example",'
        '"request_id":"1"}}\n\n'
        '{"name":"send","parameters":{"message":"example",'
        '"request_id":"2"}}\n\n')
    assert rc == 1 and data["loop_detected"] is True, (rc, data)
    assert data["details"]["type"] == "repeated_side_effect_tool_call", data
    assert data["details"]["tool"] == "send", data
    assert data["total_tool_calls"] == 2, data
    rc, data = cli_stdin(
        segment("1") + "\n"
        + '{"name":"send","parameters":{"message":"example",'
          '"request_id":"1"}}\n\n')
    assert rc == 0 and data["loop_detected"] is False, (rc, data)
    assert data["total_tool_calls"] == 1, data
    print("Must M7 unbalanced-multiline-prose regression tests passed")


def test_must_m7_unbalanced_outer_config_prose():
    """M7 outer-config variant (PR #3 re-review 8, residual Must):
    a line-start prose mention whose parentheses never balance, where
    the argument opens with an ordinary (non-tool-shaped) config JSON
    object and the tool-shaped object is nested inside it on a later
    line (``write({"path": "config.json", "content": {"name":
    "send", ...}}`` with no closing ``)``). Before this fix, the outer
    object never entered ``candidates`` (it has no top-level ``name`` +
    ``parameters``), so the inner ``send`` candidate started on a later
    line, outside the header line's exclusion span, and two prose
    segments were misreported as a repeated side-effecting ``send``
    call (CLI rc=1, API pause_and_review, total=2, tool=send). The fix
    stretches the exclusion span to the end of the outer JSON object
    the argument opens with, so the nested candidate falls inside it.
    Covers the re-review's verbatim repro (via CLI stdin, via the
    fixture, and via the structured API), the equivalent variant with
    the ``{`` moved to the line after ``write(``, the extraction
    layer, and the guards that a real call on a later line, a bare
    JSON record, identical real writes, and the tool-call budget are
    unaffected. Fixture:
    fixtures/must_m7_unbalanced_outer_config_prose.log.
    """
    DETECTOR = ROOT / "scripts" / "detect_loop.py"

    def cli_stdin(text, extra_args=()):
        p = subprocess.run(
            [sys.executable, str(DETECTOR), "--json", *extra_args],
            input=text, capture_output=True, text=True)
        return p.returncode, json.loads(p.stdout)

    def tool(name, arguments):
        return {"type": "tool_call", "name": name, "arguments": arguments}

    def config(request_id):
        return {
            "path": "config.json",
            "content": {"name": "send", "parameters": {
                "message": "example", "request_id": request_id}},
        }

    def segment(request_id, brace_on_next_line=False):
        opener = "write(\n" if brace_on_next_line else "write("
        return opener + json.dumps(config(request_id), indent=2) + "\n"

    # Verbatim repro from the re-review: two unbalanced prose
    # segments, differing only in the volatile request_id inside the
    # nested parameters -> no loop, total 0, on both channels.
    for brace_on_next_line in (False, True):
        prose = (segment("1", brace_on_next_line) + "\n"
                 + segment("2", brace_on_next_line) + "\n")
        rc, data = cli_stdin(prose)
        assert rc == 0 and data["loop_detected"] is False, (rc, data)
        assert data["total_tool_calls"] == 0, data
        api = inspect_events([segment("1", brace_on_next_line),
                              segment("2", brace_on_next_line)])
        assert api["loop_detected"] is False, api
        assert api["total_tool_calls"] == 0, api
        api = inspect_events([prose])
        assert api["loop_detected"] is False, api
        assert api["total_tool_calls"] == 0, api
    p = subprocess.run(
        [sys.executable, str(DETECTOR), "--json", "--timeout", "60",
         "--log", str(ROOT / "fixtures"
                      / "must_m7_unbalanced_outer_config_prose.log")],
        capture_output=True, text=True)
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    data = json.loads(p.stdout)
    assert data["loop_detected"] is False, data
    assert data["total_tool_calls"] == 0, data

    # Extraction layer: each unbalanced prose segment extracts
    # nothing, in either brace position; an unbalanced segment
    # followed on later lines by a real standalone call extracts
    # only the real call.
    detector = LoopDetector()
    assert detector._extract_tool_calls(segment("1")) == []
    assert detector._extract_tool_calls(segment("1", True)) == []
    assert detector._extract_tool_calls(
        segment("1") + "\n" + segment("2")) == []
    assert detector._extract_tool_calls(
        segment("1") + "\n" + 'write({"path":"audit-part"})\n') == [
        {"name": "write", "params": '{"path": "audit-part"}'}]
    assert detector._extract_tool_calls(
        segment("1") + "\n"
        + '{"name":"send","parameters":{"message":"example",'
          '"request_id":"1"}}\n') == [
        {"name": "send", "params": '{"message": "example"}'}]
    # A cross-span outer read('...') candidate is retained while
    # the nested send inside the prose segment stays excluded.
    assert detector._extract_tool_calls(
        "read('config.json')\n\n" + segment("1")) == [
        {"name": "read", "params": "'config.json'"}]
    # A non-JSON unbalanced argument must not drag a distant "{"
    # into the span: a real JSON record after such prose still counts.
    assert detector._extract_tool_calls(
        "write(some prose here\nmore prose\n"
        + '{"name":"send","parameters":{"message":"example",'
          '"request_id":"1"}}\n') == [
        {"name": "send", "params": '{"message": "example"}'}]

    # Guards: bare JSON records are unaffected by the prose span
    # (they have no line-start function-style header). Two identical
    # standalone send records still alarm on both channels.
    rc, data = cli_stdin(
        '{"name":"send","parameters":{"message":"example",'
        '"request_id":"1"}}\n\n'
        '{"name":"send","parameters":{"message":"example",'
        '"request_id":"2"}}\n\n')
    assert rc == 1 and data["loop_detected"] is True, (rc, data)
    assert data["details"]["type"] == "repeated_side_effect_tool_call", data
    assert data["details"]["tool"] == "send", data
    assert data["total_tool_calls"] == 2, data
    api = inspect_events([
        '{"name":"send","parameters":{"message":"example",'
        '"request_id":"1"}}',
        '{"name":"send","parameters":{"message":"example",'
        '"request_id":"2"}}'])
    assert api["loop_detected"] is True, api
    assert api["total_tool_calls"] == 2, api
    # Two identical real writes still alarm on both channels.
    rc, data = cli_stdin(
        'write({"path":"a"})\n\nwrite({"path":"a"})\n\n')
    assert rc == 1 and data["loop_detected"] is True, (rc, data)
    assert data["details"]["type"] == "repeated_side_effect_tool_call", data
    assert data["details"]["tool"] == "write", data
    assert data["total_tool_calls"] == 2, data
    api = inspect_events(['write({"path":"a"})', 'write({"path":"a"})'])
    assert api["loop_detected"] is True, api
    assert api["total_tool_calls"] == 2, api
    # The tool-call budget still fires on both channels.
    rc, data = cli_stdin(
        "browser()\n\nbrowser()\n\n", ("--max-tool-calls", "1"))
    assert rc == 1 and data["loop_detected"] is True, (rc, data)
    assert data["details"]["type"] == "tool_call_budget_exceeded", data
    assert data["total_tool_calls"] == 2, data
    api = inspect_events([tool("browser", {}), tool("browser", {})],
                         max_tool_calls=1)
    assert api["loop_detected"] is True, api
    assert api["details"]["type"] == "tool_call_budget_exceeded", api
    assert api["total_tool_calls"] == 2, api
    # A prose segment plus one real JSON record counts exactly the
    # real record.
    rc, data = cli_stdin(
        segment("1") + "\n"
        + '{"name":"send","parameters":{"message":"example",'
          '"request_id":"1"}}\n\n')
    assert rc == 0 and data["loop_detected"] is False, (rc, data)
    assert data["total_tool_calls"] == 1, data
    print("Must M7 unbalanced-outer-config-prose regression tests passed")


def test_must_20261112_all_repo_review():
    """Regression tests for the 4 new Musts (all-repo review 2026-11-12).

    M1: identical calls already completed mid-block must not be masked by
    a trailing different call in the same block. M2: the duration gate
    must measure the whole consecutive-identical run, not the last three
    blocks. M3: structured tool calls must not count as language drift.
    M4: an error summary (or a summary without loop_detected) must not
    be read as "no loop found" by the recovery policy.
    """
    DETECTOR = ROOT / "scripts" / "detect_loop.py"
    RECOVERY = ROOT / "scripts" / "recovery_policy.py"

    def cli_fixture(name, extra_args=()):
        p = subprocess.run(
            [sys.executable, str(DETECTOR), "--json", "--timeout", "60",
             *extra_args, "--log", str(ROOT / "fixtures" / name)],
            capture_output=True, text=True)
        return p.returncode, json.loads(p.stdout)

    def tool(name, arguments):
        return {"type": "tool_call", "name": name, "arguments": arguments}

    # M1: write,write,read in one block -> repeated side effect; the
    # trailing read used to mask the completed repeat.
    rc, data = cli_fixture("must_20261112_m1_same_block_write.log")
    assert rc == 1 and data["loop_detected"] is True, data
    assert data["details"]["type"] == "repeated_side_effect_tool_call", data
    rc, data = cli_fixture("must_20261112_m1_same_block_read.log")
    assert rc == 1 and data["loop_detected"] is True, data
    assert data["details"]["type"] == "identical_tool_calls", data
    # API half: the same three calls as one text block.
    write = json.dumps({"name": "write", "parameters": {"path": "a.txt", "text": "hello"}})
    read = json.dumps({"name": "read", "parameters": {"path": "b.txt"}})
    one_block = inspect_events(["\n".join([write, write, read])], text_mode="instant")
    assert one_block["loop_detected"] is True, one_block
    assert one_block["details"]["type"] == "repeated_side_effect_tool_call", one_block

    # M2: the same text every 10s for 600s (61 blocks). The fixture run
    # uses --timeout 60, so the run clears the gate once it reaches 60s.
    rc, data = cli_fixture("must_20261112_m2_duration_accumulated.log")
    assert rc == 1 and data["loop_detected"] is True, data
    assert data["details"]["type"] == "consecutive_identical_output", data
    assert data["details"]["duration_seconds"] >= 60, data
    # API half with the review's exact numbers: default 180s gate, 61
    # events at 10s spacing must fire once the run reaches 180s.
    events = [{"type": "text", "text": "The system will now repeat the same status update again and again.",
               "timestamp": 10.0 * i} for i in range(61)]
    long_run = inspect_events(events)
    assert long_run["loop_detected"] is True, long_run
    assert long_run["details"]["type"] == "consecutive_identical_output", long_run
    assert long_run["details"]["duration_seconds"] == 180, long_run
    # ... while a run broken by a different block does not accumulate.
    broken = events[:10] + [{"type": "text", "text": "A completely different status line appears here instead.",
                             "timestamp": 100.0}] + [
        {"type": "text", "text": "The system will now repeat the same status update again and again.",
         "timestamp": 10.0 * i} for i in range(11, 14)]
    assert inspect_events(broken)["loop_detected"] is False, broken

    # M3: three reads with Chinese paths under expected_language="zh"
    # must not drift; the normalized tool record is English-looking JSON
    # by construction. English prose under zh still drifts.
    reads = inspect_events(
        [tool("read", {"path": p}) for p in ("甲", "乙", "丙")],
        expected_language="zh", text_mode="instant")
    assert reads["loop_detected"] is False, reads
    assert reads["policy"]["action"] == "continue", reads
    prose = inspect_events(["Same English sentence here."] * 3,
                           expected_language="zh", text_mode="instant")
    assert prose["loop_detected"] is True, prose
    assert prose["details"]["type"] == "language_drift", prose

    # M4: an error summary and an empty object are rejected by decide()
    # and by the recovery CLI (exit 2); a valid summary still decides.
    for bad in ({"error": "no_input"}, {}):
        try:
            decide(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"decide accepted invalid summary: {bad!r}")
        p = subprocess.run([sys.executable, str(RECOVERY)], input=json.dumps(bad),
                           capture_output=True, text=True)
        assert p.returncode == 2, (bad, p.returncode, p.stdout, p.stderr)
    good = subprocess.run([sys.executable, str(RECOVERY)], input='{"loop_detected": false}',
                          capture_output=True, text=True)
    assert good.returncode == 0 and json.loads(good.stdout)["action"] == "continue", good.stdout
    print("Must 20261112 all-repo-review regression tests passed")


def test_must_n1_mixed_prose_tool_json():
    """Regression test for N1 (review round 10, 2026-11-13).

    A block that mixes English prose with a tool-call JSON record is
    still English natural-language output: with expected_language="zh"
    it must drift. The M3 fix had classified any block containing a
    tool call as a tool_call, so the drift check never saw the prose.
    Controls: Chinese prose mixed with a tool record must not drift,
    and a one-word English marker ("Done.") plus a tool record must
    not drift either -- the language check judges the prose that
    remains after stripping the call records, not the records.
    """
    DETECTOR = ROOT / "scripts" / "detect_loop.py"

    def cli_fixture(name, extra_args=()):
        p = subprocess.run(
            [sys.executable, str(DETECTOR), "--json", "--timeout", "60",
             *extra_args, "--log", str(ROOT / "fixtures" / name)],
            capture_output=True, text=True)
        return p.returncode, json.loads(p.stdout)

    rc, data = cli_fixture("must_n1_mixed_prose_tool_json.log",
                           ("--expect-language", "zh", "--text-mode", "instant"))
    assert rc == 1 and data["loop_detected"] is True, data
    assert data["details"]["type"] == "language_drift", data

    # API half: the same mixed content as explicit text events drifts.
    base = "First I completed a detailed review of the recent repository changes and found several concrete issues worth discussing. "
    events = [
        {"type": "text", "text": base + '{"name":"read","parameters":{"path":"file01.txt"}}'},
        {"type": "text", "text": base.replace("First", "Second") + '{"name":"read","parameters":{"path":"file02.txt"}}'},
        {"type": "text", "text": base.replace("First", "Third") + '{"name":"read","parameters":{"path":"file03.txt"}}'},
    ]
    r = inspect_events(events, expected_language="zh", text_mode="instant")
    assert r["loop_detected"] is True, r
    assert r["details"]["type"] == "language_drift", r

    # Control: Chinese prose mixed with a tool record must not drift.
    zh = inspect_events(
        [{"type": "text", "text": "我已完成检查。{\"name\":\"read\",\"parameters\":{\"path\":\"file01.txt\"}}"},
         {"type": "text", "text": "我已核对输出。{\"name\":\"read\",\"parameters\":{\"path\":\"file02.txt\"}}"},
         {"type": "text", "text": "我已确认结果。{\"name\":\"read\",\"parameters\":{\"path\":\"file03.txt\"}}"}],
        expected_language="zh", text_mode="instant")
    assert zh["loop_detected"] is False, zh

    # Control: a one-word English marker plus a tool record is not
    # enough prose to judge language drift (CLI path: the marker is
    # the whole prose remainder once the record is stripped). The
    # markers differ so Rule 1 similarity does not fire first.
    marker = (
        "Done. {\"name\":\"read\",\"parameters\":{\"path\":\"file01.txt\"}}\n\n"
        "Checked. {\"name\":\"read\",\"parameters\":{\"path\":\"file02.txt\"}}\n\n"
        "Verified. {\"name\":\"read\",\"parameters\":{\"path\":\"file03.txt\"}}\n"
    )
    p = subprocess.run(
        [sys.executable, str(DETECTOR), "--json", "--text-mode", "instant",
         "--expect-language", "zh"],
        input=marker, capture_output=True, text=True)
    assert p.returncode == 0 and json.loads(p.stdout)["loop_detected"] is False, (p.stdout, p.stderr)
    print("Must N1 mixed-prose-tool-JSON regression tests passed")


def test_must_n2_wrapped_tool_json():
    """Regression test for N2 (review round 11, 2026-11-13).

    Three pure tool-call JSON records wrapped in an outer envelope
    (``{"role": "assistant", "tool_calls": [...]}``) must not drift
    in a Chinese task: stripping only the inner call record left the
    envelope key names (role/assistant/tool_calls) behind and kind
    inference mistook them for English prose. The envelope is now
    stripped together with its calls when it carries no real prose.
    Controls: an envelope carrying a multi-word English ``note``
    still drifts, and a spaced-out envelope (``"role": "assistant"``)
    stays clean.
    """
    DETECTOR = ROOT / "scripts" / "detect_loop.py"

    rc, data = (lambda p: (p.returncode, json.loads(p.stdout)))(
        subprocess.run(
            [sys.executable, str(DETECTOR), "--json", "--timeout", "60",
             "--expect-language", "zh", "--text-mode", "instant",
             "--log", str(ROOT / "fixtures" / "must_n2_wrapped_tool_json.log")],
            capture_output=True, text=True))
    assert rc == 0 and data["loop_detected"] is False, data
    assert data["total_tool_calls"] == 3, data

    # API half: the same wrapped records through the detector's kind
    # inference (event_kind=None, as the CLI feeds stdin blocks).
    # NOTE: inspect_events({"type": "text", ...}) declares the content
    # prose, so it deliberately bypasses inference -- a wrapped record
    # passed as an explicit text event still drifts, exactly as the
    # N1 API half expects for mixed prose. Inference is exercised
    # directly on LoopDetector instead.
    detector = LoopDetector(expected_language="zh", text_mode="instant")
    for block in (
        '{"role":"assistant","tool_calls":[{"name":"read","parameters":{"path":"src/alpha/reports/monthly.csv"}}]}',
        '{"role":"assistant","tool_calls":[{"name":"read","parameters":{"path":"docs/architecture/service-map.md"}}]}',
        '{"role":"assistant","tool_calls":[{"name":"read","parameters":{"path":"tests/smoke/results/output.json"}}]}',
    ):
        detector.process_block(block)
    assert detector.loop_detected is False, detector.summary()

    # Control: prose mixed with a wrapped call still drifts through
    # the same inference path (N1 shape, wrapped record).
    detector = LoopDetector(expected_language="zh", text_mode="instant")
    for i, first in enumerate(("First", "Second", "Third")):
        detector.process_block(
            f"{first} I completed a detailed review of the recent repository changes and found several concrete issues worth discussing. "
            f'{{"role":"assistant","tool_calls":[{{"name":"read","parameters":{{"path":"file0{i + 1}.txt"}}}}]}}'
        )
    assert detector.loop_detected is True, detector.summary()
    assert detector.summary()["details"]["type"] == "language_drift", detector.summary()

    # Control: an envelope carrying real English prose still drifts.
    notes = [
        "Please review this file carefully today",
        "Please examine the second document thoroughly now",
        "Please inspect the third output file in detail",
    ]
    prose_envelope = "".join(
        '{"role":"assistant","note":"%s","tool_calls":[{"name":"read","parameters":{"path":"file0%d.txt"}}]}\n\n'
        % (note, i + 1)
        for i, note in enumerate(notes)
    )
    p = subprocess.run(
        [sys.executable, str(DETECTOR), "--json", "--text-mode", "instant",
         "--expect-language", "zh"],
        input=prose_envelope, capture_output=True, text=True)
    assert p.returncode == 1 and json.loads(p.stdout)["details"]["type"] == "language_drift", (p.stdout, p.stderr)

    # Control: a spaced-out pure envelope stays clean. (Paths differ
    # substantially, as in the fixture: one-digit differences would
    # trip Rule 1 similarity instead, which is unrelated to drift.)
    spaced = "".join(
        '{"role": "assistant", "tool_calls": [{"name": "read", "parameters": {"path": "%s"}}]}\n\n' % path
        for path in ("src/alpha/reports/monthly.csv",
                     "docs/architecture/service-map.md",
                     "tests/smoke/results/output.json")
    )
    p = subprocess.run(
        [sys.executable, str(DETECTOR), "--json", "--text-mode", "instant",
         "--expect-language", "zh"],
        input=spaced, capture_output=True, text=True)
    assert p.returncode == 0 and json.loads(p.stdout)["loop_detected"] is False, (p.stdout, p.stderr)
    print("Must N2 wrapped-tool-JSON regression tests passed")


def test_detector_behavior_contract():
    DETECTOR=ROOT/"scripts"/"detect_loop.py"

    def run(name, expected_rc, expected_loop, timeout=60, extra_args=(), forbidden=()):
        p=subprocess.run([sys.executable,str(DETECTOR),"--json","--timeout",str(timeout),*extra_args,"--log",str(ROOT/"fixtures"/name)],capture_output=True,text=True)
        assert p.returncode == expected_rc, (name,p.returncode,p.stdout,p.stderr)
        data=json.loads(p.stdout)
        assert data["loop_detected"] is expected_loop, (name,data)
        for value in forbidden:
            assert value not in p.stdout, (name, value, p.stdout)

    run("loop_detected.log",1,True)
    run("normal_output.log",0,False)
    run("repeated_but_short.log",0,False)
    # Historical evidence is tested using the documented 60-second review threshold.
    run("../logs/sample_degenerate_loop.log",1,True)
    run("tool_retry_changed_params.log",0,False)
    run("nonconsecutive_tool_calls.log",0,False)
    run("near_duplicate_below_threshold.log",0,False)
    run("tool_key_order_repeat.log",1,True, forbidden=("sensitive-demo",))
    run("side_effect_repeat.log",1,True, forbidden=("sensitive-demo",))
    run("language_drift_zh.log",1,True, extra_args=("--expect-language","zh"))
    run("tool_cycle_abab.log",1,True)
    run("tool_cycle_productive.log",0,False)
    run("tool_volatile_key_repeat.log",1,True)
    run("repeated_ngram_within_block.log",1,True)
    run("ngram_below_threshold.log",0,False)
    run("tool_budget_exceeded.log",1,True, extra_args=("--max-tool-calls","10"))
    run("tool_budget_exceeded.log",0,False)
    leak = subprocess.run(
        [sys.executable, str(DETECTOR), "--json", "--text-mode", "instant"],
        input='{"name":"send","parameters":{"api_key":"sensitive-demo"}}\n\n' * 2,
        capture_output=True,
        text=True,
    )
    assert leak.returncode == 1 and "sensitive-demo" not in leak.stdout, leak.stdout
    # Ordinary prose that mentions a tool name must not be parsed as a tool call.
    prose=(
        "I will edit (the first draft) now.\n\n"
        "Next I will edit (the release notes) for clarity.\n\n"
        "Finally I will edit (the checklist) before review.\n\n"
    )
    p=subprocess.run([sys.executable,str(DETECTOR),"--json","--text-mode","instant"],input=prose,capture_output=True,text=True)
    assert p.returncode == 0 and json.loads(p.stdout)["loop_detected"] is False, (p.stdout,p.stderr)
    # Nested JSON parameters remain a single stable call signature.
    nested='{"name":"exec","parameters":{"cmd":{"query":"status"},"flags":["--json"]}}\n\n'
    p=subprocess.run([sys.executable,str(DETECTOR),"--json","--text-mode","instant"],input=nested * 3,capture_output=True,text=True)
    assert p.returncode == 1 and json.loads(p.stdout)["details"]["type"] == "identical_tool_calls", (p.stdout,p.stderr)
    # Instant mode is opt-in; it separates immediate repetition signals from
    # the default duration-gated policy used for conservative post-processing.
    run("repeated_but_short.log",1,True, extra_args=("--text-mode", "instant"))
    # Recovery policy is a pure decision layer: it must not execute or expose tool payloads.
    POLICY=ROOT/"scripts"/"recovery_policy.py"
    def policy(summary, *args):
        p=subprocess.run([sys.executable,str(POLICY),*args],input=json.dumps(summary),capture_output=True,text=True)
        assert p.returncode == 0, (p.stdout,p.stderr)
        return json.loads(p.stdout)
    assert policy({"loop_detected":False})["action"] == "continue"
    assert policy({"loop_detected":True,"reason":"repeat","details":{"type":"repeated_side_effect_tool_call"}})["action"] == "pause_and_review"
    assert policy({"loop_detected":True,"reason":"repeat","details":{"type":"identical_tool_calls"}}, "--retryable")["action"] == "stop_and_retry_once"
    assert policy({"loop_detected":True,"reason":"repeat","details":{"type":"identical_tool_calls"}}, "--retryable", "--retry-count", "1")["action"] == "stop_and_escalate"
    bad=subprocess.run([sys.executable,str(POLICY)],input="not-json",capture_output=True,text=True)
    assert bad.returncode == 2 and 'error' in bad.stderr
    bad=subprocess.run([sys.executable,str(POLICY)],input=json.dumps({"loop_detected":"false"}),capture_output=True,text=True)
    assert bad.returncode == 2 and 'boolean' in bad.stderr
    for flag in (("--threshold", "1"), ("--timeout", "-1"), ("--similarity", "1.1"), ("--max-tool-calls", "0")):
        bad=subprocess.run([sys.executable,str(DETECTOR),*flag,"--log",str(ROOT/"fixtures"/"normal_output.log")],capture_output=True,text=True)
        assert bad.returncode == 2, (flag,bad.stdout,bad.stderr)
    empty=subprocess.run([sys.executable,str(DETECTOR),"--json"],input="",capture_output=True,text=True)
    assert empty.returncode == 2 and json.loads(empty.stdout)["error"] == "no_input"
    # Stdin must be evaluated as blocks arrive; otherwise every block would receive
    # an end-of-stream timestamp and duration-gated detection could never work.
    stream=subprocess.Popen([sys.executable,str(DETECTOR),"--json","--threshold","2","--timeout","0"],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    stream.stdin.write("same streamed block\n\n")
    stream.stdin.flush()
    stream.stdin.write("same streamed block\n\n")
    stream.stdin.flush()
    stream.stdin.close()
    stream.wait(timeout=5)
    out=stream.stdout.read()
    err=stream.stderr.read()
    assert stream.returncode == 1 and json.loads(out)["loop_detected"] is True, (out,err)
    print("detector behavior tests passed")


if __name__ == "__main__":
    test_event_normalization_contract()
    test_runtime_facade_contract()
    test_new_signals_contract()
    test_must_regressions_m1_to_m6()
    test_must_m5_paren_in_string()
    test_must_m5_empty_args()
    test_must_m5_empty_args_prose()
    test_must_m5_with_args_prose()
    test_must_m7_tool_shape_in_prose()
    test_must_m7_unbalanced_multiline_prose()
    test_must_m7_unbalanced_outer_config_prose()
    test_must_20261112_all_repo_review()
    test_must_n1_mixed_prose_tool_json()
    test_must_n2_wrapped_tool_json()
    test_detector_behavior_contract()
