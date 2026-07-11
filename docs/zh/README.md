# DWG-Agent 平台文档

> 英文索引：[../README.md](../README.md)

这些文档描述本轮文档编辑前于 2026-07-11 从 `main@d178fcf` 审计的实现。文档严格区分代码已实现、默认启用、外部前提、已验证证据和未来工作。

## 阅读顺序

1. [根 README](../../README.md)：状态、启动和已知阻断项。
2. [企业技术规范](../../DWG-Agent企业平台技术规范.md)：规范性边界。
3. [架构](architecture.md)与[处理管线](processing-pipelines.md)：请求和 worker 路径。
4. [配置](configuration.md)、[部署](deployment.md)和[运维](operations.md)：环境工作。
5. 发布前阅读[安全](security.md)、[数据库](database.md)和[工作流验证](workflow-verification.md)。

## 文档地图

| 文档 | 用途 | 事实来源 / 更新触发条件 |
|---|---|---|
| [API 参考](api.md) | 路由清单和通用 API 约定 | 通过 `make docs-generate` 从 FastAPI OpenAPI 生成 |
| [架构](architecture.md) | 组件归属、请求路径、状态与存储边界 | 应用/service、Nginx、Celery 配置 |
| [配置](configuration.md) | Settings、默认值、优先级、密钥与开关 | `config.py`、环境模板、脚本 |
| [数据库](database.md) | 22 张业务表、运行时表、迁移、seed 和数据保护 | SQLAlchemy model、Alembic、`db.sh` |
| [部署](deployment.md) | 本地/Compose 拓扑和构建限制 | Compose、Dockerfile、Nginx、启动脚本 |
| [开发](development.md) | 仓库工作流、测试和变更规则 | package manifest、测试、Makefile |
| [运维](operations.md) | 健康、日志、事故、备份/恢复和发布 runbook | 运行脚本与当前运维缺口 |
| [处理管线](processing-pipelines.md) | 输入、步骤、队列、输出和启用条件 | 管线 service/task 与 Stage 工程 |
| [安全](security.md) | 认证、授权、文件、任务和审计边界 | 安全/dependency 代码与对抗测试 |
| [工作流验证](workflow-verification.md) | 可重复门禁、E2E 场景和带日期证据 | 实际测试运行；相关变更后重跑 |
| [路线图](roadmap.md) | 明确未完成工作及完成标准 | 已知实现/部署缺口 |

## 归属边界

双语契约覆盖每个 `docs/*.md` 及同名 `docs/zh/*.md` 镜像。根中文 README 通过本英文文档入口和英文详细文档集对齐，不逐行复制。

`backend/`、`frontend/`、`infra/`、`agents/`、`cad-worker/` 和已跟踪 `Stages/` 下的组件 README 描述本地归属。`Stages/excel_final/PROCESS.md` 一类算法手册可以保持领域语言文档。`third_parts/` 文档归上游/外部项目所有，不能改写成平台能力。

`Stages/dxf2excel` 当前是损坏 gitlink，不是父仓库内容。其已填充本地 README 不是已跟踪平台文档；修复仓库归属前不能纳入文档门禁。

## 求实规则

- 目录、路由、队列、环境变量或健康进程本身不能证明功能已交付。
- 必须分别说明代码是否存在、flag 默认值、必要外部依赖和验证层级/日期。
- 当前 Compose 只有 HTTP；禁止把未生效 `443:8443` 映射描述为 TLS。
- 生产关闭运行时 OpenAPI/Swagger/ReDoc；生成 API 文件才是稳定参考。
- Redis/Valkey 不是当前组件。历史迁移引用必须标注为历史。
- 备份、监控、保留策略、Agent、CAD worker 和 clean-clone `dxf2excel` 支持仍未完成。

## 更新流程

```bash
# 路由变更
make docs-generate

# 每次文档变更
make docs-check

# 相关实现门禁
cd backend && uv run pytest -q && uv run alembic check
cd ../frontend && npm run build
```

同一变更必须同时更新英文/中文文件。两种语言中的 path、endpoint、环境变量名、status 名、迁移 revision、命令、表格形状和标题结构必须一致。带日期的验证声明必须描述环境；后续变更后它只能作为历史证据。
