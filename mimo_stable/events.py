"""Framework-neutral event normalization for the loop detector.

The adapter intentionally exposes only redacted, detector-ready blocks. It
does not execute tools or retain provider-specific event objects.
"""
from __future__ import annotations

import json
import math
import hashlib
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class NormalizedEvent:
    """A single detector input block with an optional source timestamp."""

    text: str
    timestamp: float | None = None


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("event text must be a non-empty string")
    return value.strip()


def _tool_text(name: Any, arguments: Any) -> str:
    tool_name = _text(name)
    if isinstance(arguments, str):
        try:
            params = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise ValueError("tool argument string must contain a JSON object") from exc
        if not isinstance(params, dict):
            raise ValueError("tool argument string must contain a JSON object")
    elif isinstance(arguments, Mapping):
        params = dict(arguments)
    else:
        raise ValueError("tool arguments must be a JSON string or object")
    try:
        canonical = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("tool arguments must be JSON-serializable") from exc
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    # Preserve repeat detection without retaining tool argument values in the
    # detector input or in a potential repeated-text sample.
    return json.dumps(
        {"name": tool_name, "parameters": {"_fingerprint": fingerprint}},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def normalize_event(event: str | Mapping[str, Any]) -> NormalizedEvent:
    """Normalize a supported text or tool-call event.

    Supported mappings are deliberately explicit:

    - ``{"type": "text", "text": "..."}``
    - ``{"type": "tool_call", "name": "...", "arguments": {...}}``
    - ``{"type": "assistant", "content": "..."}``
    - ``{"type": "assistant", "content": [{"type": "text", "text": "..."}]}``

    A plain string is treated as one text block. Unknown shapes raise
    ``ValueError`` instead of being silently coerced into evidence.
    """
    if isinstance(event, str):
        return NormalizedEvent(_text(event))
    if not isinstance(event, Mapping):
        raise ValueError("event must be a string or object")

    timestamp = event.get("timestamp")
    if timestamp is not None and (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
        or not math.isfinite(timestamp)
    ):
        raise ValueError("event timestamp must be numeric")
    kind = event.get("type")

    if kind == "text":
        return NormalizedEvent(_text(event.get("text")), timestamp)
    if kind == "tool_call":
        return NormalizedEvent(_tool_text(event.get("name"), event.get("arguments")), timestamp)
    if kind == "assistant":
        content = event.get("content")
        if isinstance(content, str):
            return NormalizedEvent(_text(content), timestamp)
        if isinstance(content, list) and len(content) == 1:
            part = content[0]
            if isinstance(part, Mapping) and part.get("type") == "text":
                return NormalizedEvent(_text(part.get("text")), timestamp)

    raise ValueError(f"unsupported event type: {kind!r}")


def normalize_events(events: list[str | Mapping[str, Any]]) -> list[NormalizedEvent]:
    """Normalize a finite event batch while preserving order."""
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    return [normalize_event(event) for event in events]
