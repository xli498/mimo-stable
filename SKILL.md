---
name: mimo-stable
description: 修复 MiMo (xiaomimimo) 系列模型在 Agent 工具调用场景下的 Degenerate Loop 问题。通过 timeout + maxTokens 限制 + 行为层检测三重防护，根治死循环。已验证。
---

# MiMo Stable — Degenerate Loop 修复

## 问题

MiMo reasoning 模型在 Agent 工具调用后陷入 Degenerate Loop：同一段话重复 8 次，持续 6 分钟，无视 System Prompt、frequency_penalty、用户打断。

## 根因

三重因素叠加：
1. `reasoning=True` 的英文推理路径 token 概率分布塌缩
2. `maxTokens=32000` 提供了足够的放大空间
3. `timeoutSeconds` 未设，死循环无人切断

## 修复

### 配置层（必须）

```json
{
  "timeoutSeconds": 180,
  "models": [
    { "id": "mimo-v2.5-pro", "reasoning": true, "maxTokens": 8000 },
    { "id": "mimo-v2.5", "reasoning": true, "maxTokens": 8000 },
    { "id": "mimo-v2-pro", "reasoning": true, "maxTokens": 8000 },
    { "id": "mimo-v2-omni", "reasoning": false, "maxTokens": 32000 }
  ]
}
```

### 行为层（推荐）

在 AGENTS.md 中加入检测规则：
- 连续 3+ 次相同输出 → 中断
- 同一 tool 连续 3+ 次相同参数 → 中断
- 检测到后立即停止任务，换策略重试

## 验证

两轮压测全部通过：
- 短任务：10 个算法文件，20+ 轮工具调用，1 分 40 秒，零循环
- 长任务：119KB 上下文，6 个子任务，41 秒，零循环

## 详细文档

- [README.md](./README.md) — 完整报告
- [references/parameters.md](./references/parameters.md) — 参数调优指南
