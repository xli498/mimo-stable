# MiMo Degenerate Loop — Parameter Reference

## Confirmed Working Parameters

Tested on OpenClaw gateway, May 2026, with xiaomimimo/mimo-v2.5-pro.

### Full Config

```json
{
  "temperature": 0.6,
  "frequency_penalty": 0.8,
  "presence_penalty": 0.4
}
```

### Model Coverage

All four MiMo model variants should receive these parameters:

| Model ID | Alias | Status |
|----------|-------|--------|
| `xiaomimimo/mimo-v2.5-pro` | MiMo-V2.5-Pro | ✅ Confirmed working |
| `xiaomimimo/mimo-v2.5` | MiMo-V2.5 | ✅ Same architecture |
| `xiaomimimo/mimo-v2-pro` | MiMo-V2-Pro | ✅ Same architecture |
| `xiaomimimo/mimo-v2-omni` | MiMo-V2-Omni | ⚠️ Multi-modal variant, test separately |

### Parameter Tuning Guide

If loops persist after applying base params:

1. **Increase frequency_penalty** → 0.9, then 1.0 (max)
   - Trade-off: output may become less natural
2. **Increase presence_penalty** → 0.5, then 0.6
   - Trade-off: may derail topic coherence
3. **Check context length** — MiMo at >100K tokens is less stable
4. **Switch to Chinese-only prompt** — if currently using English prompt

### What NOT to Do

- ❌ `temperature: 0` — deterministic mode, guaranteed loop
- ❌ `frequency_penalty: 0` — no repetition penalty at all
- ❌ Mix English + Chinese in system prompt
- ❌ Set `top_p` without setting frequency_penalty first
- ❌ Trust that "it probably won't happen again" without params

### Diagnostic Commands

```bash
# Check current MiMo params in OpenClaw
grep -A5 'mimo-v2.5-pro' ~/.openclaw/openclaw.json

# Monitor for loop symptoms in session logs
grep 'same output' ~/.openclaw/agents/main/sessions/*.jsonl | tail -20
```
