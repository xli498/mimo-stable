# MiMo 参数调优指南

> 修复后的参数配置，以及为什么这么设。

## 核心参数

### maxTokens: 8000

**为什么是 8000 而不是 12000 或 4000？**

- 8000 足够覆盖单轮工具调用+总结回复的长度
- 8000 不够形成 degenerate loop 的自我复制放大（需要 15000+ 的空间）
- 4000 太短，复杂任务会频繁截断
- 12000 还是给了循环一定的放大空间

**什么时候会不够用？**
- 一次性生成超长文档（2 万字以上）→ 分多轮完成
- 这种场景在 Agent 工具调用中几乎不会出现

### timeoutSeconds: 180

**为什么是 180 而不是 300？**

- DeepSeek 设了 300，但 DeepSeek 不会死循环
- MiMo 的死循环通常在 60-120 秒内已经形成
- 180 秒是"足够完成正常任务 + 不会让死循环跑太久"的平衡点
- 设太短（如 60 秒）会误杀正常长任务

### reasoning: True 的模型全部砍，False 的不动

**逻辑：**
- `reasoning=True` → 走思维链路径 → 英文推理路径不稳定 → 容易塌缩
- `reasoning=False` → 不走思维链 → 不会产生自我复制循环
- mimo-v2-omni 是 `reasoning=False`，保持 maxTokens=32000

## 不需要动的参数

| 参数 | 值 | 为什么不动 |
|------|---|-----------|
| temperature | 模型默认 | MiMo 官方推荐 0.6，但调这个对 degenerate loop 无效 |
| frequency_penalty | 模型默认 | 最高值 1.0 也无法打断已形成的循环 |
| presence_penalty | 模型默认 | 同上 |
| contextWindow | 模型默认 | 压缩上下文对 bug 无效果 |

## 补充：如果你没有 OpenClaw

如果你在其他框架（如 vLLM、SGLang、直接调 API）上使用 MiMo，同样适用：

```python
# vLLM 示例
from vllm import LLM, SamplingParams

llm = LLM(model="XiaomiMiMo/MiMo-7B-RL", trust_remote_code=True)
params = SamplingParams(
    temperature=0.6,
    max_tokens=8000,  # 关键：不要设太高
)
```

```bash
# SGLang 示例
python3 -m sglang.launch_server \
  --model-path XiaomiMiMo/MiMo-7B-RL \
  --trust-remote-code
# max_tokens 在请求时设置，不要在服务端设太高
```

## 效果预期

- **修复前：** 约 30% 的 Agent 任务触发 degenerate loop
- **修复后：** 两轮压测（20+ 轮工具调用、119KB 上下文）零循环
