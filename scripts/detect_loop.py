#!/usr/bin/env python3
"""
MiMo Stable — Degenerate Loop Detection Script

Detects degenerate loops in model output streams where the model
repeatedly emits identical or near-identical text blocks.

Detection rules:
  1. Consecutive identical output blocks (3+ repeats)
  2. Same tool called 3+ times with identical parameters
  3. Output duration exceeding threshold without meaningful change

Usage:
  # From stdin (pipe model output):
  model_output 2>&1 | python3 detect_loop.py

  # From log file:
  python3 detect_loop.py --log sample_degenerate_loop.log

  # With custom thresholds:
  python3 detect_loop.py --threshold 4 --timeout 300 --log sample.log

  # JSON output for integration:
  python3 detect_loop.py --json --log sample.log

Exit codes:
  0 — No loop detected
  1 — Loop detected
  2 — Invalid arguments
"""

import argparse
import json
import re
import sys
import time
from collections import deque
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path


class LoopDetector:
    """Detects degenerate loops in model output streams."""

    def __init__(
        self,
        repeat_threshold: int = 3,
        time_threshold: int = 180,
        similarity_threshold: float = 0.95,
        json_output: bool = False,
    ):
        self.repeat_threshold = repeat_threshold
        self.time_threshold = time_threshold
        self.similarity_threshold = similarity_threshold
        self.json_output = json_output

        # State
        self.blocks: deque = deque(maxlen=max(repeat_threshold + 5, 20))
        self.tool_calls: deque = deque(maxlen=max(repeat_threshold + 5, 20))
        self.block_timestamps: list[float] = []
        self.loop_detected = False
        self.loop_reason = ""
        self.loop_details: dict = {}

    def _similarity(self, a: str, b: str) -> float:
        """Compute similarity ratio between two strings."""
        return SequenceMatcher(None, a, b).ratio()

    def _extract_tool_calls(self, text: str) -> list[dict]:
        """Extract tool call signatures from model output text.

        Handles common formats:
        - JSON tool_call blocks
        - Function call patterns
        - Tool use markers
        """
        calls = []

        # Pattern: JSON-style tool calls
        # Match blocks like: {"name": "read", "parameters": {"path": "foo"}}
        json_tool_pattern = r'\{\s*"name"\s*:\s*"([^"]+)"\s*,\s*"parameters"\s*:\s*(\{[^}]+\})\s*\}'
        for match in re.finditer(json_tool_pattern, text, re.DOTALL):
            try:
                params_str = match.group(2)
                params = json.loads(params_str)
                calls.append({
                    "name": match.group(1),
                    "params": json.dumps(params, sort_keys=True),
                })
            except json.JSONDecodeError:
                pass

        # Pattern: function-like calls
        func_pattern = r'(read|write|exec|edit|browser|web_fetch)\s*\(([^)]*)\)'
        for match in re.finditer(func_pattern, text, re.IGNORECASE):
            tool = match.group(1).lower()
            args = match.group(2).strip()
            if args:
                calls.append({"name": tool, "params": args})

        return calls

    def _log(self, level: str, message: str):
        """Output a log message."""
        timestamp = datetime.now().isoformat()
        # --json is a machine-readable contract: emit exactly one final summary
        # document from main(), never interleave event records with it.
        if self.json_output:
            return
        print(f"[{timestamp}] [{level}] {message}", flush=True)

    def process_block(self, text: str, block_time: float | None = None):
        """Process a single output block from the model.

        Args:
            text: The model output text for this block
            block_time: Timestamp when this block was emitted (unix epoch)
        """
        if block_time is None:
            block_time = time.time()

        text = text.strip()
        if not text:
            return

        self.blocks.append(text)
        self.block_timestamps.append(block_time)

        # Extract tool calls
        tools = self._extract_tool_calls(text)
        for t in tools:
            self.tool_calls.append(t)

        # --- Rule 1: Consecutive identical output blocks ---
        if len(self.blocks) >= self.repeat_threshold:
            recent = list(self.blocks)[-self.repeat_threshold:]
            # Check if all recent blocks are identical or highly similar
            base = recent[0]
            identical = all(
                self._similarity(base, b) >= self.similarity_threshold
                for b in recent[1:]
            )

            if identical:
                duration = block_time - self.block_timestamps[-self.repeat_threshold]
                # Also check duration threshold (default 3 min)
                if duration >= self.time_threshold:
                    self.loop_detected = True
                    self.loop_reason = (
                        f"Detected {self.repeat_threshold}+ consecutive identical "
                        f"output blocks over {duration:.0f}s"
                    )
                    self.loop_details = {
                        "type": "consecutive_identical_output",
                        "repeats": len(recent),
                        "duration_seconds": duration,
                        "sample": base[:200] + ("..." if len(base) > 200 else ""),
                        "block_sizes": [len(b) for b in recent],
                    }
                    self._log(
                        "LOOP_DETECTED",
                        f"{self.loop_reason}\n  Sample: {self.loop_details['sample']}",
                    )

        # --- Rule 2: Same tool called 3+ times with identical params ---
        if len(self.tool_calls) >= self.repeat_threshold:
            recent_tools = list(self.tool_calls)[-self.repeat_threshold:]
            tool_sigs = [(t["name"], t["params"]) for t in recent_tools]
            if len(set(tool_sigs)) == 1:
                self.loop_detected = True
                name, params = tool_sigs[0]
                self.loop_reason = (
                    f"Detected {len(recent_tools)} consecutive identical "
                    f"tool calls: {name}"
                )
                self.loop_details = {
                    "type": "identical_tool_calls",
                    "tool": name,
                    "params": params[:200],
                    "repeats": len(recent_tools),
                }
                self._log(
                    "LOOP_DETECTED",
                    f"{self.loop_reason}\n  Params: {self.loop_details['params']}",
                )

    def reset(self):
        """Reset detector state."""
        self.blocks.clear()
        self.tool_calls.clear()
        self.block_timestamps.clear()
        self.loop_detected = False
        self.loop_reason = ""
        self.loop_details = {}

    def summary(self) -> dict:
        """Return detection summary."""
        return {
            "loop_detected": self.loop_detected,
            "reason": self.loop_reason,
            "details": self.loop_details,
            "blocks_processed": len(self.blocks),
            "tool_calls_tracked": len(self.tool_calls),
        }


