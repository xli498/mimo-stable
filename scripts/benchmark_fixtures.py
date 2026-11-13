#!/usr/bin/env python3
"""Run the checked-in fixtures and print a small reproducible benchmark."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DETECTOR = ROOT / "scripts" / "detect_loop.py"

CASES = [
    ("loop_detected.log", True, ()),
    ("normal_output.log", False, ()),
    ("repeated_but_short.log", False, ()),
    ("near_duplicate_below_threshold.log", False, ()),
    ("tool_retry_changed_params.log", False, ()),
    ("nonconsecutive_tool_calls.log", False, ()),
    ("tool_key_order_repeat.log", True, ()),
    ("side_effect_repeat.log", True, ()),
    ("language_drift_zh.log", True, ()),
    ("tool_cycle_abab.log", True, ()),
    ("tool_cycle_productive.log", False, ()),
    ("tool_volatile_key_repeat.log", True, ()),
    ("repeated_ngram_within_block.log", True, ()),
    ("ngram_below_threshold.log", False, ()),
    ("tool_budget_exceeded.log", True, ("--max-tool-calls", "10")),
]


def run(path: str, expected: bool, extra_args: tuple[str, ...] = ()) -> tuple[bool, str]:
    args = [sys.executable, str(DETECTOR), "--json", "--timeout", "60"]
    if path == "language_drift_zh.log":
        args += ["--expect-language", "zh"]
    args += list(extra_args)
    args += ["--log", str(ROOT / "fixtures" / path)]
    result = subprocess.run(args, capture_output=True, text=True)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, f"invalid JSON: {result.stdout!r}"
    actual = bool(data.get("loop_detected"))
    return actual == expected, f"expected={expected} actual={actual} type={data.get('details', {}).get('type')}"


def main() -> int:
    passed = 0
    for path, expected, extra_args in CASES:
        ok, detail = run(path, expected, extra_args)
        print(f"{'PASS' if ok else 'FAIL'} {path}: {detail}")
        passed += ok
    print(f"\nfixture benchmark: {passed}/{len(CASES)} cases passed")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
