# MiMo Stable

MiMo 模型循环退化（Degenerate Loop）的检测与防护工具集。

## 问题描述

在使用 `xiaomimimo/mimo-v2.5-pro` 模型时，当 `reasoning=True` 时，模型有约 30% 的概率进入退化循环状态：

- **症状**：同一段输出重复 8 次，持续约 6 分钟
- **语言切换**：从中文输出切换为英文，且无视 System Prompt 中的中文指令
- **功能停滞**：不执行任何工具调用，仅重复输出文本
- **根因**：`reasoning=True` 激活的英文推理路径导致 token 概率分布塌缩

## 三层防御体系

### 第一层：工程侧限制

在 OpenClaw 配置中设置硬性超时和 Token 限制：

```yaml
providers:
  - id: xiaomimimo/mimo-v2.5-pro
    timeoutSeconds: 180       # 3 分钟硬性超时
    maxTokens: 8000           # 限制单次输出长度
```

实际效果：即使模型进入循环，也会在 3 分钟内强制终止，防止资源浪费。

### 第二层：行为检测

运行 `scripts/detect_loop.py` 实时监控模型输出，检测连续重复。

```bash
# 从日志文件检测
python3 scripts/detect_loop.py --log logs/sample_degenerate_loop.log

# 管道模式（实时监控）
model_output 2>&1 | python3 scripts/detect_loop.py

# JSON 输出（集成到监控系统）
python3 scripts/detect_loop.py --json --log logs/sample_degenerate_loop.log
```

检测规则：
- 连续 3+ 次输出块完全相同（相似度 ≥ 95%）
- 连续 3+ 次工具调用参数完全相同
- 持续时间超过 180 秒

### 第三层：行为规则（AGENTS.md）

在 AGENTS.md 中加入检测规则，让模型自身具备循环感知能力。详见 [SKILL.md](SKILL.md)。

## 证据

### 退化循环日志

`logs/sample_degenerate_loop.log` — 一个真实观察日志：
- Block 1-2: 中文输出，正常
- Block 3-11: 英文输出，完全相同的文本重复 8 次
- 持续时间：约 6 分钟
- `frequency_penalty=1.0` 和 `temperature=0.7` 已启用但无效

### 正常运行日志

`logs/fixed_normal_run.log` — 修复后的正常运行日志：
- 3 次不同的工具调用
- 中文输出保持一致
- 42 秒内完成
- 无任何循环迹象

## 已尝试的无效修复

| 修复方法 | 效果 |
|---------|------|
| `frequency_penalty=1.0` | ❌ 无效 |
| `temperature` 调高至 1.0 | ❌ 无效 |
| System Prompt 强制中文 | ❌ 模型切换为英文 |
| 上下文压缩（减少输入 tokens） | ❌ 无效 |

## 有效修复

| 修复方法 | 效果 | 原理 |
|---------|------|------|
| `timeoutSeconds=180` | ✅ 有效 | 工程侧硬性超时 |
| `maxTokens=8000` | ✅ 有效 | 限制单次输出长度 |
| 行为层检测 | ✅ 有效 | 提前终止循环 |

## 文件结构

```
mimo-stable/
├── README.md                    # 本文档
├── SKILL.md                     # OpenClaw 技能定义
├── CHANGELOG.md                 # 版本记录
├── scripts/
│   ├── detect_loop.py           # 循环检测脚本（Python）
│   ├── test_short.sh            # 短测试（10 文件 + 语法检查）
│   └── test_long.sh             # 长测试（大文件 + 多检查点）
├── logs/
│   ├── sample_degenerate_loop.log   # 退化循环日志样本
│   └── fixed_normal_run.log         # 修复后正常日志样本
└── references/
    └── parameters.md            # 参数参考
```

## 测试

> `sample_degenerate_loop.log` 是历史观察证据。它在默认 180 秒阈值下可能不报警：检测器以连续三个块的时间跨度判断，而该窗口约为 66 秒。复核该证据时使用 `--timeout 60`；生产阈值应按业务容忍度评估，不能把测试阈值直接当作生产配置。


```bash
# 运行短测试
bash scripts/test_short.sh

# 运行长测试
bash scripts/test_long.sh

# 对日志样本运行循环检测
python3 scripts/detect_loop.py --timeout 60 --log logs/sample_degenerate_loop.log
# 预期输出: LOOP_DETECTED（退出码 1）

python3 scripts/detect_loop.py --log logs/fixed_normal_run.log
# 预期输出: NO LOOP
```

## 许可

MIT

## 行为契约

`fixtures/` 中的规范化样例用于稳定回归：循环、正常输出、短时重复分别覆盖报警、无报警和防误报。执行：

```bash
python3 tests/test_detector.py
```

历史日志用于说明观察事实；fixture 用于保证检测器行为，二者不互相替代。
