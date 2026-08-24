#!/usr/bin/env python3
"""Behavior contract tests for the dependency-free loop detector."""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
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
