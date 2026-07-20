# 变更记录

本文件记录技术预览版之后影响用户、部署或开发契约的变化。历史提交细节以 Git 记录为准。

## [Unreleased]

### Added

- 按架构、参考、指南、验证四层重建项目文档，并建立领域化仓库重构设计与可回滚实施计划。
- 增加运行契约快照、12 模块归属清单和结构图追溯矩阵；校正 Celery 稳定任务数为 11。
- 恢复非破坏性的运行栈验证脚本，保持统一门禁的静态检查入口完整。
- 面向技术人员的 v0.1 技术预览指南、全量审计报告和贡献指南。
- 文档门禁动态校验根 README OpenAPI 数、数据库参考中的 Alembic head/表数和 DXF→Excel Stage 跟踪边界。
- `make verify-quick`、`make verify-full` 与 full gate 的 DXF→Excel Stage 测试。
- Linux 十阶段 `linux_production` 模板、模板能力 API、文件/结果产物绑定和统一阶段执行端点。
- 生产流程控制台的文件筛选/绑定、DXF→Excel、Excel Final、占位契约和产物下载操作。

### Changed

- 基础设施按 gateway、database、storage、messaging、operations、verification 分类；Windows 目标边界拆为 Node Agent、CAM Runner、SinoCAM Adapter 与协议。
- Compose、Nginx、本地脚本、文档和测试同步新路径；RabbitMQ/Outbox/Beat 保持真实目标留白。
- 删除与 `frontend/public/logo.png` 字节相同的根 `image.png`，运行日志无损迁入网关目录。
- `Stages/dxf2excel` 从不可还原 gitlink 转为父仓库普通跟踪源码；外部验证 corpus、生成工作簿、PDF、cache 和虚拟环境继续排除。
- 当前文档事实更新为 OpenAPI 114 paths / 135 operations、Alembic `e2f4b8c6a130`、36 张模型表和完整 runtime 最多 45 张表。
- 工作流直接复用现有 Job/Celery 与 `/files`：自动阶段按工作流/阶段幂等绑定 attempt，成功结果自动挂接，取消流程同步取消 active Job。
- 自动阶段失败或被单独取消后可从同一 executions 端点重试：复用 Job、递增 attempt、重开原阶段并保持旧 worker fencing；显式取消流程仍不可重开。
- Linux 生产阶段按模板强制校验 artifact type，任意文件类型不能绕过占位/外部交接条件。

### Known limitations

- Compose 仅发布 HTTP，不提供 TLS。
- 图纸拆板、CAM 工作包、Windows Node Agent/SinoCAM 和结果接纳只有接口与产物契约，核心执行尚未实现。
- 自动备份恢复、集中监控告警和生产容量验收尚未交付。
- 仓库 LICENSE、ODA/第三方组件和样本数据分发策略尚待负责人确认。

## [0.1.0-preview] - 2026-07-18

- 建立 FastAPI、React、MySQL、Celery SQL transport、Local/MinIO 的内部技术预览基线。
- 提供身份/RBAC、项目/文件、任务/结果/复核/审计、双向 CAD 转换、DXF→Excel、Excel Final、数据控制台和人工工作流骨架。
- 建立 attempt-safe Job 状态、存储流转/一致性处置、中文文档和分层自动验证。
