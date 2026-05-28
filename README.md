# MiMo Stable

> Your MiMo model is repeating itself like a broken record? Cool. Here's the kill switch.

## What's Broken

MiMo (xiaomimimo series) has a pathology where it locks into a degenerate loop — same output, same tool call, same everything. 8 times. 6 minutes. Zero progress.

**Why:** English reasoning path is unstable. The model's internal distribution collapses into a fixed-point attractor during English thinking. Once it's there, it stays there.

## The Kill Switch

Three layers. None optional.

### Layer 1 — The Numbers

```json
{
  "temperature": 0.6,
  "frequency_penalty": 0.8,
  "presence_penalty": 0.4
}
```

`frequency_penalty: 0.8` does the heavy lifting. Without it the model will literally print the same line until you run out of tokens. `temperature: 0.6` is the only number MiMo's own docs recommend. Everything else was found by breaking stuff.

### Layer 2 — Language Lock

Force Chinese-only. Yes, really. MiMo's English reasoning path IS the bug. Chinese sidesteps the unstable code path. This isn't a cosmetic choice — it's a structural fix.

### Layer 3 — Kill Switch

```
3+ identical outputs → abort task → send interrupt → new strategy
```

Embed this in your agent. If your agent can't detect its own loop, it deserves to be replaced.

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

Apply to all four MiMo variants. They share the same architecture. They share the same bug.

## The Graveyard of Bad Ideas

| Attempt | Why It Failed |
|---------|---------------|
| Crank temperature up | More randomness. More loops. Great. |
| Crank temperature down | Deterministic collapse. Literally guaranteed loop. |
| Cap maxTokens | Delays death by 2 minutes. Doesn't prevent it. |
| Detection-only, no params | Congratulations, you saw the loop happen. Still happened. |
| Context window compression | Zero effect. The bug is in the generation, not the context. |

## Files

- `SKILL.md` — full diagnosis: what, why, how
- `references/parameters.md` — tuning guide, what to try when base params aren't enough

## License

MIT. Steal it. Fix your models.
