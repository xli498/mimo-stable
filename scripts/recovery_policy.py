#!/usr/bin/env python3
"""Turn detector output into a conservative, machine-readable next action.

This module deliberately does not execute tools, retry requests, or switch models.
It only produces a decision so the caller can apply its own permissions and policy.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mimo_stable.policy import decide

def main() -> int:
    parser = argparse.ArgumentParser(description="Create a conservative recovery decision from detector JSON.")
    parser.add_argument("--summary", help="Detector JSON file; otherwise read one JSON document from stdin.")
    parser.add_argument("--retryable", action="store_true", help="Allow one controlled retry when no retry has happened.")
    parser.add_argument("--retry-count", type=int, default=0)
    args = parser.parse_args()

    try:
        if args.summary:
            with open(args.summary, encoding="utf-8") as handle:
                raw = handle.read()
        else:
            raw = sys.stdin.read()
        summary = json.loads(raw)
        if not isinstance(summary, dict):
            raise ValueError("summary must be a JSON object")
        if args.retry_count < 0:
            raise ValueError("retry-count must be non-negative")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    try:
        decision = decide(summary, retryable=args.retryable, retry_count=args.retry_count)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(decision, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
