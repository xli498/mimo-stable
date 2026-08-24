# Tool-call detection

The detector can inspect common JSON-style tool calls and function-like calls.
Use instant text mode for a short fixture demonstration:

```bash
python3 scripts/detect_loop.py \
  --json \
  --text-mode instant \
  --log fixtures/tool_key_order_repeat.log
```

Repeated identical calls produce a signal such as:

```json
{
  "loop_detected": true,
  "details": {
    "type": "identical_tool_calls",
    "tool": "read"
  }
}
```

A repeated side-effecting call is treated more conservatively:

```bash
python3 scripts/detect_loop.py \
  --json \
  --log fixtures/side_effect_repeat.log
```

The detector does not execute, cancel, or retry the tool. The outer runner must
perform its own idempotency and outcome checks before taking action.
