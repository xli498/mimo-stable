# Basic text detection

Use the included normalized fixture to detect repeated output blocks:

```bash
python3 scripts/detect_loop.py \
  --json \
  --text-mode instant \
  --log fixtures/loop_detected.log
```

The command exits with status `1` when a loop signal is detected. The JSON summary
can be consumed by an outer runner:

```json
{
  "loop_detected": true,
  "details": {
    "type": "consecutive_identical_output"
  }
}
```

For a stream, separate output blocks with a blank line:

```bash
model_output 2>&1 | python3 scripts/detect_loop.py --json
```

The detector emits a signal only. The caller decides whether to stop generation,
request review, retry, or switch models.
