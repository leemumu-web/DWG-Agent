# DWG-Agent 平台文档

本目录是仓库唯一维护的项目详细文档，全部使用中文。2026-07-18 的审查对象是当前工作树；文档不会把 HEAD 与工作树混为一谈，也不会把目录、路由、配置项或健康进程直接视为已交付能力。

## 项目定位

DWG-Agent 是一个内部 CAD/Excel 文件处理平台。浏览器通过 Nginx 使用 React 管理端和 FastAPI API；MySQL 保存身份、权限、项目、文件元数据、任务、结果、复核、审计、工作流以及 Celery broker/result 状态；Local FS 或 MinIO 保存文件字节；Celery worker 调用各 Stage 完成长任务。

当前主要可用面包括身份/RBAC、项目、文件、图纸元数据、Job/JobStep、结果与复核、审计、五条可执行队列路径、Excel Final 关系化导入、人工生产流程，以及统一监视 MySQL 登记、Local/MinIO 对象、入出库流水和一致性处置的数据控制台。四条转换管线默认关闭并受外部依赖约束；Agent 执行、CAD 图纸业务算法和 Windows CAD Worker 是明确非目标。Redis/Valkey 已从活动架构移除。

## 阅读路径

1. [根 README](../README.md)：快速状态、启动方式、目录地图和已知阻断项。
2. [技术预览指南](developer-preview.md)：技术人员首次安装、开发与验收的最短路径。
3. [2026-07-18 全量审计报告](audit-report-2026-07-18.md)：本轮检查范围、证据、修复和剩余风险。
4. [企业技术规范](../DWG-Agent企业平台技术规范.md)：必须长期保持的架构、安全和交付约束。
5. [架构](architecture.md)：组件职责以及同步、异步、SSE、下载、存储一致性路径。
6. [API 参考](api.md)：从当前 FastAPI OpenAPI 自动生成的路由清单和关键生产契约。
7. [数据库](database.md)：28 张模型表、8 张 Celery runtime 表、迁移、流转账本和恢复集合。
8. [处理管线](processing-pipelines.md)与[通用工作流](workflow-framework.md)：转换执行与业务编排的区别。
9. [配置](configuration.md)、[部署](deployment.md)和[运维](operations.md)：本地/Compose 实施和事故处理。
10. [安全](security.md)、[验证](workflow-verification.md)与[路线图](roadmap.md)：发布前边界、证据和后续工作。

## 文档职责

| 文档 | 回答的问题 | 主要事实来源 |
|---|---|---|
| [架构](architecture.md) | 请求如何流动，各组件拥有何种状态 | `app/main.py`、route/service、Celery、Nginx、storage adapter |
| [API 参考](api.md) | 当前有哪些 HTTP 路由 | FastAPI `app.openapi()`；由 `make docs-generate` 生成 |
| [配置](configuration.md) | 每个环境变量怎样生效，默认值和风险是什么 | `core/config.py`、`.env*.example`、Compose、前端构建参数 |
| [数据库](database.md) | 表、关系、迁移、seed、Celery runtime 和备份边界是什么 | SQLAlchemy model、Alembic、Kombu/Celery backend、`scripts/db.sh` |
| [部署](deployment.md) | 怎样构建并启动本地/Compose 拓扑 | Dockerfile、`compose.yaml`、Nginx、`scripts/docker.sh` |
| [技术预览指南](developer-preview.md) | 怎样从 clean checkout 进入可开发状态 | 锁文件、脚本、门禁和本轮证据 |
| [审计报告](audit-report-2026-07-18.md) | 当前能交付到什么程度、还剩什么风险 | 本轮命令输出、源码与配置审计 |
| [开发](development.md) | 怎样修改代码并保持契约稳定 | 包清单、测试、Makefile、模块边界 |
| [运维](operations.md) | 怎样判定健康、备份、恢复和定位事故 | 健康端点、状态脚本、日志、备份脚本 |
| [处理管线](processing-pipelines.md) | 每条 Job 的输入、队列、Stage、输出和启用条件是什么 | pipeline API/service/task 和 Stage 文档 |
| [通用工作流](workflow-framework.md) | 人工编排层已经做了什么，还没有接通什么 | workflow model/service/API/UI/迁移/测试 |
| [安全](security.md) | 身份、权限、文件、下载、错误和审计如何约束 | auth/dependency/service、安全测试、Nginx |
| [验证](workflow-verification.md) | 什么测试能证明什么，当前实际跑到了哪一层 | 本轮命令输出与历史 E2E 记录 |
| [路线图](roadmap.md) | 哪些问题仍会阻断可复现或生产交付 | 当前实现缺口和明确非目标 |

## 事实层级

文档对每项能力分别记录以下层级：

1. **代码存在**：有 route/service/task/model，不代表默认可用。
2. **配置可启用**：flag 和依赖已满足，不代表真实样本通过。
3. **自动测试通过**：说明测试环境和替身；SQLite 不能证明 MySQL 锁行为。
4. **本地集成通过**：说明服务、样本、日期和未覆盖故障。
5. **生产可用**：还需要 TLS、备份恢复、监控告警、容量、安全和运维责任证据。

当前 Compose 只发布 HTTP，不发布 443；生产模式关闭运行时 `/docs`、`/redoc` 和 `/openapi.json`。`Stages/dxf2excel` 已转为父仓库正常跟踪的一方源码，但其 419 文件历史验证 corpus 不随仓库分发，clean checkout 只能重复内置单测。基础设施页面显示 `automated_backup=false`，不能替代备份调度或恢复演练。仓库也尚未声明 LICENSE，只按内部技术预览处理。

## 仓库归属

- `docs/`、根 README、技术规范和一方组件 README 由本仓库维护，采用中文。
- `Stages/` 中已跟踪的算法说明由对应 Stage 维护；平台文档只描述调用契约和验证边界。
- `third_parts/` 属于上游/外部项目，不因本项目改为中文文档而重写其原始说明。
- `Stages/dxf2excel` 是父仓库维护的一方 Stage；源码、锁文件和单测纳入门禁，外部 corpus、生成工作簿、缓存和虚拟环境不纳入版本控制。

## 更新规则

路由、模型、配置、端口、队列、状态机或部署脚本变化后，先更新代码和测试，再更新对应章节。禁止复制旧测试数字作为当前结论；带日期证据在后续变更后只能作为历史记录。

```bash
# 路由变化后重新生成中文 API 清单
make docs-generate

# 每次文档变化都执行
make docs-check

# 根据影响范围执行实现门禁
cd backend && uv run pytest -q && uv run alembic check
cd ../frontend && npm run build
```
