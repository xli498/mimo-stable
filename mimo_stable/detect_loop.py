#!/usr/bin/env python3
"""
LLM Degenerate Loop Guardrails — Detection Script

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
import hashlib
import json
import math
import re
import sys
import time
from collections import deque
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path


# Repeating any external action is riskier than repeating a read-only action.
# This set is intentionally conservative: unknown tools retain the normal
# three-repeat rule rather than being guessed as side-effecting.
SIDE_EFFECT_TOOLS = {
    "send", "write", "edit", "delete", "remove", "payment", "pay",
    "purchase", "post", "publish", "create", "submit",
}


class LoopDetector:
    """Detects degenerate loops in model output streams."""

    def __init__(
        self,
        repeat_threshold: int = 3,
        time_threshold: int = 180,
        similarity_threshold: float = 0.95,
        text_mode: str = "duration",
        json_output: bool = False,
        expected_language: str | None = None,
    ):
        if not isinstance(repeat_threshold, int) or isinstance(repeat_threshold, bool) or repeat_threshold < 2:
            raise ValueError("repeat_threshold must be at least 2")
        if not isinstance(time_threshold, int) or isinstance(time_threshold, bool) or time_threshold < 0:
            raise ValueError("time_threshold must be non-negative")
        if (
            not isinstance(similarity_threshold, (int, float))
            or isinstance(similarity_threshold, bool)
            or not math.isfinite(similarity_threshold)
            or not 0.0 <= similarity_threshold <= 1.0
        ):
            raise ValueError("similarity_threshold must be between 0 and 1")
        if text_mode not in {"duration", "instant"}:
            raise ValueError("text_mode must be 'duration' or 'instant'")
        if expected_language not in {None, "zh"}:
            raise ValueError("expected_language must be None or 'zh'")
        self.repeat_threshold = repeat_threshold
        self.time_threshold = time_threshold
        self.similarity_threshold = similarity_threshold
        self.text_mode = text_mode
        self.json_output = json_output
        self.expected_language = expected_language

        # State
        window_size = max(repeat_threshold + 5, 20)
        self.blocks: deque = deque(maxlen=window_size)
        self.tool_calls: deque = deque(maxlen=window_size)
        self.block_timestamps: deque = deque(maxlen=window_size)
        self.loop_detected = False
        self.loop_reason = ""
        self.loop_details: dict = {}

    def _similarity(self, a: str, b: str) -> float:
        """Compute similarity ratio between two strings."""
        return SequenceMatcher(None, a, b).ratio()

    @staticmethod
    def _params_hash(params: str) -> str:
        """Return a stable, non-reversible identifier for logging only."""
        return hashlib.sha256(params.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _block_evidence(text: str) -> dict[str, int | str]:
        """Return non-reversible evidence without retaining output content."""
        return {
            "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
            "text_length": len(text),
        }

    @staticmethod
    def _looks_english(text: str) -> bool:
        """Conservative language-drift heuristic for an explicitly Chinese task."""
        latin = len(re.findall(r"[A-Za-z]", text))
        cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
        return latin >= 10 and cjk == 0

    def _extract_tool_calls(self, text: str) -> list[dict]:
        """Extract tool call signatures from model output text.

        Handles common formats:
        - JSON tool_call blocks
        - Function call patterns
        - Tool use markers
        """
        calls = []

        # Parse JSON objects rather than matching braces. Tool parameters may
        # legitimately contain nested objects, arrays, or escaped strings.
        decoder = json.JSONDecoder()
        for match in re.finditer(r'\{', text):
            try:
                candidate, _ = decoder.raw_decode(text[match.start():])
            except (json.JSONDecodeError, ValueError):
                pass
            else:
                if (
                    isinstance(candidate, dict)
                    and isinstance(candidate.get("name"), str)
                    and isinstance(candidate.get("parameters"), dict)
                ):
                    calls.append({
                        "name": candidate["name"],
                        "params": json.dumps(candidate["parameters"], sort_keys=True),
                    })

        # Function-style calls must begin a line. This avoids treating normal
        # prose such as "I will edit (the draft)" as an actual tool invocation.
        func_pattern = r'^\s*(read|write|exec|edit|browser|web_fetch)\s*\(([^)]*)\)'
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

        # Tool calls must be consecutive output events. A normal model/result
        # block between calls is evidence of progress, so do not carry an old
        # call across it when evaluating a call-loop.
        tools = self._extract_tool_calls(text)
        if not tools:
            self.tool_calls.clear()
        for t in tools:
            self.tool_calls.append(t)

        # A configured Chinese task that repeatedly produces English-only
        # output is an actionable drift signal. It is opt-in because language
        # cannot be inferred safely from an arbitrary log.
        if self.expected_language == "zh" and len(self.blocks) >= self.repeat_threshold:
            recent_blocks = list(self.blocks)[-self.repeat_threshold:]
            if all(self._looks_english(block) for block in recent_blocks):
                self.loop_detected = True
                self.loop_reason = "Detected repeated English-only output in a Chinese task"
                self.loop_details = {
                    "type": "language_drift",
                    "expected_language": "zh",
                    "repeats": len(recent_blocks),
                }
                self._log("LOOP_DETECTED", self.loop_reason)
                return

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
                if self.text_mode == "instant" or duration >= self.time_threshold:
                    self.loop_detected = True
                    self.loop_reason = (
                        f"Detected {self.repeat_threshold}+ consecutive identical "
                        f"output blocks over {duration:.0f}s"
                    )
                    self.loop_details = {
                        "type": "consecutive_identical_output",
                        "signal": "instant" if self.text_mode == "instant" else "duration_gated",
                        "repeats": len(recent),
                        "duration_seconds": duration,
                        "evidence": self._block_evidence(base),
                        "block_sizes": [len(b) for b in recent],
                    }
                    self._log(
                        "LOOP_DETECTED",
                        f"{self.loop_reason}\n  Text hash: {self.loop_details['evidence']['text_hash']}",
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
                    "params_hash": self._params_hash(params),
                    "repeats": len(recent_tools),
                }
                self._log(
                    "LOOP_DETECTED",
                    f"{self.loop_reason}\n  Params hash: {self.loop_details['params_hash']}",
                )

        # External side effects must not be retried blindly. Two identical
        # attempts are enough to stop and require a fresh safety review.
        if len(self.tool_calls) >= 2:
            previous, current = list(self.tool_calls)[-2:]
            if (
                previous["name"] == current["name"]
                and previous["params"] == current["params"]
                and current["name"] in SIDE_EFFECT_TOOLS
            ):
                self.loop_detected = True
                self.loop_reason = f"Detected repeated side-effecting tool call: {current['name']}"
                self.loop_details = {
                    "type": "repeated_side_effect_tool_call",
                    "tool": current["name"],
                    "params_hash": self._params_hash(current["params"]),
                    "repeats": 2,
                }
                self._log("LOOP_DETECTED", self.loop_reason)

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
    if not path.is_file():
        print(f"Error: Not a regular file: {filepath}", file=sys.stderr)
        sys.exit(2)

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"Error: Cannot read {filepath}: {exc}", file=sys.stderr)
        sys.exit(2)
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


def iter_from_stdin():
    """Yield stdin blocks as they close, preserving their arrival time.

    Processing must happen during iteration. Collecting all of stdin before
    evaluating blocks would assign nearly identical timestamps and invalidate
    duration-gated detection for real streamed output.
    """
    current_block: list[str] = []

    for line in sys.stdin:
        if line.strip() == "":
            if current_block:
                yield "\n".join(current_block), time.time()
                current_block = []
        else:
            current_block.append(line.rstrip("\n"))

    if current_block:
        yield "\n".join(current_block), time.time()


def main():
    parser = argparse.ArgumentParser(
        description="LLM Degenerate Loop Guardrails — Detection",
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
        "--text-mode",
        choices=["duration", "instant"],
        default="duration",
        help=(
            "Text-repeat policy: duration requires --timeout; instant triggers "
            "after the repeat threshold (default: duration)"
        ),
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
    parser.add_argument(
        "--expect-language",
        choices=["zh"],
        help="Enable language-drift detection for an explicitly Chinese task",
    )
    args = parser.parse_args()

    if args.threshold < 2:
        parser.error("--threshold must be at least 2")
    if args.timeout < 0:
        parser.error("--timeout must be non-negative")
    if not 0.0 <= args.similarity <= 1.0:
        parser.error("--similarity must be between 0 and 1")

    # Create detector
    detector = LoopDetector(
        repeat_threshold=args.threshold,
        time_threshold=args.timeout,
        similarity_threshold=args.similarity,
        text_mode=args.text_mode,
        json_output=args.json,
        expected_language=args.expect_language,
    )

    # Read input
    start_time = time.time()
    total_blocks = 0
    if args.log:
        blocks = read_from_file(args.log)
        for text, ts in blocks:
            total_blocks += 1
            detector.process_block(text, ts)
            if detector.loop_detected:
                break
    else:
        for text, ts in iter_from_stdin():
            total_blocks += 1
            detector.process_block(text, ts)
            if detector.loop_detected:
                break

    if total_blocks == 0:
        print("Warning: No input blocks found", file=sys.stderr)
        if args.json:
            print(json.dumps({"error": "no_input"}))
        sys.exit(2)

    # Output summary
    summary = detector.summary()
    summary["elapsed_seconds"] = time.time() - start_time
    summary["total_blocks"] = total_blocks

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'=' * 60}")
        print(f"Loop Detection Summary")
        print(f"{'=' * 60}")
        print(f"  Blocks processed: {total_blocks}")
        print(f"  Loop detected:    {'YES ⚠️' if summary['loop_detected'] else 'NO ✅'}")
        if summary["loop_detected"]:
            print(f"  Reason:           {summary['reason']}")
            details = summary.get("details", {})
            if details.get("type") == "consecutive_identical_output":
                print(f"  Repeats:          {details.get('repeats')}")
                print(f"  Duration:         {details.get('duration_seconds', 0):.0f}s")
                evidence = details.get("evidence", {})
                print(f"  Text hash:        {evidence.get('text_hash', 'N/A')}")
                print(f"  Text length:      {evidence.get('text_length', 'N/A')}")
            elif details.get("type") == "identical_tool_calls":
                print(f"  Tool:             {details.get('tool')}")
                print(f"  Repeats:          {details.get('repeats')}")
                print(f"  Params hash:      {details.get('params_hash', 'N/A')}")
            elif details.get("type") == "repeated_side_effect_tool_call":
                print(f"  Tool:             {details.get('tool', 'N/A')}")
                print("  Action:           pause and re-check before retrying")
        print(f"{'=' * 60}")

    sys.exit(1 if summary["loop_detected"] else 0)


if __name__ == "__main__":
    main()
