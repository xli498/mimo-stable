# MiMo Stable — Degenerate Loop Fix

> Three-layer defense against repetition deadlocks in MiMo-series LLMs. If your MiMo model is stuck in a loop, these are the exact parameters that fix it.

## The Problem

MiMo models (xiaomimimo series) occasionally enter a degenerative loop — spitting out identical text blocks 8+ times for 6+ minutes, or storming the same tool call with zero progress.

**Root cause:** MiMo's English reasoning chain is unstable. Switching to English during thinking collapses the output distribution into a fixed-point attractor.

## The Fix (3 Layers)

### 1. Sampling Parameters (the numbers)

```json
{
  "temperature": 0.6,
  "frequency_penalty": 0.8,
  "presence_penalty": 0.4
}
```

`frequency_penalty: 0.8` is the MVP. Without it, the model happily outputs the same line forever. `temperature: 0.6` is the only officially documented parameter (from MiMo GitHub README).

### 2. Language Enforcement

Force Chinese-only output. MiMo's English path triggers the loop. Chinese sidesteps it entirely.

### 3. Runtime Detection

Embed loop detection in your agent config: 3+ identical outputs = kill current task, send interrupt, change strategy.

## OpenClaw Config

```json
{
  "models": {
    "xiaomimimo/mimo-v2.5-pro": {
      "params": {
        "temperature": 0.6,
        "frequency_penalty": 0.8,
        "presence_penalty": 0.4
      }
    }
  }
}
```

## What Didn't Work

| Tried | Result |
|-------|--------|
| Higher temperature | More randomness, still loops |
| Lower temperature | Deterministic collapse |
| maxTokens cap | Delays, doesn't prevent |
| Detection-only (no params) | Catches but doesn't stop |

## Files

- `SKILL.md` — full diagnosis and fix guide
- `references/parameters.md` — tuning guide and diagnostic commands

## License

MIT
