"""Public package namespace for mimo-stable."""

__version__ = "1.1.5"

from .events import NormalizedEvent, normalize_event, normalize_events
from .runtime import inspect_events

__all__ = ["NormalizedEvent", "normalize_event", "normalize_events", "inspect_events"]
