# Changelog

All notable changes to LLM Degenerate Loop Guardrails.

## [1.1.4] - 2026-08-24

### Fixed
- Corrected the `pyproject.toml` project metadata layout so isolated builds and PyPI publishing validate successfully.

## [1.1.3] - 2026-08-24

### Fixed
- Removed stray diff prefixes from the Unreleased changelog entries.
- Added PyPI project links, keywords, and Python package classifiers.
- Switched PyPI package metadata to the English README for a clearer public project page.

## [1.1.2] - 2026-08-24

### Fixed
- Corrected the Chinese and English installation guidance to document the published PyPI CLI package.
- Clarified that the distribution provides the `mimo-loop-detect` CLI and does not currently expose a `mimo_stable` Python import API.

## [1.1.1] - 2026-08-24

### Added
- Added an English project overview with architecture, installation, and integration guidance.
- Added explicit PyPI Trusted Publishing workflow for reproducible package releases.
- Added public integration examples and release-quality documentation.

### Fixed
- Aligned the published package workflow with the public `main` branch and Python 3.10+ metadata.

## [1.1.0] - 2026-08-24

### Changed
- 将项目定位从 MiMo 专用“稳定/修复”收紧为跨模型退化循环经验分享、检测与工程侧止损。
- 明确 MiMo、GLM 和其他模型的案例必须分别记录，不能外推触发概率或根因。
- 删除无充分证据的通用故障率、确定性根因和“修复后”表述。
- 补充案例记录字段、证据边界和脱敏要求。

### Fixed
- 修正非连续工具调用可能被误判为连续循环的问题。
- 增加非法阈值、超时、相似度和恢复 JSON 类型校验。
- 修复 stdin 管道先收集后处理导致的持续时间失真，并将旧测试脚本改为真实项目检查入口。

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
- `logs/fixed_normal_run.log` — 一次正常运行日志样本（3 次工具调用，42 秒）
- `SKILL.md` — 技能定义，包含检测规则和 AGENTS.md 模板
- `CHANGELOG.md` — 本文件
- `references/parameters.md` — 历史参数观察记录

### Changed
- `README.md` — 重写为技术文档风格
  - 添加证据引用（日志、脚本）
  - 添加三层防御体系说明
  - 添加历史参数尝试与工程侧止损措施对比表
  - 移除煽动性语言

### Fixed
- 文档说明：记录历史参数尝试未打断当时案例，不外推为跨模型结论

## [Unreleased]

### Changed
- 明确源码本地安装边界，并将 MiMo 配置示例标注为历史案例而非通用推荐。
- 补充贡献、安全报告、Issue 和 PR 的隐私与证据边界要求。
- SKILL.md frontmatter 对齐 OpenClaw 官方规范：移除非标准字段（triggers/dependencies/author/created），触发语并入 description。
- CI 质量门禁的 Python 矩阵扩展到 3.13 和 3.14（此前仅 3.10–3.12，与宣称的 3.10+ 支持不一致）；pyproject.toml classifiers 同步补充 3.13/3.14。

### Added
- 安装后 CLI smoke test、包版本与 CHANGELOG 一致性检查，以及对应 CI 校验。
- 清理重复的 Bug 模板，并收紧 Skill 中的历史配置与调度示例边界。

### Planned
- 收集脱敏的跨模型案例，并按模型/端点/参数分组比较
- 增加可选的真实运行日志适配器，但不保存密钥和敏感工具参数
- 在有足够样本后再报告限定条件下的误报、漏报和检测延迟
