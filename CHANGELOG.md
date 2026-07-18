# 变更记录

本文件记录技术预览版之后影响用户、部署或开发契约的变化。历史提交细节以 Git 记录为准。

## [Unreleased]

### Added

- 面向技术人员的 v0.1 技术预览指南、全量审计报告和贡献指南。
- 文档门禁动态校验根 README OpenAPI 数、`CLAUDE.md` Alembic head/表数和 DXF→Excel Stage 跟踪边界。
- `make verify-quick`、`make verify-full` 与 full gate 的 DXF→Excel Stage 测试。

### Changed

- `Stages/dxf2excel` 从不可还原 gitlink 转为父仓库普通跟踪源码；外部验证 corpus、生成工作簿、PDF、cache 和虚拟环境继续排除。
- 当前文档事实更新为 OpenAPI 96 paths / 115 operations、Alembic `d5e8a1c4b720`、28 张模型表和完整 runtime 最多 37 张表。

### Known limitations

- Compose 仅发布 HTTP，不提供 TLS。
- Agent、CAD 图纸业务算法和 Windows CAD Worker 不属于交付范围。
- 自动备份恢复、集中监控告警和生产容量验收尚未交付。
- 仓库 LICENSE、ODA/第三方组件和样本数据分发策略尚待负责人确认。

## [0.1.0-preview] - 2026-07-18

- 建立 FastAPI、React、MySQL、Celery SQL transport、Local/MinIO 的内部技术预览基线。
- 提供身份/RBAC、项目/文件、任务/结果/复核/审计、双向 CAD 转换、DXF→Excel、Excel Final、数据控制台和人工工作流骨架。
- 建立 attempt-safe Job 状态、存储流转/一致性处置、中文文档和分层自动验证。