def read_from_file(filepath: str) -> list[tuple[str, float]]:
    """Read blocks from a log file.

    Each block is separated by a delimiter line like:
    --- BLOCK N at TIMESTAMP ---

    Returns list of (text, timestamp) tuples.
    """
    path = Path(filepath)
    if not path.exists():
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        sys.exit(2)

    content = path.read_text(encoding="utf-8", errors="replace")
    blocks = []
    current_block: list[str] = []
    current_time = time.time()

    for line in content.splitlines():
        # Match block delimiter
        match = re.match(r"^--- BLOCK (\d+) at (.+) ---$", line)
        if match:
            if current_block:
                blocks.append(("\n".join(current_block), current_time))
                current_block = []
            try:
                ts = datetime.fromisoformat(match.group(2))
                current_time = ts.timestamp()
            except ValueError:
                pass
        else:
            current_block.append(line)

    # Don't forget the last block
    if current_block:
        blocks.append(("\n".join(current_block), current_time))

    return blocks


def read_from_stdin() -> list[tuple[str, float]]:
    """Read blocks from stdin. Blocks are separated by blank lines or
    a configurable delimiter."""
    blocks = []
    current_block: list[str] = []

    for line in sys.stdin:
        if line.strip() == "":
            if current_block:
                blocks.append(("\n".join(current_block), time.time()))
                current_block = []
        else:
            current_block.append(line.rstrip("\n"))

    if current_block:
        blocks.append(("\n".join(current_block), time.time()))

    return blocks


def main():
    parser = argparse.ArgumentParser(
        description="MiMo Stable — Degenerate Loop Detection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  model_output 2>&1 | python3 detect_loop.py
  python3 detect_loop.py --log sample.log
  python3 detect_loop.py --threshold 4 --timeout 300 --log sample.log
  python3 detect_loop.py --json --log sample.log --timeout 180
""",
    )
    parser.add_argument(
        "--log",
        type=str,
        help="Path to log file (reads from stdin if not provided)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=3,
        help="Number of consecutive identical blocks to trigger detection (default: 3)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Minimum duration in seconds for loop detection (default: 180, i.e. 3 min)",
    )
    parser.add_argument(
        "--similarity",
        type=float,
        default=0.95,
        help="Similarity threshold for text comparison (default: 0.95)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results in JSON format",
    )
    args = parser.parse_args()

    # Create detector
    detector = LoopDetector(
        repeat_threshold=args.threshold,
        time_threshold=args.timeout,
        similarity_threshold=args.similarity,
        json_output=args.json,
    )

    # Read input
    if args.log:
        blocks = read_from_file(args.log)
    else:
        blocks = read_from_stdin()

    if not blocks:
        print("Warning: No input blocks found", file=sys.stderr)
        if args.json:
            print(json.dumps({"error": "no_input"}))
        sys.exit(0)

    # Process blocks
    start_time = time.time()
    for text, ts in blocks:
        detector.process_block(text, ts)
        if detector.loop_detected:
            break

    # Output summary
    summary = detector.summary()
    summary["elapsed_seconds"] = time.time() - start_time
    summary["total_blocks"] = len(blocks)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'=' * 60}")
        print(f"Loop Detection Summary")
        print(f"{'=' * 60}")
        print(f"  Blocks processed: {len(blocks)}")
        print(f"  Loop detected:    {'YES ⚠️' if summary['loop_detected'] else 'NO ✅'}")
        if summary["loop_detected"]:
            print(f"  Reason:           {summary['reason']}")
            details = summary.get("details", {})
            if details.get("type") == "consecutive_identical_output":
                print(f"  Repeats:          {details.get('repeats')}")
                print(f"  Duration:         {details.get('duration_seconds', 0):.0f}s")
                print(f"  Sample:           {details.get('sample', 'N/A')}")
            elif details.get("type") == "identical_tool_calls":
                print(f"  Tool:             {details.get('tool')}")
                print(f"  Repeats:          {details.get('repeats')}")
                print(f"  Params:           {details.get('params', 'N/A')}")
        print(f"{'=' * 60}")

    sys.exit(1 if summary["loop_detected"] else 0)


if __name__ == "__main__":
    main()
