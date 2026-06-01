# Changelog

All notable changes to MiMo Stable.

## [1.0.0] - 2026-05-20

### Added
- `scripts/detect_loop.py` — Python 循环检测脚本
  - 规则 1：连续 3+ 次相同输出块检测
  - 规则 2：连续 3+ 次相同工具调用检测
  - 支持 JSON 输出格式
  - 支持管道模式和日志文件模式
  - 可配置的相似度、重复次数和时间阈值
- `scripts/test_short.sh` — 短测试脚本（10 文件 + 语法 + 执行验证）
- `scripts/test_long.sh` — 长测试脚本（50KB 文件 + 多检查点 + 模式搜索）
- `logs/sample_degenerate_loop.log` — 退化循环日志样本（8 次重复，6 分钟）
- `logs/fixed_normal_run.log` — 修复后正常日志样本（3 次工具调用，42 秒）
- `SKILL.md` — 技能定义，包含检测规则和 AGENTS.md 模板
- `CHANGELOG.md` — 本文件
- `references/parameters.md` — MiMo 模型参数参考

### Changed
- `README.md` — 重写为技术文档风格
  - 添加证据引用（日志、脚本）
  - 添加三层防御体系说明
  - 添加已尝试修复 vs 有效修复对比表
  - 移除煽动性语言

### Fixed
- 文档说明：明确 `frequency_penalty` 和 `temperature` 对 MiMo 退化无效

## [Unreleased]

### Planned
- 语言切换自动检测（中文 → 英文）
- 内存中的滑动窗口检测（非文件模式）
- 自动终止循环进程功能
- 多模型支持（当前仅针对 MiMo）
