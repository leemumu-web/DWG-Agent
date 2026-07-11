# DWG-Agent 平台 — 文档

> 🌐 **语言:** [English](../README.md) | **中文**
>
> DWG-Agent 企业级 CAD 处理平台的交接文档。
> 每篇文档均与代码保持同步，并与 [`../`](../)（英文）一一对应。
> **规范依据:** [`../../DWG-Agent企业平台技术规范.md`](../../DWG-Agent企业平台技术规范.md)。

## 索引

| 文档 | 内容 |
|------|------|
| [architecture.md](architecture.md) | 系统总览、物理拓扑、分层架构、数据流、RBAC 模型、存储、实现状态矩阵、功能开关清单。 |
| [api.md](api.md) | 完整 REST API 参考 —— 全部 `/api/v1` 端点、认证、统一响应/错误信封、分页、状态码、角色、任务状态机、管线常量、功能开关。 |
| [database.md](database.md) | 引擎与连接池配置、完整表目录、实体关系、Alembic 迁移、种子数据、备份/恢复。 |
| [deployment.md](deployment.md) | 前置依赖、5 分钟快速上手、本地开发环境、Docker Compose 拓扑、环境变量参考、Nginx/MySQL/MinIO/Celery SQL transport/ODA 配置。 |
| [development.md](development.md) | 仓库结构导览、后端与前端工作流、测试策略、代码规范、依赖管理、常见陷阱。 |
| [security.md](security.md) | 认证流程、RBAC 模型、API 与文件安全措施、渗透测试问题修复、生产安全清单、审计日志覆盖。 |
| [roadmap.md](roadmap.md) | 当前集成基线、可靠性/Agent/CAD/运维优先级、验收门槛和明确非目标。 |
| [workflow-verification.md](workflow-verification.md) | 面向线上 API 的端到端全栈工作流验证演练。 |

## 约定

- 所有路径均**相对于仓库根目录**，不含任何机器相关的绝对路径。
- 英文位于 `docs/`；中文镜像位于 `docs/zh/`，结构完全一致（标题、表格、代码块）。仅自然语言不同；技术标记（端点路径、环境变量、代码、命令）在两者中保持一致。
- 修改接口时，须在同一次提交中同步更新对应的英文文档**及**其 `zh/` 对应文档。
