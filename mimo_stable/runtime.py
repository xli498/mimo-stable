"""Small programmatic runtime guard facade."""
from __future__ import annotations

import math
from typing import Any, Mapping

from .detect_loop import LoopDetector
from .events import normalize_event
from .policy import decide


def inspect_events(
    events: list[str | Mapping[str, Any]],
    *,
    repeat_threshold: int = 3,
    time_threshold: int = 180,
    similarity_threshold: float = 0.95,
    text_mode: str = "duration",
    expected_language: str | None = None,
    retryable: bool = False,
    retry_count: int = 0,
) -> dict[str, Any]:
    """Normalize and inspect events, returning detector and policy evidence."""
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    if not isinstance(retry_count, int) or isinstance(retry_count, bool) or retry_count < 0:
        raise ValueError("retry_count must be a non-negative integer")
    if not isinstance(retryable, bool):
        raise ValueError("retryable must be a boolean")
    if not isinstance(similarity_threshold, (int, float)) or isinstance(similarity_threshold, bool) or not math.isfinite(similarity_threshold):
        raise ValueError("similarity_threshold must be between 0 and 1")
    detector = LoopDetector(
        repeat_threshold=repeat_threshold,
        time_threshold=time_threshold,
        similarity_threshold=similarity_threshold,
        text_mode=text_mode,
        expected_language=expected_language,
        json_output=True,
    )
    for raw in events:
        event = normalize_event(raw)
        detector.process_block(event.text, event.timestamp)
        if detector.loop_detected:
            break
    summary = detector.summary()
    summary["total_events"] = len(events)
    summary["policy"] = decide(summary, retryable=retryable, retry_count=retry_count)
    return summary
