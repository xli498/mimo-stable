# LLM Degenerate Loop Guardrails

面向国产及其他 LLM 的输出流和工具调用退化循环经验记录与检测工具。
项目最初来自 MiMo `reasoning=True` 场景的重复输出观察，后来发现类似现象
也可能出现在 GLM 等其他模型中。这里分享的是识别、止损和复盘方法，不是
针对任何模型的根治方案。

> [!IMPORTANT]
> 当前项目的核心能力是**检测与止损**，不是自动修复模型。检测到循环后，
> 由上层运行器决定停止、切换模型、重试或人工复核。

## 目录

- [30 秒开始](#30-秒开始)
- [问题描述](#问题描述)
- [三层防御体系](#三层防御体系)
- [检测策略](#检测策略)
- [可复现证据](#可复现证据)
- [如何记录和分享新案例](#如何记录和分享新案例)
- [工程侧缓解措施](#工程侧缓解措施不是模型修复)
- [文件结构](#文件结构)
- [测试](#测试)
- [安装与集成](#安装与集成)
- [许可](#许可)
- [行为契约](#行为契约)

## 30 秒开始

```bash
python3 scripts/detect_loop.py --log fixtures/loop_detected.log
python3 tests/test_detector.py
python3 scripts/benchmark_fixtures.py
```

机器集成使用单份 JSON 摘要和退出码：

```bash
python3 scripts/detect_loop.py --json --timeout 60 --log fixtures/loop_detected.log
# 退出码 0 = 未检测到；1 = 检测到；2 = 参数/输入错误

# 将检测摘要转成保守的恢复决策（只输出决策，不执行重试）
python3 scripts/detect_loop.py --json --timeout 60 --log fixtures/loop_detected.log | \
  python3 scripts/recovery_policy.py --retryable
```

## 问题描述

在特定模型、参数、任务和上游服务条件下，LLM 可能进入退化循环状态：

- **症状**：同一段输出重复，持续时间异常增长
- **语言切换**：特定中文任务中切换为英文，且无视语言约束
- **功能停滞**：不执行有进展的工具调用，仅重复输出文本
- **根因尚未确定**：`reasoning=True`、上下文长度、工具链状态、服务端实现等
  都可能是相关变量；本项目不把相关性写成因果结论。

类似表现并不等于同一个 bug。MiMo、GLM 或其他国产模型的案例必须分别记录
模型版本、端点、参数、任务和时间，不能把一个模型的触发概率或规避经验直接
外推到另一个模型。

## 三层防御体系

### 第一层：工程侧限制（止损，不是修复）

在运行器中设置硬性超时和 Token 限制（具体字段以所用框架官方文档为准）：

```yaml
# 示例结构；字段名和数值必须按实际框架、模型与任务校准。
provider:
  model: <your-model>
  timeout: <task-specific-limit>
  max_tokens: <task-specific-limit>
```

> [!WARNING]
> **历史案例，不是通用推荐：** 最初 MiMo 案例曾使用
> `timeoutSeconds=180` 和 `maxTokens=8000` 限制单次资源占用。这些值不是
> 跨模型配置建议，也不表示能够改变模型进入循环的概率。

作用是限制单次故障的最长资源占用，不改变模型本身的循环概率。

### 第二层：行为检测

运行 `scripts/detect_loop.py` 实时监控模型输出，检测连续重复。

管道输入以空行切分输出块；`--timeout` 的持续时间只在流式输入期间有实际意义，
离线日志使用日志中的块时间戳。

```bash
python3 scripts/detect_loop.py --log logs/sample_degenerate_loop.log
model_output 2>&1 | python3 scripts/detect_loop.py
python3 scripts/detect_loop.py --json --log logs/sample_degenerate_loop.log
```

检测规则：

- 连续 3+ 次输出块完全相同或高度相似
- 连续 3+ 次工具调用参数完全相同
- 已知副作用工具的重复调用
- 显式声明中文任务后的英文漂移
- 可选的持续时间门控

### 第三层：行为规则（AGENTS.md）

在 AGENTS.md 或对应运行器规则中加入检测和处置约束。详见
[SKILL.md](SKILL.md)。该层是提示和流程规则，不替代运行时检测。

## 检测策略

默认文本重复采用**持续时间门控**：需要达到 `--threshold` 次相似输出，且
重复窗口达到 `--timeout` 秒。这适合日志后处理，减少短暂重复的误报。

如果上层需要在达到重复次数后立即得到信号，可显式使用：

```bash
python3 scripts/detect_loop.py --text-mode instant --log fixtures/repeated_but_short.log
```

工具调用重复和已知副作用工具的重复调用不受文本持续时间门控影响；生产集成仍应
结合幂等键、调用结果和重试原因做二次判断。

## 可复现证据

> [!NOTE]
> Fixture 用于稳定回归，历史日志用于记录具体观察；两者的证据性质不同。

`logs/sample_degenerate_loop.log` 是历史观察日志；`logs/fixed_normal_run.log`
是一次正常运行日志。它们只说明具体案例，不构成模型故障率统计，也不证明
任何参数能“修复”模型。

当前仓库没有足够实验次数估计任何模型的通用触发概率，因此不提供“某模型
有 X% 概率出问题”之类的结论。

## 如何记录和分享新案例

建议至少记录以下信息，并在发布前脱敏：

- 模型与精确版本、供应商/端点、调用时间段
- `reasoning`、temperature、max tokens、上下文规模等实际参数
- 任务类型、是否多轮、是否涉及工具调用
- 重复发生前后的输出块摘要或哈希，不公开密钥、隐私和完整敏感工具参数
- 是否真正发生资源浪费/副作用，检测器是否报警，检测延迟
- 重试、切换模型或改变参数后的结果

案例记录用于复盘和横向比较，不应写成因果证明。没有原始证据时，使用
“观察到”“可能相关”“尚未复现”，不要使用“必然”“已证明”“彻底解决”。

## 工程侧缓解措施（不是模型修复）

| 措施 | 作用 | 边界 |
| :--- | :--- | :--- |
| `timeoutSeconds=180` | 限制单次资源损失 | 不改变模型行为 |
| `maxTokens=8000` | 限制输出上限 | 不等于不再循环 |
| 行为层检测 | 提供停止/切换信号 | 需要上层执行恢复动作 |

## 文件结构

```
mimo-stable/
├── README.md
├── SKILL.md
├── pyproject.toml
├── CHANGELOG.md
├── scripts/
│   ├── detect_loop.py        # 循环检测脚本
│   ├── benchmark_fixtures.py # 可复现 fixture 基准测试
│   └── recovery_policy.py    # 保守恢复决策层（不执行副作用）
├── tests/test_detector.py    # 行为契约测试
├── fixtures/                 # 规范化回归样例
├── logs/                     # 历史观察日志
└── references/parameters.md
```

## 测试

```bash
python3 -m py_compile scripts/*.py
bash -n scripts/*.sh
python3 tests/test_detector.py
python3 scripts/benchmark_fixtures.py
# 或使用仓库提供的入口：
bash scripts/test_short.sh
bash scripts/test_long.sh
```

当前 benchmark 覆盖 9 个规范化案例：循环、正常输出、短时重复、近似文本、
变化参数重试、非连续工具调用、工具 key 顺序、重复副作用工具和中文任务语言漂移。

历史日志在默认 180 秒阈值下可能不报警；复核时使用 `--timeout 60`。生产阈值
应按业务容忍度评估，不能把测试阈值直接当作生产配置。

## 安装与集成

项目保持零运行时依赖，直接运行脚本最稳妥。当前支持**从源码本地安装**，但尚未
发布到 PyPI；请不要使用 `pip install mimo-stable` 获取公共发行包。

本地验证安装入口：

```bash
python3 -m pip install --no-deps .
mimo-loop-detect --json --timeout 60 --log fixtures/loop_detected.log
```

上层运行器不应在检测到循环后盲目重试：

1. 保存脱敏后的事件摘要；
2. 停止当前生成或工具链；
3. 根据任务是否幂等决定重试；
4. 必要时切换模型或请求人工复核。

## 许可

MIT

## 行为契约

`fixtures/` 中的规范化样例用于稳定回归，历史日志用于说明观察事实；二者不互相
替代。执行 `python3 tests/test_detector.py` 可验证检测器行为。
