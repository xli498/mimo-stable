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
    for kwargs in ({"repeat_threshold": True}, {"time_threshold": 0.5}, {"similarity_threshold": float("nan")}, {"text_mode": "unknown"}):
        try:
            LoopDetector(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"detector accepted invalid configuration: {kwargs!r}")


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
    for flag in (("--threshold", "1"), ("--timeout", "-1"), ("--similarity", "1.1")):
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
    test_detector_behavior_contract()
