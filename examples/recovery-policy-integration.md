# Recovery policy integration

Pipe one JSON detector summary into the conservative recovery policy:

```bash
python3 scripts/detect_loop.py \
  --json \
  --timeout 60 \
  --log fixtures/loop_detected.log \
  > /tmp/loop-summary.json

python3 scripts/recovery_policy.py \
  --summary /tmp/loop-summary.json \
  --retryable \
  --retry-count 0
```

The policy emits a decision, not an operation:

```json
{
  "action": "stop_and_retry_once",
  "detector_type": "consecutive_identical_output",
  "retryable": true,
  "retry_count": 0
}
```

The upper-layer runner owns the side effect. It should apply its own permissions,
idempotency rules, retry budget, logging, and human-review requirements. The
recovery policy never calls a model, tool, scheduler, or process controller.
