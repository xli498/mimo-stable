# Public Specification

## Purpose

`mimo-stable` is a dependency-free runtime guard for detecting repeated or
degraded LLM output and tool-call patterns. It produces a machine-readable
decision for an outer runner to handle.

It is a guardrail and evidence collector, not a model-quality oracle and not a
replacement for full tracing or evaluation platforms.

## Non-goals

- It does not diagnose why a model degraded.
- It does not terminate a model process by itself.
- It does not retry, switch providers, or invoke tools.
- It does not promise a universal threshold for every model or task.
- It does not retain raw sensitive tool payloads in reports.

## Input and output contract

The detector accepts a stream of blocks separated by blank lines, or a log file
using the documented timestamped block delimiter. Callers must redact
credentials, cookies, personal data, and side-effect payloads before passing
input to the detector.

JSON output contains at least `loop_detected`, a stable `reason` when detected,
and structured `details` when available. Repeated text evidence is emitted as a
stable hash and length rather than the original text. Exit codes are `0` for no loop, `1`
for loop detected, and `2` for invalid input or configuration.

The output is evidence for an outer policy layer. It is not proof of model
intent or of a permanent provider failure.

## Detection classes

The current implementation may identify identical or highly similar repeated
output, canonicalized identical consecutive tool calls, repeated side-effect
calls that require review, and configured language drift. A changed-parameter
retry is treated as progress rather than a loop signal.

Thresholds are policy inputs. Defaults are conservative historical starting
points and require calibration against the target runner.

## Policy boundary

The detector reports. A separate policy layer may map the report to actions
such as `continue`, `pause_and_review`, `stop_and_retry_once`, or
`stop_and_escalate`. That layer must preserve idempotency and must not repeat
non-idempotent side effects without an explicit external decision.

## Programmatic API

The package exposes `inspect_events` for a small framework-neutral integration:

```python
from mimo_stable import inspect_events

result = inspect_events(
    [
        {"type": "text", "text": "progress"},
        {"type": "tool_call", "name": "read", "arguments": {"path": "README.md"}},
    ]
)
```

The returned object contains detector evidence and a pure policy suggestion.
The caller still owns process control, retries, provider changes, tool
execution, persistence, and redaction before collection.

## Verification gate

A release is locally verified only when behavior, invalid-input, redaction,
import, CLI, and documentation-contract checks pass. Historical logs are
evidence examples, not statistical proof. New rules require deterministic
fixtures before being described as supported behavior.
