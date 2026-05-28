---
name: mimo-stable
description: MiMo model degenerate loop diagnosis and fix — three-layer defense against repetition deadlocks in MiMo-series LLMs (xiaomimimo). Use when MiMo models exhibit identical repeated output, tool-call storms, or dead-loop behavior.
---

# MiMo Stable — Degenerate Loop Fix

Production-hardened fix for MiMo-series models (xiaomimimo/mimo-v2.5-pro, v2.5, v2-pro, v2-omni) that suffer from degenerate repetition loops.

## The Problem

MiMo models occasionally enter a **degenerate loop**: outputting the exact same text block 8+ times over 6+ minutes, or calling the same tool with identical parameters endlessly. This is a model-layer pathology, not a code bug.

**Root cause:** MiMo's English reasoning chain is unstable. When the model switches to English during internal reasoning (thinking), the output distribution collapses into a fixed-point attractor — a degenerate loop.

## The Fix: Three-Layer Defense

### Layer 1 — Sampling Parameters (API-level)

```json
{
  "temperature": 0.6,
  "frequency_penalty": 0.8,
  "presence_penalty": 0.4
}
```

- `temperature: 0.6` — official MiMo recommendation from GitHub README. Balances creativity without drifting into repetition.
- `frequency_penalty: 0.8` — penalizes tokens based on how often they've appeared so far. **This is the MVP parameter.** Without it, the model cheerfully outputs the same line forever.
- `presence_penalty: 0.4` — penalizes any token that has appeared at all, encouraging topic diversity. Lower than frequency_penalty to avoid derailing coherence.

### Layer 2 — Language Enforcement (Prompt-level)

```
思考和输出一律用中文。推理过程（thinking）禁止切英文。
输出必须中文。这是模型 degenerate loop 的根因防御——
MiMo 英文推理路径不稳定，切英文后极易死循环。
```

Force Chinese-only output in system prompt. MiMo's English reasoning path is the primary loop trigger. Keeping everything in Chinese sidesteps the unstable code path entirely.

### Layer 3 — Runtime Detection (Agent-level)

Detection rules to embed in agent behavior config:

```
检测条件：
- 连续 3+ 次输出完全相同或高度相似的文本
- 同一个 tool 连续调用 3+ 次且参数完全相同

恢复策略：
1. 立即停止当前任务
2. 发送新消息打断循环
3. 重新评估任务目标，换一种方法继续
```

## When to Use

Apply these parameters when:
- Any MiMo-series model is the active model
- The agent reports repeated/identical output blocks
- Tool-call storms (same call, same params, no progress)
- User reports "it's stuck in a loop"

## Application in OpenClaw

In `openclaw.json`, under the `xiaomimimo` provider's model entries:

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

Apply to ALL four MiMo model variants (v2.5-pro, v2.5, v2-pro, v2-omni).

## What We Tried & What Didn't Work

| Attempt | Result |
|---------|--------|
| Raising temperature (>0.6) | Worse — more randomness, still loops |
| Lowering temperature (<0.6) | Deterministic collapse, guaranteed loop |
| maxTokens cap | Delays but doesn't prevent |
| contextWindow compression | No effect on loop behavior |
| Only AGENTS.md detection rules | Detects but doesn't prevent |

## Lessons

1. **"Reduce probability" is not enough.** Must make it structurally impossible.
2. **Multi-layer is mandatory.** No single parameter fixes MiMo loops.
3. **Language is structure.** Switching thinking language changes the entire generation distribution. Chinese-only is a structural fix, not a cosmetic one.
4. **Official docs are sparse.** MiMo's README only documents `temperature: 0.6`. Everything else was discovered through systematic experimentation.
