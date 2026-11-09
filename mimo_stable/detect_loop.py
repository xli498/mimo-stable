#!/usr/bin/env python3
"""
LLM Degenerate Loop Guardrails — Detection Script

Detects degenerate loops in model output streams where the model
repeatedly emits identical or near-identical text blocks.

Detection rules:
  1. Consecutive identical output blocks (3+ repeats)
  2. Same tool called 3+ times with identical parameters
     (top-level volatile keys such as request_id are ignored;
      the same key nested inside a business payload is kept)
  3. Output duration exceeding threshold without meaningful change
  4. Tool-call cycles (a 2-4 step pattern repeating twice)
  5. Repeated n-grams inside a single long block
  6. Optional tool-call budget ceiling (opt-in only)

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

# Keys that carry per-attempt metadata rather than the intent of a call.
# Two calls that differ only in these keys are the same call. Stripping them
# keeps a loop from hiding behind regenerated request ids or timestamps.
# Deliberately narrow in two ways: keys such as "id", "offset" or "path"
# stay, because they can change what the call does; and stripping applies
# only to the top level of the arguments object, because a field of the same
# name nested inside a business payload (for example a "timestamp" inside a
# list of records being written) is business content, not call metadata.
# Callers that carry metadata in a nested object must hoist it to the top
# level for it to be ignored.
VOLATILE_PARAM_KEYS = {
    "request_id", "trace_id", "created_at", "timestamp", "nonce",
}

# A repeating read/write pattern (read -> edit -> read -> edit) is normal
# repair work, not a loop. A cycle is only a signal when it does not mix
# both sides. The sets are intentionally small; unknown tools count as
# neither, so an unknown-tool cycle still triggers.
READ_TOOLS = {
    "read", "browser", "web_fetch", "grep", "find", "search",
    "list", "cat", "glob", "view",
}
WRITE_TOOLS = {
    "write", "edit", "create", "delete", "remove", "send", "post",
    "publish", "submit", "exec", "apply_patch", "move", "rename",
    "update", "insert",
}

# Cycle detection: pattern lengths to look for, and how many complete
# repetitions of the pattern count as a loop. Two full cycles matches the
# published behavior of comparable guards without waiting for a long stall.
CYCLE_PERIODS = (2, 3, 4)
CYCLE_REPEATS = 2

# Within-block n-gram repetition: a single long block whose own phrases
# repeat. Both bars must clear, so boilerplate in a long answer does not
# trigger on a few repeated lines.
NGRAM_WORDS = 5
NGRAM_MIN_WORDS = 50
NGRAM_MIN_OCCURRENCES = 5
NGRAM_MIN_COVERAGE = 0.5


def strip_volatile_params(params):
    """Return a copy of tool parameters without top-level volatile keys.

    Only the top level of the arguments object is denoised: that is where
    per-attempt call metadata lives. Nested values are business payload and
    are preserved verbatim, so two writes that differ only in a timestamp
    inside the content being written stay different calls (M1). Non-dict
    inputs are returned unchanged.
    """
    if isinstance(params, dict):
        return {
            key: value
            for key, value in params.items()
            if key not in VOLATILE_PARAM_KEYS
        }
    return params


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
        max_tool_calls: int | None = None,
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
        if max_tool_calls is not None and (
            not isinstance(max_tool_calls, int)
            or isinstance(max_tool_calls, bool)
            or max_tool_calls < 1
        ):
            raise ValueError("max_tool_calls must be None or a positive integer")
        self.repeat_threshold = repeat_threshold
        self.time_threshold = time_threshold
        self.similarity_threshold = similarity_threshold
        self.text_mode = text_mode
        self.json_output = json_output
        self.expected_language = expected_language
        self.max_tool_calls = max_tool_calls

        # State
        window_size = max(repeat_threshold + 5, 20)
        self.blocks: deque = deque(maxlen=window_size)
        self.tool_calls: deque = deque(maxlen=window_size)
        self.block_timestamps: deque = deque(maxlen=window_size)
        # Cycle signatures accumulate across result blocks: a real agent loop
        # is call -> result -> call -> result, so clearing on every plain
        # block would make cycles unreachable. Bounded separately.
        self.cycle_signatures: deque = deque(maxlen=24)
        self.total_tool_calls = 0
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

        One JSON object is one call record: once an object is identified as
        a tool call, scanning resumes after it, so a nested example inside
        its parameters is never counted as a second call (M3). A JSON object
        that is not itself a call record is still scanned inside, so a call
        wrapped in another object is found. Function-style calls may start
        on any line, so several calls in one block are all counted (M3).
        When a function-style argument parses as a JSON object it is
        normalized exactly like a JSON record (keys sorted, top-level
        volatile keys ignored); any other argument syntax is compared
        verbatim (M5). When a JSON record and a function-style call overlap
        in the text, only the outer construct is kept, so the same call is
        never counted twice.
        """
        candidates: list[tuple[int, int, dict]] = []

        # Parse JSON objects rather than matching braces. Tool parameters may
        # legitimately contain nested objects, arrays, or escaped strings.
        decoder = json.JSONDecoder()
        pos = 0
        while True:
            brace = re.search(r"\{", text[pos:])
            if brace is None:
                break
            start = pos + brace.start()
            try:
                candidate, end = decoder.raw_decode(text[start:])
            except (json.JSONDecodeError, ValueError):
                pos = start + 1
                continue
            if (
                isinstance(candidate, dict)
                and isinstance(candidate.get("name"), str)
                and isinstance(candidate.get("parameters"), dict)
            ):
                params = strip_volatile_params(candidate["parameters"])
                candidates.append((
                    start,
                    start + end,
                    {
                        "name": candidate["name"],
                        "params": json.dumps(params, sort_keys=True),
                    },
                ))
                pos = start + end
            else:
                pos = start + 1

        # Function-style calls must begin a line. This avoids treating normal
        # prose such as "I will edit (the draft)" as an actual tool invocation.
        func_pattern = r"^\s*(read|write|exec|edit|browser|web_fetch)\s*\(([^)]*)\)"
        for match in re.finditer(func_pattern, text, re.IGNORECASE | re.MULTILINE):
            tool = match.group(1).lower()
            args = match.group(2).strip()
            if not args:
                continue
            params: str = args
            try:
                parsed = json.loads(args)
            except (json.JSONDecodeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                params = json.dumps(
                    strip_volatile_params(parsed), sort_keys=True
                )
            candidates.append((match.start(), match.end(), {
                "name": tool, "params": params,
            }))

        # Keep candidates in text order, dropping any candidate strictly
        # inside one that starts earlier (the outer construct wins).
        candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
        kept: list[tuple[int, int, dict]] = []
        for candidate in candidates:
            if any(
                candidate[0] < other[1] and other[0] < candidate[1]
                for other in kept
            ):
                continue
            kept.append(candidate)
        kept.sort(key=lambda item: item[0])
        return [call for _, _, call in kept]

    def _check_tool_cycle(self):
        """Detect a repeating tool-call cycle (A-B-A-B, up to 4-step patterns).

        Looks at the most recent signatures only. Returns True when a
        pattern of CYCLE_PERIODS length has repeated CYCLE_REPEATS times,
        unless the pattern mixes read-class and write-class tools (normal
        repair work). Only the tool names and a hash of the pattern are
        reported; parameter values never enter the output. Callers check
        after every call is appended (M4), so a cycle is caught the moment
        it completes even if later calls in the same block follow it.
        """
        signatures = list(self.cycle_signatures)
        for period in CYCLE_PERIODS:
            needed = period * CYCLE_REPEATS
            if len(signatures) < needed:
                continue
            window = signatures[-needed:]
            pattern = window[:period]
            if window[period:] != pattern:
                continue
            tools = {name for name, _ in pattern}
            if tools & READ_TOOLS and tools & WRITE_TOOLS:
                continue
            pattern_text = json.dumps(pattern, sort_keys=True)
            self.loop_detected = True
            self.loop_reason = (
                f"Detected tool-call cycle: {period}-step pattern repeated "
                f"{CYCLE_REPEATS} times"
            )
            self.loop_details = {
                "type": "tool_call_cycle",
                "period": period,
                "repeats": CYCLE_REPEATS,
                "tools": sorted(tools),
                "pattern_hash": hashlib.sha256(pattern_text.encode("utf-8")).hexdigest()[:16],
            }
            self._log("LOOP_DETECTED", self.loop_reason)
            return True
        return False

    def _repeated_ngram_evidence(self, text: str) -> dict | None:
        """Detect repetition inside one long block.

        A 5-word shingle must occur at least 5 times and repeated material
        must cover at least half of the block. Short blocks are skipped:
        below 50 words there is not enough material to judge. Occurrence
        count and coverage are computed over the same set of phrases: only
        phrases occurring at least 5 times contribute coverage (M2), so a
        block cannot clear the coverage bar with phrases that merely
        occurred twice.
        """
        words = text.split()
        if len(words) < NGRAM_MIN_WORDS:
            return None
        lowered = [word.casefold() for word in words]
        starts: dict[tuple[str, ...], list[int]] = {}
        for index in range(len(lowered) - NGRAM_WORDS + 1):
            key = tuple(lowered[index:index + NGRAM_WORDS])
            starts.setdefault(key, []).append(index)
        max_occurrences = max(len(positions) for positions in starts.values())
        if max_occurrences < NGRAM_MIN_OCCURRENCES:
            return None
        covered: set[int] = set()
        for positions in starts.values():
            if len(positions) >= NGRAM_MIN_OCCURRENCES:
                for start in positions:
                    covered.update(range(start, start + NGRAM_WORDS))
        coverage = len(covered) / len(words)
        if coverage < NGRAM_MIN_COVERAGE:
            return None
        return {
            "type": "repeated_ngram_within_block",
            "ngram_words": NGRAM_WORDS,
            "occurrences": max_occurrences,
            "block_words": len(words),
            "coverage": round(coverage, 3),
            "evidence": self._block_evidence(text),
        }

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
            # Cycle signatures accumulate across result blocks; the
            # consecutive-call deque above is cleared by plain blocks.
            self.cycle_signatures.append((t["name"], t["params"]))
            # Check on every call, not only at the end of the block (M4):
            # a cycle completes the moment its last call arrives, and later
            # calls in the same block must not mask it.
            if not self.loop_detected:
                self._check_tool_cycle()
        self.total_tool_calls += len(tools)

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

        # --- Rule 3: Tool-call cycle (A-B-A-B) ---
        if not self.loop_detected:
            self._check_tool_cycle()

        # --- Rule 4: Repeated n-grams inside one long block ---
        if not self.loop_detected:
            ngram = self._repeated_ngram_evidence(text)
            if ngram is not None:
                self.loop_detected = True
                self.loop_reason = (
                    f"Detected repeated {ngram['ngram_words']}-word phrase "
                    f"{ngram['occurrences']} times inside one block"
                )
                self.loop_details = ngram
                self._log("LOOP_DETECTED", self.loop_reason)

        # --- Rule 5 (opt-in): Tool-call budget ceiling ---
        if (
            not self.loop_detected
            and self.max_tool_calls is not None
            and self.total_tool_calls > self.max_tool_calls
        ):
            self.loop_detected = True
            self.loop_reason = (
                f"Tool-call budget exceeded: {self.total_tool_calls} calls "
                f"(limit {self.max_tool_calls})"
            )
            self.loop_details = {
                "type": "tool_call_budget_exceeded",
                "total_tool_calls": self.total_tool_calls,
                "max_tool_calls": self.max_tool_calls,
            }
            self._log("LOOP_DETECTED", self.loop_reason)

    def reset(self):
        """Reset detector state."""
        self.blocks.clear()
        self.tool_calls.clear()
        self.block_timestamps.clear()
        self.cycle_signatures.clear()
        self.total_tool_calls = 0
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
            "total_tool_calls": self.total_tool_calls,
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
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=None,
        help=(
            "Optional last-resort ceiling: flag once the total number of "
            "tool calls exceeds N (default: no ceiling)"
        ),
    )
    args = parser.parse_args()

    if args.threshold < 2:
        parser.error("--threshold must be at least 2")
    if args.timeout < 0:
        parser.error("--timeout must be non-negative")
    if not 0.0 <= args.similarity <= 1.0:
        parser.error("--similarity must be between 0 and 1")
    if args.max_tool_calls is not None and args.max_tool_calls < 1:
        parser.error("--max-tool-calls must be a positive integer")

    # Create detector
    detector = LoopDetector(
        repeat_threshold=args.threshold,
        time_threshold=args.timeout,
        similarity_threshold=args.similarity,
        text_mode=args.text_mode,
        json_output=args.json,
        expected_language=args.expect_language,
        max_tool_calls=args.max_tool_calls,
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
