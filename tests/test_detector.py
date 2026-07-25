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
run("near_duplicate_below_threshold.log",0,False)
run("tool_key_order_repeat.log",1,True, forbidden=("sensitive-demo",))
run("side_effect_repeat.log",1,True, forbidden=("sensitive-demo",))
run("language_drift_zh.log",1,True, extra_args=("--expect-language","zh"))
print("detector behavior tests passed")
