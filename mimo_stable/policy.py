"""Pure recovery decision layer used by the programmatic facade."""
from __future__ import annotations

from typing import Any, Mapping


def decide(summary: dict[str, Any], *, retryable: bool = False, retry_count: int = 0) -> dict[str, Any]:
    """Return a conservative action without performing side effects."""
    if not isinstance(summary, Mapping):
        raise ValueError("summary must be a JSON object")
    detected = summary.get("loop_detected", False)
    if not isinstance(detected, bool):
        raise ValueError("loop_detected must be a JSON boolean")
    if not isinstance(retry_count, int) or isinstance(retry_count, bool) or retry_count < 0:
        raise ValueError("retry_count must be a non-negative integer")
    if not isinstance(retryable, bool):
        raise ValueError("retryable must be a JSON boolean")
    details = summary.get("details", {})
    if details is None:
        details = {}
    if not isinstance(details, Mapping):
        raise ValueError("details must be a JSON object")
    kind = details.get("type")
    if not detected:
        action = "continue"
        rationale = "No degenerate-loop signal was detected."
    elif kind == "repeated_side_effect_tool_call":
        action = "pause_and_review"
        rationale = "A repeated side-effecting call requires an idempotency and outcome check before retry."
    elif retryable and retry_count == 0:
        action = "stop_and_retry_once"
        rationale = "Stop the current generation, then allow one controlled retry with a fresh context or strategy."
    else:
        action = "stop_and_escalate"
        rationale = "Stop the current generation; do not blindly retry after a loop or exhausted retry budget."
    return {
        "action": action,
        "rationale": rationale,
        "detector_reason": summary.get("reason", ""),
        "detector_type": kind,
        "retryable": retryable,
        "retry_count": retry_count,
    }
