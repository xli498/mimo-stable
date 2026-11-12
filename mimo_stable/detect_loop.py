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
        # Kind of each block, aligned with ``blocks``: "text" for natural
        # language output, "tool_call" for a structured tool-call record.
        # The language rule only applies to natural-language output (M3,
        # 2026-11-12 all-repo review): a tool call's normalized form is
        # English-looking JSON by construction and must never count as
        # language-drift evidence.
        self.block_kinds: deque = deque(maxlen=window_size)
        # Start of the current run of consecutive identical blocks, for
        # the duration gate (M2, 2026-11-12 all-repo review): the gate
        # must measure the whole run, not just the last three blocks.
        # The run is exact repetition of the same output text. A
        # similarity test would be wrong here: near-identical tool
        # records for different files (read file01, file02, ...) are
        # distinct work, not a repeated output, and must not accumulate
        # into a duration signal (that case belongs to the opt-in
        # tool-call budget, not to Rule 1).
        self._identical_run_base: str | None = None
        self._identical_run_start: float | None = None
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

    @staticmethod
    def _scan_balanced_parens(text: str, open_index: int) -> tuple[str, int] | None:
        """Return (arguments, end) for the call opening at ``open_index``.

        Scans for the parenthesis matching the ``(`` at ``open_index``,
        ignoring parentheses inside single- or double-quoted strings and
        honouring backslash escapes inside them, so a ``)`` in a JSON
        string value (or an escaped quote just before one) does not end
        the arguments early (M5 boundary). Nested parentheses outside
        strings are counted. Returns ``None`` when the parentheses never
        balance: an unparseable prefix must never be used as a call
        signature. ``end`` is the index just past the closing ``)``.
        """
        depth = 0
        quote: str | None = None
        index = open_index
        while index < len(text):
            char = text[index]
            if quote is not None:
                if char == "\\":
                    index += 2
                    continue
                if char == quote:
                    quote = None
                index += 1
                continue
            if char in ("'", '"'):
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return text[open_index + 1:index], index + 1
            index += 1
        return None

    @staticmethod
    def _ends_call_record(text: str, end: int) -> bool:
        """Return True when ``end`` (just past ``)``) ends a call record.

        A complete function-style call record occupies its line: only
        whitespace may follow the closing parenthesis before the end of
        the line (or of the text). Anything else on the same line -- a
        word, punctuation, another token -- means the line-start name
        is prose mentioning the tool (e.g. ``write() denotes ...``),
        not a call. Prose on a *later* line does not affect the call:
        only the remainder of the call's own line is examined.
        """
        return text[end:].split("\n", 1)[0].strip() == ""

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
        verbatim (M5). A zero-argument call (``browser()``) counts with
        params ``"{}"``, the same normalized form the structured API
        produces for ``arguments: {}``, so CLI and API agree on the
        tool-call budget -- but only when the closing ``)`` ends the
        call record: only whitespace may follow it on the same line.
        A line-start name followed by prose on the same line (e.g.
        ``write() denotes the writer interface ...`` or
        ``write({"path": ...}) denotes ...``) is ordinary
        documentation mentioning the tool, not a call, and is not
        counted (line-start prose Must, PR #3 re-review 3 for
        zero-argument calls, re-review 4 for calls with arguments):
        the closing ``)`` must end the call record in both branches,
        i.e. only whitespace may follow it on the same line. The prose
        exclusion also covers a tool-shaped JSON object nested inside
        the excluded mention's arguments (e.g. the ``send`` inside
        ``write({"name": "send", ...}) denotes ...``): the JSON scan
        runs first and would otherwise leave that inner candidate
        standing after the outer mention is dropped (M7, PR #3
        re-review 6). The same holds when the parentheses never
        balance and the nested JSON spans several lines: the
        exclusion span stretches to cover a JSON candidate that
        starts on the mention's line, instead of stopping at that
        line's end (M7 unbalanced variant, PR #3 re-review 7).
        It also holds when the argument opens with an ordinary
        (non-tool-shaped) config JSON object and the tool-shaped
        object is nested inside it on a later line: the span
        stretches to the end of that outer object, so the inner
        candidate falls inside it (M7 outer-config variant, PR #3
        re-review 8).
        The closing parenthesis is found by a quote-aware
        scan, so a ")" inside a quoted string (or an escaped quote just
        before one) does not end the arguments early (M5 boundary); a call
        whose parentheses never balance is not counted at all, because an
        unparseable argument prefix must never be used as a call signature.
        When a JSON record and a function-style call overlap
        in the text, only the outer construct is kept, so the same call is
        never counted twice.
        """
        candidates: list[tuple[int, int, dict]] = []

        # Parse JSON objects rather than matching braces. Tool parameters may
        # legitimately contain nested objects, arrays, or escaped strings.
        # Every successfully decoded JSON object is also recorded in
        # ``json_spans`` (tool-shaped or not): the prose-exclusion logic
        # below needs the span of an outer config object that merely
        # *contains* a tool-shaped object (M7 outer-config variant,
        # PR #3 re-review 8).
        decoder = json.JSONDecoder()
        json_spans: list[tuple[int, int]] = []
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
            json_spans.append((start, start + end))
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
        # The arguments are not captured with a regex: the closing parenthesis
        # is found by a quote-aware scan, so a ")" inside a quoted string
        # does not truncate the arguments (M5 boundary).
        func_header = re.compile(
            r"^\s*(read|write|exec|edit|browser|web_fetch)\s*\(",
            re.IGNORECASE | re.MULTILINE,
        )
        # Spans of line-start function-style mentions that the
        # complete-call-record check rejected as prose (M7): a prose
        # mention's arguments may themselves contain a tool-shaped JSON
        # object (e.g. ``write({"name": "send", ...}) denotes ...``),
        # which the JSON scan above already recorded as a candidate for
        # the inner ``send``. The prose exclusion must cover those inner
        # candidates too, or the outer exclusion is undone by the inner
        # one surviving the overlap dedup below.
        excluded_spans: list[tuple[int, int]] = []
        for header in func_header.finditer(text):
            tool = header.group(1).lower()
            scanned = self._scan_balanced_parens(text, header.end() - 1)
            if scanned is None:
                # Parentheses never balance: there is no complete call
                # here, so the line-start mention is prose; exclude any
                # tool-shaped JSON candidate on the same line (up to the
                # end of the header's line only, so later lines are
                # unaffected). A tool-shaped JSON candidate that *starts*
                # on the header's line may itself span several lines
                # (e.g. ``write({\n "name": "send", ...\n}`` with no
                # closing paren): the span must stretch to cover that
                # whole candidate, or the fully-inside filter below
                # keeps it and the prose mention is undone by its inner
                # ``send`` (M7 unbalanced variant, PR #3 re-review 7).
                # Only candidates starting inside the span extend it;
                # a candidate starting on a later line (e.g. a real
                # call after the prose) is untouched, and a candidate
                # that merely overlaps from before the header is not
                # swallowed either. The header's line is measured from
                # the tool name, not from the match start: the leading
                # ``\s*`` in the header pattern can swallow the
                # preceding newline, which would otherwise make the
                # line end the previous (blank) line and miss a
                # candidate starting on the header's own line.
                name_start = header.start(1)
                line_end = text.find("\n", name_start)
                span_end = len(text) if line_end == -1 else line_end
                # M7 outer-config variant (PR #3 re-review 8): the
                # argument may open with an ordinary config JSON object
                # (e.g. ``write({"path": "config.json", "content":
                # {"name": "send", ...}}`` with no closing paren). That
                # outer object is not tool-shaped, so it never enters
                # ``candidates``, and the inner ``send`` candidate starts
                # on a later line -- outside the header's line, so the
                # candidate-extension loop below never reaches it. When
                # only whitespace lies between the ``(`` and the opening
                # ``{`` of such an object (same line, or the ``{`` moved
                # to the next line), the argument *is* that object:
                # stretch the span to the end of it, so the nested
                # candidate falls inside the span and the fully-inside
                # filter drops it. The whitespace-only guard keeps a
                # real call on a later line out of the span: anything
                # after the outer object ends is untouched, and a
                # non-JSON argument cannot drag a distant ``{`` into
                # the span. The candidate-extension loop then runs over
                # the stretched span as before.
                open_paren = header.end() - 1
                for obj_start, obj_end in json_spans:
                    if obj_start >= open_paren and text[
                        open_paren + 1:obj_start
                    ].strip() == "":
                        span_end = max(span_end, obj_end)
                        break
                for cand_start, cand_end, _ in candidates:
                    if header.start() <= cand_start < span_end:
                        span_end = max(span_end, cand_end)
                excluded_spans.append((header.start(), span_end))
                continue
            args, end = scanned
            args = args.strip()
            if not args:
                # Zero-argument call (e.g. ``browser()``): count it, with
                # params normalized to "{}" exactly as the structured API
                # normalizes ``arguments: {}``. Dropping it here made the
                # CLI silently disagree with the API on the tool-call
                # budget (empty-args Must, PR #3 re-review 2). But the
                # call must end its line: a line-start name followed by
                # prose on the same line (``write() denotes ...``) is
                # documentation mentioning the tool, not a call, and is
                # skipped (line-start prose Must, PR #3 re-review 3).
                if not self._ends_call_record(text, end):
                    excluded_spans.append((header.start(), end))
                    continue
                candidates.append((header.start(), end, {
                    "name": tool, "params": "{}",
                }))
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
            # Same complete-call-record boundary as the zero-argument
            # branch: the closing ")" must end the call's line (only
            # whitespace may follow it). A line-start name with
            # arguments followed by prose on the same line (e.g.
            # ``write({"path": ...}) denotes ...``) is documentation
            # mentioning the tool, not a call, and is skipped
            # (line-start prose Must, PR #3 re-review 4). Prose on a
            # later line does not affect the call.
            if not self._ends_call_record(text, end):
                excluded_spans.append((header.start(), end))
                continue
            candidates.append((header.start(), end, {
                "name": tool, "params": params,
            }))

        # Drop any candidate fully inside a prose-excluded function-style
        # span (M7). The JSON scan runs before the function-style prose
        # check, so without this the inner tool-shaped JSON of a prose
        # mention (e.g. the ``send`` inside
        # ``write({"name": "send", ...}) denotes ...``) would survive
        # even though the outer mention was correctly excluded. A real
        # standalone call passes the record check and is never in an
        # excluded span, so it is unaffected; the overlap dedup below
        # still drops inner candidates of a kept outer call.
        if excluded_spans:
            candidates = [
                candidate
                for candidate in candidates
                if not any(
                    candidate[0] >= start and candidate[1] <= end
                    for start, end in excluded_spans
                )
            ]

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

    def _check_identical_tool_calls(self):
        """Flag ``repeat_threshold`` consecutive identical tool calls (Rule 2).

        Checked after every call is appended, not only at the end of a
        block (M1, 2026-11-12 all-repo review): a trailing different call
        in the same block must not mask a repeat that already completed
        mid-block. Returns True when a loop was flagged.
        """
        if len(self.tool_calls) < self.repeat_threshold:
            return False
        recent_tools = list(self.tool_calls)[-self.repeat_threshold:]
        tool_sigs = [(t["name"], t["params"]) for t in recent_tools]
        if len(set(tool_sigs)) != 1:
            return False
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
        return True

    def _check_repeated_side_effect(self):
        """Flag two identical side-effecting tool calls (Rule 2b).

        Checked after every call is appended, for the same reason as
        ``_check_identical_tool_calls`` (M1, 2026-11-12 all-repo review):
        the second identical side-effecting call is already a stop
        signal, even if a different call follows it in the same block.
        Returns True when a loop was flagged.
        """
        if len(self.tool_calls) < 2:
            return False
        previous, current = list(self.tool_calls)[-2:]
        if not (
            previous["name"] == current["name"]
            and previous["params"] == current["params"]
            and current["name"] in SIDE_EFFECT_TOOLS
        ):
            return False
        self.loop_detected = True
        self.loop_reason = f"Detected repeated side-effecting tool call: {current['name']}"
        self.loop_details = {
            "type": "repeated_side_effect_tool_call",
            "tool": current["name"],
            "params_hash": self._params_hash(current["params"]),
            "repeats": 2,
        }
        self._log("LOOP_DETECTED", self.loop_reason)
        return True

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

    def process_block(self, text: str, block_time: float | None = None, event_kind: str | None = None):
        """Process a single output block from the model.

        Args:
            text: The model output text for this block
            block_time: Timestamp when this block was emitted (unix epoch)
            event_kind: "text" for natural-language output, "tool_call"
                for a structured tool-call record. When None the kind is
                inferred from the tool calls extracted from the block.
                The language rule only applies to "text" blocks (M3).
        """
        if block_time is None:
            block_time = time.time()

        text = text.strip()
        if not text:
            return

        self.blocks.append(text)
        self.block_timestamps.append(block_time)

        # Track the run of consecutive identical blocks: the duration gate
        # (Rule 1) measures this run, not the last three blocks (M2,
        # 2026-11-12 all-repo review). A run that has been repeating the
        # same text for ten minutes must clear a three-minute gate even
        # though any three consecutive blocks span only twenty seconds.
        # Exact repetition only: a block that merely resembles the run
        # base (different file, one word changed) starts a new run, so
        # distinct work never accumulates into a duration signal.
        if text != self._identical_run_base:
            self._identical_run_base = text
            self._identical_run_start = block_time

        # Tool calls must be consecutive output events. A normal model/result
        # block between calls is evidence of progress, so do not carry an old
        # call across it when evaluating a call-loop.
        tools = self._extract_tool_calls(text)
        if event_kind is None:
            kind = "tool_call" if tools else "text"
        elif event_kind in ("text", "tool_call"):
            kind = event_kind
        else:
            raise ValueError("event_kind must be 'text' or 'tool_call'")
        self.block_kinds.append(kind)
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
            # Same per-call rule for identical calls and repeated side
            # effects (M1, 2026-11-12 all-repo review): a repeat that
            # already completed mid-block is a signal worth keeping, even
            # if a different call follows it in the same block.
            if not self.loop_detected:
                self._check_identical_tool_calls()
            if not self.loop_detected:
                self._check_repeated_side_effect()
        self.total_tool_calls += len(tools)

        # A configured Chinese task that repeatedly produces English-only
        # output is an actionable drift signal. It is opt-in because language
        # cannot be inferred safely from an arbitrary log.
        if self.expected_language == "zh" and len(self.blocks) >= self.repeat_threshold:
            recent_blocks = list(self.blocks)[-self.repeat_threshold:]
            recent_kinds = list(self.block_kinds)[-self.repeat_threshold:]
            # Only natural-language output counts (M3, 2026-11-12 all-repo
            # review): a structured tool-call record is English-looking
            # JSON by construction -- its arguments are replaced by a
            # fingerprint -- so it must never count as drift evidence.
            if all(kind == "text" for kind in recent_kinds) and all(
                self._looks_english(block) for block in recent_blocks
            ):
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
        # Guarded like Rules 4-5: a signal already raised earlier in this
        # block (e.g. identical tool calls, checked per call) is kept,
        # not overwritten by a later rule (M1, 2026-11-12 all-repo review).
        # Before the per-call move, the end-of-block Rule 2 ran after
        # Rule 1 and overwrote its verdict; the first signal now wins.
        if not self.loop_detected and len(self.blocks) >= self.repeat_threshold:
            recent = list(self.blocks)[-self.repeat_threshold:]
            # Check if all recent blocks are identical or highly similar
            base = recent[0]
            identical = all(
                self._similarity(base, b) >= self.similarity_threshold
                for b in recent[1:]
            )

            if identical:
                # Duration of the whole consecutive-identical run (M2,
                # 2026-11-12 all-repo review), not just the last three
                # blocks: with ten-second spacing the last three blocks
                # always span twenty seconds and can never reach a
                # three-minute gate, however long the run has lasted.
                duration = block_time - self._identical_run_start
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
        self.block_kinds.clear()
        self._identical_run_base = None
        self._identical_run_start = None
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
