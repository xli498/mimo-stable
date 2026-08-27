---
name: mimo-stable
description: 跨模型 LLM 退化循环经验记录、检测与工程侧止损；当需要检测 LLM 循环、分析重复输出或工具调用重复时使用。不承诺根治模型问题。
version: 1.1.4
---

# LLM Degenerate Loop Guardrails

## 运行器配置参考

以下只是某个历史案例的参考，不是 MiMo、GLM 或其他模型的通用修复配置。
字段名称和行为必须以实际运行器/供应商官方文档为准。

在运行器中配置任务级的资源上限。字段名和行为必须以实际运行器/供应商
官方文档为准：

```yaml
# 示例结构，不是可直接粘贴到某个框架的配置
runtime:
  timeout: <task-specific-limit>
  max_output_tokens: <task-specific-limit>
```

历史 MiMo 案例曾使用过 `timeoutSeconds=180`、`maxTokens=8000` 和
`reasoning=True`。这些字段和值只用于记录当时的运行条件，不能直接外推到
其他模型、端点或框架，也不表示能够修复模型循环。

## AGENTS.md 行为规则

将以下规则添加到项目的 `AGENTS.md` 中：

```markdown
## LLM Degenerate Loop 检测

当模型在特定任务和运行条件下出现退化循环迹象时，按证据记录并触发工程侧止损。

### 检测条件
- 连续 3+ 次输出完全相同或高度相似的文本（相似度 ≥ 95%）
- 连续 3+ 次工具调用参数完全相同
- 模型从中文输出切换为英文输出，且无视 System Prompt

### 恢复策略
1. 检测到循环后，立即停止当前任务
2. 发送新消息或切换模型打破循环
3. 重新评估任务目标，换一种方法继续

### 预防
- 在 tool call 模式下保持警惕
- 如果发现输出开始重复，主动改变策略
- 使用 detect_loop.py 脚本进行实时监控
```

## 检测脚本集成

### 方式一：管道监控

```bash
# 将上层运行器输出的脱敏块流交给检测器；空行分隔输出块
your_runner_command 2>&1 | \
  python3 scripts/detect_loop.py --threshold 3 --timeout 180
```

### 方式二：日志后处理

```bash
# 对一个脱敏日志快照做后处理；不要把多个 JSON 摘要直接 append 到同一文件
python3 scripts/detect_loop.py --log /path/to/redacted-session.log --json > /tmp/loop-summary.json
python3 -c "import json,sys; d=json.load(open('/tmp/loop-summary.json')); sys.exit(1 if d['loop_detected'] else 0)"
```

如需周期监控，应由上层调度器管理快照、幂等记录和告警；本项目不提供 cron
配置，也不会自动停止模型、切换模型、重试请求或调用工具。

## 检测规则详解

### 规则 1：连续相同输出

```
输入: [block_1, block_2, block_3, block_4] 其中 block_2 == block_3 == block_4
阈值: 3 次
相似度: >= 0.95 (使用 SequenceMatcher)
持续时间: >= 180 秒 (默认)
输出: LOOP_DETECTED
```

### 规则 2：相同工具调用

```
输入: [exec("top"), exec("top"), exec("top")]
阈值: 3 次
参数匹配: 完全相同 (JSON keys 排序后比较)
输出: LOOP_DETECTED
```

## 模型行为特征

| 特征 | 正常模式 | 退化循环模式 |
|------|---------|------------|
| 输出语言 | 符合任务约束 | 可能出现无解释的语言漂移 |
| 输出内容 | 每次不同，有进展 | 可能完全相同、无进展 |
| 工具调用 | 正常执行或有合理重试 | 可能重复相同调用 |
| 时间特征 | 与任务相称 | 可能异常增长或持续占用 |
| Token 分布 | 不能仅凭日志判断 | 不能仅凭日志证明概率塌缩 |

## 已知限制

1. **检测延迟**：默认文本模式受 `--timeout` 门控；需要即时信号时使用 `--text-mode instant`
2. **语言切换检测**：必须通过 `--expect-language zh` 显式开启，避免把正常英文/代码误报为故障
3. **阈值调优**：`threshold=3` 和 `similarity=0.95` 来自历史案例，其他模型和任务需要单独校准
4. **只读模式**：当前仅检测和报告，不自动终止模型进程；停止、切换模型和重试由上层运行器负责
5. **副作用重试**：重复副作用调用可能是合理的幂等重试，生产系统应结合幂等键、调用结果和重试原因二次判断
6. **统计边界**：仓库 fixture 和历史日志用于回归/说明，不足以证明通用故障率
