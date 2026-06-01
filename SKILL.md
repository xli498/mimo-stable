# SKILL.md — MiMo Stable

## 技能定义

```yaml
name: mimo-stable
description: >
  MiMo 退化循环检测与防护。当使用 xiaomimimo/mimo-v2.5-pro 模型时，
  检测并防护约 30% 概率的 degenerate loop（重复输出 8 次，持续 6 分钟）。
version: 1.0.0
author: xli498
```

## OpenClaw 配置规则

在模型配置中添加以下设置：

```yaml
# openclaw.yaml
providers:
  - id: xiaomimimo/mimo-v2.5-pro
    api: https://api.xiaomimimo.com/v1
    timeoutSeconds: 180        # 3 分钟硬性超时
    maxTokens: 8000            # 限制单次输出长度
    # 注意：不要设置 frequency_penalty 或过高的 temperature
    # 经验表明这些参数对 MiMo 退化循环无效
```

## AGENTS.md 行为规则

将以下规则添加到项目的 `AGENTS.md` 中：

```markdown
## MiMo Degenerate Loop 检测

当使用 xiaomimimo/mimo-v2.5-pro 模型时，模型可能进入退化循环。

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
# 在启动模型服务时传入管道
openclaw agent run --model xiaomimimo/mimo-v2.5-pro 2>&1 | \
  python3 scripts/detect_loop.py --threshold 3 --timeout 180
```

### 方式二：日志后处理

```bash
# 定期检查日志
python3 scripts/detect_loop.py --log /var/log/openclaw/session.log --json | \
  python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(1 if d['loop_detected'] else 0)"
```

### 方式三：cron 监控

```bash
# 每 5 分钟检查一次
*/5 * * * * cd /path/to/mimo-stable && python3 scripts/detect_loop.py --log /var/log/openclaw/current.log --json >> /var/log/loop_check.json
```

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
| 输出语言 | 中文（按 System Prompt） | 英文（无视 System Prompt） |
| 输出内容 | 每次不同，有进展 | 完全相同，无进展 |
| 工具调用 | 正常执行 | 不调用任何工具 |
| 时间特征 | 响应时间合理 | 每次 ~33 秒，重复 8+ 次 |
| Token 分布 | 正常分布 | 概率塌缩到单一路径 |

## 已知限制

1. **检测延迟**：基于块的检测需要等待至少 3 个块才能触发（约 1.5 分钟）
2. **语言切换检测**：当前版本不自动检测语言切换（未来计划添加）
3. **阈值调优**：`threshold=3` 和 `similarity=0.95` 基于 MiMo 经验，其他模型可能需要不同值
4. **只读模式**：当前仅检测和报告，不自动终止模型进程（依赖 timeoutSeconds 进行硬性终止）
