# DWG-Agent 开发指南

> **面向读者：** 首席开发者、新工程师、任何需要理解本代码库组织方式以及如何有效贡献的人。
>
> **当前状态：** 第一阶段已完成 — 包含 RESTful API、RBAC、文件管理和作业生命周期的平台骨架。第三阶段（DWG↔DXF 与 DXF→Excel 流水线）已在代码中完整实现，但默认通过功能开关禁用。第二阶段（Agent 子系统）和第四阶段（ZWCAD Worker）仍为桩代码。
>
> **权威来源：** 每一个设计决策都追溯到仓库根目录下的 `DWG-Agent企业平台技术规范.md`（技术规范）。如有疑问，请先阅读规范。

---

## 1. 仓库结构走读

每个目录都有其存在的特定理由。以下是每个目录的作用以及应放置其中的代码类型。

```
complete_framework/
├── DWG-Agent企业平台技术规范.md   ← 所有设计决策的权威来源（v2.0, 1296行）
├── CLAUDE.md                      ← Agent 指令 — 约定、禁止事项、文件映射
├── README.md                      ← 面向人类读者的项目概述
├── compose.yaml                   ← 所有服务的 Docker Compose 配置
├── .env.example                   ← 本地开发环境模板
├── .env.docker.example            ← Docker Compose 环境模板
├── Makefile                       ← 便捷目标（后端/前端/数据库/脚本的封装）
│
├── backend/                       ← Python 3.12, uv, FastAPI — 主要代码库
│   ├── pyproject.toml             ← 依赖项、ruff 配置、构建设置
│   ├── uv.lock                    ← 已提交 — 精确的依赖版本
│   ├── .python-version            ← 3.12（告知 uv 使用哪个 Python 版本）
│   ├── alembic.ini                ← Alembic 迁移配置（指向 app.core.config）
│   ├── Dockerfile                 ← 多阶段构建、非 root 用户、HEALTHCHECK
│   ├── .dockerignore
│   ├── app/                       ← 所有应用代码
│   │   ├── main.py                ← FastAPI 应用创建、生命周期、CORS、异常处理器
│   │   ├── api/v1/                ← 路由处理器（薄层 — 不含业务逻辑）
│   │   │   ├── router.py          ← 中央路由器：将所有子路由挂载到 /api/v1 下
│   │   │   ├── auth_api.py        ← POST /sessions, DELETE /sessions/current, POST /tokens/refresh, GET /me, PATCH /password
│   │   │   ├── users_api.py       ← 用户 CRUD、角色分配、密码重置
│   │   │   ├── roles_api.py       ← 角色 CRUD、权限分配
│   │   │   ├── projects_api.py    ← 项目 CRUD、成员管理
│   │   │   ├── files_api.py       ← 上传、列表、下载链接、删除
│   │   │   ├── drawings_api.py    ← 图纸 CRUD、版本管理
│   │   │   ├── jobs_api.py        ← 创建、列表、取消、重试、结果
│   │   │   ├── results_api.py     ← 结果详情、下载链接、审核提交、审核历史
│   │   │   ├── reviews_api.py     ← 待审核列表
│   │   │   ├── audit_logs_api.py  ← 审计日志列表（仅 super_admin/auditor）
│   │   │   ├── agent_runs_api.py  ← （第二阶段 — AGENT_ENABLED=false 时返回 503）
│   │   │   └── system_api.py      ← GET /system/health, GET /system/health/oda
│   │   ├── core/                  ← 横切关注点基础设施
│   │   │   ├── config.py          ← pydantic-settings，所有环境变量，MySQL/Redis/Celery URL
│   │   │   ├── security.py        ← JWT 创建/验证，密码哈希（argon2）
│   │   │   ├── permissions.py     ← RBAC 权限检查，依赖可调用对象
│   │   │   ├── exceptions.py      ← AppHTTPException（请使用此异常，不要使用裸 HTTPException）
│   │   │   ├── redis_client.py    ← 惰性初始化同步 Redis 客户端（不可用时安全降级）
│   │   │   ├── logger.py          ← 结构化日志
│   │   │   ├── validators.py      ← 排序列白名单（validate_sort_by — SQLi 防护，BUG-13）
│   │   │   └── constants.py       ← 枚举、字符串常量
│   │   ├── db/                    ← 数据库设置
│   │   │   ├── base.py            ← SQLAlchemy 声明式 Base
│   │   │   ├── session.py         ← 引擎创建（pool_pre_ping + 仅 MySQL 的连接池参数）、get_db 生成器
│   │   │   └── init_db.py         ← 种子数据：默认角色、权限、管理员用户
│   │   ├── models/                ← SQLAlchemy ORM 模型（10个文件）
│   │   │   ├── mixins.py          ← TimestampMixin（created_at, updated_at）
│   │   │   ├── user.py            ← 用户模型，含状态、密码字段
│   │   │   ├── role.py            ← 角色 + 权限 + 关联表
│   │   │   ├── project.py         ← 项目 + 项目成员
│   │   │   ├── file.py            ← 文件元数据（bucket, storage_key, sha256 等）
│   │   │   ├── drawing.py         ← 图纸 + 图纸版本
│   │   │   ├── job.py             ← 作业 + 作业步骤
│   │   │   ├── agent_run.py       ← AgentRun + AgentRunStep（第二阶段 Schema 已就绪）
│   │   │   ├── result.py          ← 分析结果 + 审核记录
│   │   │   └── audit_log.py       ← 审计日志
│   │   ├── schemas/               ← Pydantic v2 请求/响应模型
│   │   │   ├── common.py          ← 共享：分页参数、包装响应辅助函数
│   │   │   ├── auth_schema.py     ← 登录、令牌、密码修改
│   │   │   ├── user_schema.py     ← 用户创建、更新、响应
│   │   │   ├── project_schema.py  ← 项目创建、更新、响应
│   │   │   ├── file_schema.py     ← 文件上传、响应
│   │   │   ├── drawing_schema.py  ← 图纸创建、响应
│   │   │   ├── job_schema.py      ← 作业创建、响应、步骤响应
│   │   │   ├── result_schema.py   ← 结果响应、审核提交
│   │   │   ├── audit_schema.py    ← 审计日志响应
│   │   │   └── agent_schema.py    ← AgentRun 创建、响应（第二阶段）
│   │   ├── services/              ← 业务逻辑 — 所有状态变更操作（17 个模块）
│   │   │   ├── auth_service.py    ← 登录、登出、令牌刷新、密码修改
│   │   │   ├── user_service.py    ← 用户 CRUD、角色分配、启用/禁用
│   │   │   ├── project_service.py ← 项目 CRUD、成员管理
│   │   │   ├── file_service.py    ← 签名下载链接、结果映射 + ZIP 构建、访问检查
│   │   │   ├── drawing_service.py ← 图纸/版本 CRUD、版本递增
│   │   │   ├── job_service.py     ← 作业生命周期、入队路由、run_local_stub_job
│   │   │   ├── review_service.py  ← 审核提交、待审核列表
│   │   │   ├── agent_service.py   ← Agent 编排（第二阶段 — 抛出 NotImplementedError）
│   │   │   ├── storage_service.py ← 文件保存/检索/删除 + 校验（本地 + MinIO）
│   │   │   ├── audit_service.py   ← write_audit_log()、审计日志列表
│   │   │   ├── redis_memory.py    ← Agent 会话记忆（第二阶段基础设施）
│   │   │   ├── cache_service.py   ← 通用缓存层（由 dxf2excel 使用）
│   │   │   ├── dxf_service.py     ← 通过 ODA 子进程编排 DWG→DXF（第三阶段）
│   │   │   ├── dxf2dwg_service.py ← 通过 ODA 反向编排 DXF→DWG（第三阶段）
│   │   │   ├── dxf2excel_service.py ← 批量 DXF→Excel 材料表提取（第三阶段）
│   │   │   ├── dxf_stats.py       ← 标准库 DXF 实体/段计数器（保真度指标）
│   │   │   └── job_events.py      ← Redis 发布/订阅作业进度 + SSE 流
│   │   ├── repositories/          ← 占位 — 空的 __init__.py
│   │   │                           （数据库访问将在第二阶段+从 services 中提取）
│   │   ├── agents/                ← 占位 — agent_factory、prompts、tool_registry 桩代码
│   │   ├── mcp_client/            ← 占位 — MCP 客户端 + 适配器桩代码
│   │   ├── workers/               ← celery_app + report/dxf/dxf2dwg/dxf2excel 任务已实现；agent/cad 任务桩代码
│   │   ├── storage/               ← 存储抽象层
│   │   │   ├── base.py            ← 抽象 StorageBackend
│   │   │   ├── local_storage.py   ← 本地文件系统（第一阶段已激活）
│   │   │   └── minio_storage.py   ← MinIO/S3 后端，用于 Docker 部署
│   │   ├── integrations/zwcad/    ← 占位 — ZWCAD Worker 客户端 + Schema（第四阶段）
│   │   └── utils/                 ← 工具函数
│   │       ├── path_utils.py      ← ensure_within_root() — 所有文件路径必须经过此函数处理
│   │       └── file_hash.py       ← SHA-256 计算
│   ├── tests/                     ← 599 个测试，31 个测试文件（pytest）
│   │   ├── conftest.py            ← 自动使用 fixture：FakeRedis + 内存 SQLite 隔离
│   │   ├── test_health.py         ← 健康检查端点
│   │   ├── test_config.py         ← 设置验证（MySQL、Redis、Celery URL 计算）
│   │   ├── test_db_session.py     ← 引擎创建、会话、pragma
│   │   ├── test_smoke_flow.py     ← 完整正向路径：登录 → 创建项目 → 上传 → 作业
│   │   ├── test_security_boundaries.py  ← 未认证/未授权访问测试
│   │   ├── test_api_regressions.py      ← 端点契约测试
│   │   ├── test_new_features.py         ← 最近添加功能的测试
│   │   ├── test_token_lifecycle.py      ← 访问令牌/刷新令牌流程
│   │   ├── test_rigorous.py             ← 边缘情况和错误处理
│   │   ├── test_deep_verify.py          ← 更深层次的验证测试
│   │   ├── test_edge_cases.py           ← 边界条件测试
│   │   ├── test_stage1_boundaries.py    ← 第一阶段范围边界测试（禁用功能 → 503）
│   │   ├── test_adversarial_auth.py     ← 对抗性认证/令牌攻击测试
│   │   ├── test_adversarial_files.py    ← 对抗性上传/zip 炸弹/路径遍历测试
│   │   ├── test_adversarial_jobs.py     ← 对抗性作业生命周期/RBAC 测试
│   │   ├── test_job_lifecycle.py        ← 作业状态转换、取消、重试
│   │   ├── test_rbac_deep.py            ← 跨角色和资源的深度 RBAC
│   │   ├── test_service_layer.py        ← 服务层单元测试
│   │   ├── test_file_service.py         ← 文件服务（签名链接、ZIP、访问）测试
│   │   ├── test_dxf_pipeline.py         ← DWG→DXF 流水线测试（第三阶段）
│   │   ├── test_dxf2dwg_pipeline.py     ← DXF→DWG 流水线测试（第三阶段）
│   │   ├── test_dxf2excel_pipeline.py   ← DXF→Excel 流水线测试（第三阶段）
│   │   ├── test_cache_service.py        ← 缓存层测试（FakeRedis）
│   │   ├── test_redis_client.py         ← Redis 客户端连接测试
│   │   ├── test_redis_memory.py         ← Agent 记忆服务测试
│   │   ├── test_redis_real.py           ← 真实 Redis 集成测试（13 个测试，自动跳过）
│   │   ├── test_compose.py              ← Docker Compose 配置验证
│   │   ├── test_celery_minio_deployment.py ← Celery/MinIO 部署配置验证
│   │   ├── test_cross_audit_fixes.py     ← 横切审计修复验证
│   │   ├── test_migrations.py           ← Alembic 迁移测试
│   │   └── test_scripts.py              ← Shell 脚本验证
│   ├── migrations/               ← Alembic
│   │   ├── env.py                 ← 迁移环境（导入 Base + 所有模型）
│   │   ├── script.py.mako         ← 新迁移模板
│   │   └── versions/              ← 4 个迁移脚本（初始 17 表 → 时间戳修复 → resource_id 类型 → batch_name）
│   └── var/                       ← 运行时数据 — 上传文件、SQLite 数据库（gitignored）
│
├── Stages/                        ← 独立的流水线引擎包（后端的 uv 路径依赖）
│   ├── dwg2dxf/                   ← dwg-converter：通过 ODA File Converter 实现 DWG→DXF（内置 tools/oda AppImage）
│   ├── dxf2dwg/                   ← dxf-converter：通过 ODA File Converter 实现 DXF→DWG 反向转换
│   └── dxf2excel/                 ← dxf2excel：纯 Python DXF→Excel 材料表提取
│
├── frontend/                      ← React 19 + TypeScript + Vite + Ant Design 6
│   ├── package.json               ← 所有版本已锁定 — 禁止使用 "latest"
│   ├── package-lock.json
│   ├── tsconfig.json
│   ├── vite.config.ts             ← Vite 配置（无代理；本地开发需设置 VITE_API_BASE_URL）
│   ├── index.html
│   └── src/
│       ├── main.tsx               ← ReactDOM 入口
│       ├── App.tsx                ← 根组件
│       ├── api/                   ← 所有 API 调用通过此层（12 个模块 + client.ts）
│       │   ├── client.ts          ← Axios 实例，含拦截器（认证头、401 刷新）
│       │   ├── auth.api.ts        ← login, logout, refresh, me, changePassword
│       │   ├── users.api.ts       ← 用户 CRUD
│       │   ├── roles.api.ts       ← 角色 CRUD
│       │   ├── projects.api.ts    ← 项目 CRUD、成员
│       │   ├── files.api.ts       ← 文件上传、列表、下载链接
│       │   ├── drawings.api.ts    ← 图纸 CRUD、版本
│       │   ├── jobs.api.ts        ← 作业 CRUD、取消、重试
│       │   ├── results.api.ts     ← 结果详情、审核提交
│       │   ├── reviews.api.ts     ← 待审核列表
│       │   ├── agent-runs.api.ts  ← Agent 执行（第二阶段）
│       │   ├── audit-logs.api.ts  ← 审计日志列表
│       │   └── system.api.ts      ← 系统/ODA 健康检查
│       ├── app/                   ← 应用外壳
│       │   ├── router.tsx         ← 路由定义，含权限守卫
│       │   ├── providers.tsx      ← TanStack Query、Ant Design ConfigProvider
│       │   └── layout.tsx         ← 主布局：侧边栏、头部、内容区域
│       ├── features/              ← 页面模块（10 个目录）
│       │   ├── auth/              ← 登录页面
│       │   ├── dashboard/         ← 仪表盘/工作台
│       │   ├── users/             ← 用户管理（管理员）
│       │   ├── projects/          ← 项目列表 + 详情
│       │   ├── files/             ← 文件列表 + 上传
│       │   ├── drawings/          ← 图纸列表 + 详情
│       │   ├── jobs/              ← 作业列表 + 详情
│       │   ├── reviews/           ← 待审核 + 审核表单
│       │   ├── profile/           ← 用户个人资料页
│       │   └── admin/             ← 角色、审计日志（super_admin）
│       ├── components/            ← 共享 UI 组件（7 个文件）
│       │   ├── FileUpload.tsx         ← 拖拽文件上传
│       │   ├── PermissionGuard.tsx    ← 基于角色的渲染守卫
│       │   ├── ConversionPage.tsx     ← DWG/DXF 转换启动 UI
│       │   ├── ExcelPreview.tsx       ← Excel 结果预览
│       │   ├── ZipDownloadModal.tsx   ← 批量 ZIP 下载对话框
│       │   ├── JobTimeline.tsx        ← 作业步骤/进度时间线
│       │   └── ui.tsx                 ← 共享 UI 原语
│       ├── hooks/                 ← 自定义 React hooks
│       ├── utils/                 ← 前端工具函数
│       ├── stores/                ← Zustand 状态管理
│       │   └── auth.store.ts      ← 当前用户、角色、令牌
│       └── types/                 ← TypeScript 类型定义
│           ├── auth.ts
│           ├── user.ts
│           ├── project.ts
│           ├── file.ts
│           ├── drawing.ts
│           ├── job.ts
│           ├── result.ts
│           ├── agent.ts
│           └── audit.ts
│
├── docs/                          ← 交接文档（8 份 + zh/ 翻译）
│   ├── architecture.md            ← 系统架构概览
│   ├── api.md                     ← API 参考
│   ├── database.md                ← 数据库 Schema 参考
│   ├── deployment.md              ← 部署与运维指南
│   ├── development.md             ← 本文档
│   ├── roadmap.md                 ← 六阶段交付路线图
│   ├── security.md                ← 安全架构与渗透测试发现
│   ├── workflow-verification.md   ← 端到端工作流验证记录
│   └── zh/                        ← 上述文档的中文翻译
│
├── infra/                         ← 部署基础设施配置
│   ├── nginx/
│   │   ├── nginx.conf             ← Docker 部署 nginx 配置（容器路径）
│   │   └── nginx.local.conf       ← 本地开发 nginx 配置（绝对路径，sed 模板化）
│   ├── mysql/
│   │   └── init.sql               ← Docker 初始 Schema + 种子数据
│   ├── redis/
│   │   └── redis.conf             ← Docker 用 AOF、LRU、maxmemory 256mb
│   ├── minio/                     ← MinIO 配置占位
│   └── verify.sh                  ← 基础设施验证脚本
│
├── scripts/                       ← 开发/运维 Shell 脚本
│   ├── lib.sh                     ← 共享函数（日志、环境加载）
│   ├── start-dev.sh               ← 启动后端 + 前端开发服务器
│   ├── start-all.sh               ← 启动所有服务（含 nginx、redis、mysql）
│   ├── stop-all.sh                ← 停止所有服务
│   ├── status.sh                  ← 检查服务状态
│   └── db.sh                      ← 数据库辅助工具（迁移、种子、重置）
│
├── agents/                        ← 占位 — 未来 Agent 定义模块
├── cad-worker/                    ← 占位 — Windows C# CAD Worker
└── tests/                         ← 占位 — 未来端到端/集成测试
```

---

## 2. 后端开发工作流

本节演示如何向后端添加一个完整的新功能，从数据库到 API 再到测试。我们将以"添加新的 `notifications` 资源"作为具体示例。

### 2.1 分步指南：添加新端点

**步骤 1：定义 SQLAlchemy 模型**（`app/models/notification.py`）

```python
from __future__ import annotations

from sqlalchemy import BigInteger, Column, ForeignKey, String, Text, Boolean
from app.db.base import Base
from app.models.mixins import TimestampMixin


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("sys_users.id"), nullable=False)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False, nullable=False)
```

然后在 `app/db/base.py` 中注册该模型（或确保在 `app/models/__init__.py` 中导入，以便 Alembic 的 `env.py` 能发现它）。

**步骤 2：创建 Alembic 迁移**

```bash
cd backend
uv run alembic revision --autogenerate -m "add notifications table"
uv run alembic upgrade head
```

请务必检查生成的迁移文件。Alembic 自动生成通常能正确处理大多数列类型，但需检查 `ondelete` 级联、默认值以及可能需要手动调整的枚举类型。

**步骤 3：定义 Pydantic Schema**（`app/schemas/notification_schema.py`）

```python
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, ConfigDict


class NotificationCreate(BaseModel):
    title: str
    message: str


class NotificationUpdate(BaseModel):
    is_read: bool | None = None


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    title: str
    message: str
    is_read: bool
    created_at: datetime
```

所有响应 Schema 都应使用 `ConfigDict(from_attributes=True)`，以便能从 ORM 模型实例直接构造。

**步骤 4：编写服务逻辑**（`app/services/notification_service.py`）

Service 层包含业务逻辑。它们接收 SQLAlchemy `Session` 和 Pydantic Schema，返回 ORM 模型或 Pydantic 响应。Service 层**必须不能**依赖于 FastAPI `Request` 对象。

```python
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.notification import Notification
from app.schemas.notification_schema import NotificationCreate


def create_notification(db: Session, user_id: int, data: NotificationCreate) -> Notification:
    notification = Notification(user_id=user_id, **data.model_dump())
    db.add(notification)
    db.commit()
    db.refresh(notification)
    return notification


def list_notifications(db: Session, user_id: int, is_read: bool | None = None):
    stmt = select(Notification).where(Notification.user_id == user_id)
    if is_read is not None:
        stmt = stmt.where(Notification.is_read == is_read)
    return db.scalars(stmt.order_by(Notification.created_at.desc())).all()
```

**步骤 5：添加路由处理器**（`app/api/v1/notifications_api.py`）

API 路由处理器是薄层 — 它们解析参数、调用 Service、返回包装后的响应。此处不包含任何业务逻辑。

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from app.api.deps import CurrentUser
from app.db.session import get_db
from app.schemas.common import ok
from app.schemas.notification_schema import NotificationCreate, NotificationResponse
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.post("", status_code=201)
def create_notification(
    payload: NotificationCreate,
    current_user: CurrentUser,
    request: Request,
    db: Session = Depends(get_db),
):
    notification = notification_service.create_notification(db, current_user.id, payload)
    return ok(NotificationResponse.model_validate(notification).model_dump(), request.state.request_id)


@router.get("")
def list_notifications(
    is_read: bool | None = Query(None),
    current_user: CurrentUser,
    request: Request,
    db: Session = Depends(get_db),
):
    notifications = notification_service.list_notifications(db, current_user.id, is_read)
    return ok(
        [NotificationResponse.model_validate(n).model_dump() for n in notifications],
        request.state.request_id,
    )
```

**步骤 6：在中央路由器中注册**（`app/api/v1/router.py`）

```python
from app.api.v1.notifications_api import router as notifications_router

api_router.include_router(notifications_router)
```

**步骤 7：编写测试**（`tests/test_notifications_api.py`）

完整测试模式参见 [第 4 节](#4-测试策略与如何编写测试)。

**步骤 8：编写审计日志**（如果端点会改变状态）

对于任何状态变更操作（创建、更新、删除），调用 `write_audit_log()`：

```python
from app.services.audit_service import write_audit_log

write_audit_log(
    db=db,
    actor_user_id=current_user.id,
    action="notification.create",
    resource_type="notification",
    resource_id=notification.id,
    after_json=NotificationResponse.model_validate(notification).model_dump(),
)
```

### 2.2 架构规则（来自规范第 6.2 节）

以下规则不可协商：

| 层 | 目录 | 允许 | 不允许 |
|-------|-----------|------------|----------------|
| API | `app/api/v1/` | 路由、参数解析、依赖注入、响应包装 | 业务逻辑、直接数据库查询 |
| Service | `app/services/` | 业务逻辑编排 | 依赖 FastAPI `Request` |
| Repository | `app/repositories/` | 数据库读写封装 | 业务规则 |
| Worker | `app/workers/` | 调用 Service、执行异步任务 | 重复业务逻辑 |
| Agent | `app/agents/` | 工具编排、LLM 交互 | 直接访问数据库/文件系统 |
| Model | `app/models/` | ORM 表定义 | 业务逻辑 |

**额外硬性规则：**

1. **所有文件路径必须经过 `app/utils/path_utils.py`**（`ensure_within_root()`）。切勿直接从用户输入构造存储路径。
2. **所有业务端点要求认证。** `current_user: CurrentUser` 依赖项绝不能有 `= None` 默认值 — 否则未认证的请求将到达业务逻辑。
3. **使用 `AppHTTPException`**（来自 `app.core.exceptions`）处理业务错误。不要抛出裸 `fastapi.HTTPException` — `AppHTTPException` 确保一致的错误响应格式。
4. **状态码**遵循规范第 7.2 节：查询/更新用 200，资源创建用 201，异步受理用 202，删除用 204。切勿对错误返回 `200 + code: 0`。

### 2.3 API 响应格式

每个端点返回以下格式之一：

**单个资源 / 变更操作：**
```json
{
  "data": { ... },
  "meta": { "request_id": "req_...", "timestamp": "2026-07-03T10:00:00+08:00" }
}
```

**带分页的列表：**
```json
{
  "data": [ ... ],
  "pagination": { "page": 1, "page_size": 20, "total": 120 },
  "meta": { "request_id": "req_...", "timestamp": "..." }
}
```

**错误：**
```json
{
  "error": { "code": "RESOURCE_NOT_FOUND", "message": "...", "details": {} },
  "meta": { "request_id": "req_...", "timestamp": "..." }
}
```

### 2.4 依赖参数顺序

FastAPI 按位置解析参数。路由函数签名中的正确顺序必须遵守 **Python 的语法规则**：无默认值的参数必须放在有默认值的参数之前。

由于 `CurrentUser`（`Annotated[..., Depends(...)]` 类型）在函数签名中没有 `= default`，它必须出现在 `Depends()` 和 `Query()` 参数之前：

```python
@router.patch("/{user_id}")
def update_user(
    user_id: int,                         # 1. 路径参数（无默认值）
    payload: UserUpdate,                  # 2. 请求体（无默认值）
    current_user: CurrentUser,            # 3. Annotated Depends（无默认值 — 必须在有默认值之前）
    page: int = Query(1),                 # 4. 查询参数（有默认值）
    db: Session = Depends(get_db),        # 5. 显式 Depends()（有默认值）
    file: UploadFile | None = None,       # 6. UploadFile — 始终放在最后
):
```

搞错此顺序会导致 Python `SyntaxError`，而不仅仅是 FastAPI 422 错误。关键规则：**所有无默认值的参数放在前面**，然后是有默认值的参数，`UploadFile` 始终放在最后。

---

## 3. 前端开发工作流

### 3.1 添加新页面

**步骤 1：添加 API 客户端模块**（`src/api/notifications.api.ts`）

所有 HTTP 调用通过 `src/api/client.ts` 进行，它是一个 Axios 实例，具备：
- 自动注入 `Authorization: Bearer <token>` 请求头（从 `sessionStorage` 通过 Zustand store 读取 token）
- 从 `VITE_API_BASE_URL` 环境变量获取 Base URL（默认为空字符串）

```typescript
import client from './client';
import type { NotificationResponse } from '@/types/notification';

export const listNotifications = (isRead?: boolean) =>
  client.get<{ data: NotificationResponse[] }>('/api/v1/notifications', { params: { is_read: isRead } });

export const markAsRead = (id: number) =>
  client.patch(`/api/v1/notifications/${id}`, { is_read: true });
```

**切勿在组件中直接写 `fetch()` 或原始 `axios.get()`。** 所有 API 调用通过 `src/api/` 模块进行。

**步骤 2：添加 TypeScript 类型**（`src/types/notification.ts`）

类型应镜像对应的 Pydantic 响应 Schema：

```typescript
export interface NotificationResponse {
  id: number;
  user_id: number;
  title: string;
  message: string;
  is_read: boolean;
  created_at: string;
}
```

**步骤 3：创建功能页面**（`src/features/notifications/`）

使用 TanStack Query 管理服务端状态，使用 Zustand 管理仅客户端状态（认证、UI）。

```typescript
import { useQuery } from '@tanstack/react-query';
import { listNotifications } from '@/api/notifications.api';

export default function NotificationsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['notifications'],
    queryFn: () => listNotifications(),
  });
  // ...
}
```

**步骤 4：添加路由**（`src/app/router.tsx`）

使用 `PermissionGuard` 组件包裹需要特定角色的路由：

```tsx
{
  path: 'notifications',
  element: <PermissionGuard requiredRoles={['engineer', 'admin']}><NotificationsPage /></PermissionGuard>,
}
```

**步骤 5：添加菜单项**（在 `src/app/layout.tsx` 侧边栏配置中）

### 3.2 前端约定

**API Base URL：** 始终使用 `VITE_API_BASE_URL` 环境变量。本地开发时，需要将其设置为 `http://127.0.0.1:8000`（Vite 开发服务器没有内置代理）。在 Docker 中，nginx 提供构建后的前端并代理 `/api/v1/` 到后端，因此环境变量可能为空。

**令牌存储：** 仅使用 `sessionStorage` — 绝不使用 `localStorage`。这可以缓解基于 XSS 的令牌窃取。`client.ts` 中的 Axios 拦截器每次请求时从 `sessionStorage` 读取。

**状态管理：**
- **TanStack Query** 用于服务端状态（列表、详情视图、使查询失效的变更操作）。
- **Zustand** 用于仅客户端状态（当前用户、认证状态、UI 开关）。
- 不要将服务端数据存储在 Zustand 中 — 那是 TanStack Query 的职责。

**权限执行：** 三个层次：
1. **路由级别：** `PermissionGuard` 组件包裹受保护路由。
2. **菜单级别：** 侧边栏菜单项根据用户角色条件渲染。
3. **组件/按钮级别：** 各个操作按钮在渲染前检查权限。

**重要提示：** 前端权限检查仅是 UX 优化。后端才是真正的执行点。永远不要假设前端已正确拦截用户。

**依赖版本：** 所有 `package.json` 依赖锁定到精确版本。禁止使用 `"latest"`。使用 `npm install <package>@<version>` 添加新依赖。

---

## 4. 测试策略与如何编写测试

### 4.1 测试架构

测试套件设计追求速度和隔离：

- **数据库：** 每个测试通过 `StaticPool` 获得隔离的内存 SQLite 数据库。`conftest.py` 的 autouse fixture 创建全新的引擎，构建所有表，并覆盖 FastAPI 的 `Depends(get_db)` 使用测试会话。没有任何测试触及真实的 MySQL 数据库。
- **Redis：** 每个测试通过 `conftest.py` 的 autouse fixture 获得一个 `FakeRedis` 实例，该 fixture 通过 monkeypatch 替换 `app.core.redis_client` 模块级单例。键在测试之间被清空。单独的 `test_redis_real.py` 文件测试真实 Redis 服务器，在 Redis 不可用时自动跳过。
- **HTTP：** 所有测试使用 `fastapi.testclient.TestClient` — 进程内测试，无需真实 HTTP 服务器。无网络依赖。

### 4.2 运行测试

```bash
cd backend

# 运行所有测试（安静模式）
uv run pytest -q

# 运行特定测试文件
uv run pytest tests/test_auth.py -q

# 运行特定测试函数
uv run pytest tests/test_auth.py::test_login_success -q

# 以详细输出运行（查看测试名称）
uv run pytest -v

# 运行并在首次失败时停止
uv run pytest -x

# 仅运行匹配关键字表达式的测试
uv run pytest -k "login"
```

预期：Redis 可用时 599 通过，0 失败。如果 Redis 不可用，`test_redis_real.py` 中的 13 个测试将被跳过。

### 4.3 测试前进行 Lint 检查

```bash
cd backend
uv run ruff check app tests    # 必须零错误通过
```

### 4.4 测试文件命名

- 文件：`test_<topic>.py`
- 函数：`test_<what>_<expected_behavior>`
- 鼓励使用描述性名称 — 函数名本身就是文档。

### 4.5 辅助函数模式（跨测试共享）

大多数测试文件使用以下辅助函数：

```python
from fastapi.testclient import TestClient
from app.db.init_db import init_db
from app.main import app


def _client() -> TestClient:
    """创建带有全新种子数据库的 TestClient。"""
    init_db()
    return TestClient(app)


def _admin(client: TestClient) -> dict[str, str]:
    """以管理员身份登录并返回 Authorization 请求头字典。"""
    resp = client.post("/api/v1/auth/sessions", json={
        "username": "admin",
        "password": "SuperAdminPass1"
    })
    assert resp.status_code == 201
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _engineer(client: TestClient) -> dict[str, str]:
    """以工程师身份登录并返回 Authorization 请求头字典。"""
    # 如果需要，先创建工程师用户，然后登录
    ...
```

### 4.6 常见场景测试模式

**测试需要认证的端点：**
```python
def test_list_users_requires_auth():
    client = _client()
    resp = client.get("/api/v1/users")
    assert resp.status_code == 401
```

**测试受保护的管理员端点：**
```python
def test_delete_user_as_admin():
    client = _client()
    headers = _admin(client)
    resp = client.delete("/api/v1/users/2", headers=headers)
    assert resp.status_code == 204
```

**测试 RBAC — 工程师不能访问管理员端点：**
```python
def test_engineer_cannot_create_user():
    client = _client()
    headers = _engineer(client)
    resp = client.post("/api/v1/users", json={...}, headers=headers)
    assert resp.status_code == 403
```

**测试错误响应格式：**
```python
def test_not_found_returns_proper_error_format():
    client = _client()
    headers = _admin(client)
    resp = client.get("/api/v1/users/99999", headers=headers)
    assert resp.status_code == 404
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == "USER_NOT_FOUND"
    assert "meta" in body
    assert "request_id" in body["meta"]
```

### 4.7 关键测试规则

**绝不使用 `assert False`。** 改用 `raise AssertionError("message")`。Ruff 规则 B011 拒绝裸 `assert False`，因为它会模糊地捕获 `AssertionError`。

```python
# 错误
if some_condition:
    assert False

# 正确
if some_condition:
    raise AssertionError("Expected X but got Y")
```

### 4.8 关键测试文件及其覆盖范围

| 文件 | 用途 |
|------|---------|
| `test_smoke_flow.py` | 端到端正向路径：登录 → 项目 → 上传 → 作业 → 结果 |
| `test_security_boundaries.py` | 每个受保护端点的未认证/未授权访问 |
| `test_api_regressions.py` | 契约测试：每个端点返回正确的状态码和格式 |
| `test_token_lifecycle.py` | 访问令牌创建、刷新、过期、登出 |
| `test_new_features.py` | 最近实现功能的测试 |
| `test_rigorous.py` | 穷举边缘情况和错误处理测试 |
| `test_deep_verify.py` | 业务规则和数据完整性的更深层次验证 |
| `test_edge_cases.py` | 边界条件：空输入、最大长度字符串等 |
| `test_job_lifecycle.py` | 作业状态转换、取消、重试生命周期 |
| `test_rbac_deep.py` | 跨角色和资源的深度 RBAC 权限检查 |
| `test_service_layer.py` | 服务层单元测试（与 HTTP 隔离的业务逻辑） |
| `test_file_service.py` | 文件服务：签名下载链接、ZIP 构建、访问检查 |
| `test_dxf_pipeline.py` | DWG→DXF 流水线（第三阶段）：转换、步骤、结果持久化 |
| `test_dxf2dwg_pipeline.py` | DXF→DWG 反向流水线（第三阶段） |
| `test_dxf2excel_pipeline.py` | DXF→Excel 材料表提取流水线（第三阶段） |
| `test_adversarial_auth.py` | 对抗性认证/令牌攻击面 |
| `test_adversarial_files.py` | 对抗性上传：zip 炸弹、路径遍历、错误文件头 |
| `test_adversarial_jobs.py` | 对抗性作业生命周期与 RBAC 探测 |
| `test_stage1_boundaries.py` | 验证禁用的第二阶段+功能返回 503（而非 500） |
| `test_health.py` | 健康检查端点 |
| `test_config.py` | 设置验证、MySQL/Redis URL 计算 |
| `test_db_session.py` | 引擎创建、WAL pragma、连接池 |
| `test_redis_client.py` | Redis 客户端初始化和故障模式 |
| `test_redis_memory.py` | Agent 记忆服务（存储/检索/裁剪/TTL） |
| `test_redis_real.py` | 真实 Redis 集成测试（不可用时自动跳过） |
| `test_cache_service.py` | 缓存层 get/set/delete/namespace 操作 |
| `test_migrations.py` | Alembic 迁移 up/down/roundtrip |
| `test_compose.py` | Docker Compose 配置验证 |
| `test_celery_minio_deployment.py` | Celery/MinIO 部署配置验证 |
| `test_cross_audit_fixes.py` | 横切审计修复验证 |
| `test_scripts.py` | Shell 脚本验证 |

---

## 5. 代码约定

### 5.1 Python（后端）

**文件头：** 每个功能性 `.py` 文件以以下内容开头：
```python
from __future__ import annotations
```
这使得所有文件都能使用 PEP 604 语法（`X | None`、`list[dict]`），即使是在定义带有前向引用的模型的文件中。**例外：** `__init__.py` 文件（不包含类型注解）以及未来阶段的占位/桩代码文件可豁免。

**类型提示：**
- 使用 `X | None`，而不是 `Optional[X]`（由 ruff UP007 强制）。
- 使用 `list[X]`、`dict[K, V]`、`tuple[X, Y]`，而不是 `List`、`Dict`、`Tuple`（ruff UP006）。
- 从 `collections.abc` 导入：`from collections.abc import Callable, Sequence`（ruff UP035）。
- 所有公开函数签名必须有类型注解。

**导入：**
- 使用 ruff 的 `I`（isort）规则排序，即：标准库优先，然后第三方库，最后本地库（`app.*`）。
- 无未使用的导入（ruff F401）。
- 无通配符导入。

**行长度：** 100 字符（在 `pyproject.toml` 的 `[tool.ruff]` 中配置）。

**空白符：**
- 无尾随空白符（ruff W291）。
- 文件末尾必须有换行符。
- 4 空格缩进（不可用 Tab）。

**Pydantic 模型：**
```python
from pydantic import BaseModel, ConfigDict

class MySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # 启用 ORM → Schema 转换
    name: str
    count: int = 0
```

**数据库模型：**
```python
from app.db.base import Base
from app.models.mixins import TimestampMixin

class MyModel(Base, TimestampMixin):
    __tablename__ = "my_table"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
```

任何需要 `created_at` / `updated_at` / `deleted_at` 的表都使用 `TimestampMixin`。

### 5.2 TypeScript（前端）

- 所有 API 响应必须在 `src/types/` 中定义 TypeScript 接口。
- 不要使用 `any` — 如果类型未知，使用 `unknown` 并进行类型收窄。
- 对象形状优先使用 `interface`，联合/交叉类型使用 `type`。
- React 组件使用函数声明配合 `React.FC` 或显式属性类型。
- 适用时使用 `const` 断言（`as const`）表示字面量类型。

### 5.3 API 命名约定

- **资源名称使用复数名词：** `/api/v1/users`、`/api/v1/projects`。
- **子资源嵌套在父资源下：** `/api/v1/projects/{id}/members`。
- **复合名称使用 kebab-case：** `/api/v1/agent-runs`、`/api/v1/audit-logs`。
- **文件和模块名称：** Python 用 snake_case，前端文件用 kebab-case。
- **禁止基于动词的端点：** 绝对不能 `/getUser` 或 `/createJob`。使用 HTTP 方法：`GET /api/v1/users/{id}`、`POST /api/v1/jobs`。
- **状态变更操作作为子资源：** `POST /api/v1/jobs/{id}/cancellation-requests`、`POST /api/v1/jobs/{id}/retry-requests`。

---

## 6. 依赖管理

### 6.1 后端：uv

**添加运行时依赖：**
```bash
cd backend
uv add <package-name>
```

**添加开发依赖：**
```bash
cd backend
uv add --dev <package-name>
```

**移除依赖：**
```bash
cd backend
uv remove <package-name>
```

**同步（从锁定文件安装所有依赖）：**
```bash
cd backend
uv sync
```

**关键规则：** 始终使用 `uv add` / `uv remove` 修改依赖。切勿手动编辑 `pyproject.toml` 中的依赖列表 — 否则 `uv.lock` 将不同步。`uv.lock` 文件被提交到版本控制，因此每个人获得完全相同的依赖树。

**Python 版本：** 在 `pyproject.toml` 中锁定为 `>=3.12,<3.13`。`.python-version` 文件告知 `uv` 明确使用 Python 3.12。

**本地路径依赖（陷阱）：** 三个运行时依赖——`dwg-converter`、`dxf-converter` 和 `dxf2excel`——是**可编辑的本地路径依赖**，通过 `backend/pyproject.toml` 中的 `[tool.uv.sources]` 从 `Stages/dwg2dxf`、`Stages/dxf2dwg` 和 `Stages/dxf2excel` 解析。由于这些路径相对于 `backend/`，Docker 构建上下文必须是**仓库根目录**（`dockerfile: backend/Dockerfile`），以便 `../Stages/*` 能正确解析。`editable=true` 使每个包的 `__file__` 指向其源码树，从而让 `check_env.py` 能定位 `Stages/dwg2dxf/tools/oda/` 下捆绑的 ODA 二进制。切勿在不更新 `[tool.uv.sources]` 的情况下移动或重命名 `Stages/` 目录。

### 6.2 前端：npm

**添加依赖：**
```bash
cd frontend
npm install <package>@<exact-version>
```

**移除依赖：**
```bash
cd frontend
npm uninstall <package>
```

**安装所有依赖：**
```bash
cd frontend
npm ci    # 使用 package-lock.json — CI/可重现构建首选
# 或
npm install    # 如果 package.json 已更改，更新 package-lock.json
```

**关键规则：** 绝不使用 `"latest"` 作为 `package.json` 中的版本说明符。每个依赖锁定到精确版本或安全的语义化版本范围。使用 `"latest"` 意味着不同开发者和 CI 构建会在无警告的情况下获得不同版本。

---

## 7. 常见陷阱与注意事项

### 7.1 FastAPI 依赖顺序

搞错 FastAPI 路由函数中的参数顺序是令人困惑的 422 错误的第一大来源。**根本约束是 Python 语法**：无默认值的参数必须放在有默认值的参数之前。

由于 `CurrentUser`（`Annotated[..., Depends(...)]` 类型）在签名中没有 `= default`，它必须出现在 `Depends()` 和 `Query()` 参数之前：

1. 路径参数（如 `user_id: int`）— 无默认值
2. 请求体（如 `payload: UserUpdate`）— 无默认值
3. 无默认值的 Annotated Depends（如 `current_user: CurrentUser`）
4. 查询参数（如 `page: int = Query(1)`）— 有默认值
5. `Depends()` 依赖（如 `db: Session = Depends(get_db)`）— 有默认值
6. `UploadFile` — 始终放在最后

**正确**签名示例：
```python
@router.patch("/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdate,
    current_user: CurrentUser,       # 无默认值 — 必须在 Depends() 之前
    db: Session = Depends(get_db),   # 有默认值 — 必须在无默认值参数之后
):
```

**错误**签名示例（Python `SyntaxError`）：
```python
# 错误：无默认值的参数跟在有默认值的参数后面
@router.patch("/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),   # 有默认值
    current_user: CurrentUser,       # 无默认值 — SyntaxError！
):
```

### 7.2 绝不为 `current_user` 设置 None 默认值

```python
# 错误 — 此端点将接受未认证请求
def list_projects(current_user: CurrentUser = None):
    ...

# 正确 — 业务端点强制要求认证
def list_projects(current_user: CurrentUser):
    ...
```

唯一应接受未认证请求的端点是 `/health`、`POST /api/v1/auth/sessions`（登录）和 `POST /api/v1/auth/tokens/refresh`（通过 httpOnly 的 `dwg_refresh_token` cookie 验证，而非 Bearer 令牌）。

### 7.3 使用 AppHTTPException，而非 HTTPException

```python
# 错误 — 绕过统一的错误响应格式
from fastapi import HTTPException
raise HTTPException(status_code=404, detail="User not found")

# 正确 — 产生一致的 {"error": {"code": ..., "message": ...}} 格式
from app.core.exceptions import AppHTTPException
raise AppHTTPException(status_code=404, code="USER_NOT_FOUND", message="User not found")
```

### 7.4 文件路径安全

切勿直接从用户输入构造文件路径：

```python
# 错误 — 路径遍历漏洞
file_path = f"uploads/{user_provided_filename}"

# 正确 — 使用 path_utils
from app.utils.path_utils import ensure_within_root
safe_path = ensure_within_root(base_dir, user_provided_filename)
```

### 7.5 SQLite 与 MySQL 的差异

测试使用内存 SQLite；生产环境使用 MySQL。请注意以下差异：
- SQLite 默认字符串比较不区分大小写；MySQL 取决于排序规则。
- SQLite 不强制外键约束，除非设置 `PRAGMA foreign_keys = ON`（conftest fixture 已设置）。
- SQLite 的 `AUTOINCREMENT` 行为与 MySQL 的 `AUTO_INCREMENT` 略有不同。
- 某些 MySQL 特有的 SQL（如 `ON DUPLICATE KEY UPDATE`）在测试中会失败。
- MySQL 的 `JSON` 列类型映射到 SQLAlchemy 的 `JSON`，这在 SQLite 上可用但以文本形式存储。

如果需要在测试中测试 MySQL 特定行为，请在单元级别测试（例如，配置测试在不使用真实 MySQL 服务器的情况下实例化 `Settings()`）。

### 7.6 前端的认证令牌

```typescript
// 错误 — XSS 漏洞
localStorage.setItem('token', accessToken);

// 正确 — sessionStorage 在标签页关闭时清除
sessionStorage.setItem('token', accessToken);
```

`src/api/client.ts` 中的 Axios 客户端自动从 `sessionStorage` 读取。

### 7.7 测试中的 `assert False`

```python
# 错误 — ruff B011 拒绝此写法
if condition:
    assert False

# 正确
if condition:
    raise AssertionError("Expected condition X but got Y")
```

### 7.8 不要将业务逻辑写在路由处理器中

```python
# 错误 — 路由处理器包含业务逻辑
@router.post("/{file_id}/process")
def process_file(
    file_id: int,
    current_user: CurrentUser,               # 无默认值 — 必须在 Depends() 之前
    db: Session = Depends(get_db),
):
    file = db.scalar(select(File).where(File.id == file_id))
    if file.status != "available":
        raise AppHTTPException(...)
    # ... 更多内联逻辑 ...

# 正确 — 路由委托给 Service
@router.post("/{file_id}/process")
def process_file(
    file_id: int,
    current_user: CurrentUser,
    request: Request,
    db: Session = Depends(get_db),
):
    result = file_service.process_file(db, file_id, current_user.id)
    return ok(FileResponse.model_validate(result).model_dump(), request.state.request_id)
```

### 7.9 禁用的功能必须返回 503

Agent-run 端点（第二阶段）在 `AGENT_ENABLED=false` 时被禁用，必须返回 `503 Service Unavailable`，而不是 `500 Internal Server Error`。同样的规则适用于第三阶段流水线——它们已完整实现但默认关闭：当对应的 `*_PIPELINE_ENABLED` 开关为 false 时，`POST /api/v1/jobs` 返回 503（`DXF_PIPELINE_DISABLED` / `DXF2DWG_PIPELINE_DISABLED` / `DXF2EXCEL_PIPELINE_DISABLED`）。`test_stage1_boundaries.py` 测试验证了这一点。如果启用某项功能，请更新这些测试。

### 7.10 不要在前端硬编码 API URL

```typescript
// 错误
const resp = await axios.get('http://localhost:8000/api/v1/users');

// 正确
import client from '@/api/client';
const resp = await client.get('/api/v1/users');
```

客户端的 `baseURL` 来自 `VITE_API_BASE_URL`，本地开发时应设置为后端 URL（如 `http://127.0.0.1:8000`），在 Docker/nginx 部署中可能为空。

---

## 8. 数据库迁移工作流

### 8.1 创建迁移

```bash
cd backend
uv run alembic revision --autogenerate -m "add notifications table"
```

这会在 `backend/migrations/versions/` 中生成一个新文件，包含 `upgrade()` 和 `downgrade()` 函数。

### 8.2 审查迁移

提交前务必审查自动生成的迁移。常见需检查的问题：
- 外键上的 `ondelete="CASCADE"` 或 `ondelete="SET NULL"` — Alembic 可能无法正确推断这些。
- 枚举类型 — Alembic 可能生成 `sa.Enum()` 而未指定 `native_enum=False` 以兼容 SQLite。
- 默认值 — 字符串和布尔值通常没问题；服务端默认值可能需要手动 `server_default=text("...")`。
- 索引 — 自动生成的迁移可能遗漏你在模型上手动添加的索引。

### 8.3 应用迁移

```bash
# 应用所有待处理的迁移
cd backend
uv run alembic upgrade head

# 应用某个特定迁移
uv run alembic upgrade <revision_id>

# 回退一步（仅开发环境 — 生产环境无备份切勿执行）
uv run alembic downgrade -1

# 检查当前迁移状态
uv run alembic current
```

### 8.4 测试迁移

`test_migrations.py` 文件测试迁移可以应用、回退和重新应用（往返）且无错误。创建新迁移后务必运行：

```bash
cd backend
uv run pytest tests/test_migrations.py -v
```

### 8.5 模型注册

所有 ORM 模型必须在 Alembic `env.py` 文件的 `target_metadata` 链中被导入。当前 `env.py` 执行 `from app.db.base import Base`，如果模型在应用中的某处被导入，它们将通过 SQLAlchemy 的元数据注册表被发现。如果新模型未被 `--autogenerate` 发现，请检查它是否在 `app/models/__init__.py` 中被导入。

---

## 9. Lint 检查与质量门禁

### 9.1 后端：ruff

**配置**（在 `pyproject.toml` 中）：

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "W"]
ignore = ["B008", "E501", "UP037"]
```

规则分类说明：
- `E` — pycodestyle 错误（语法、缩进、空白符）
- `F` — Pyflakes（未使用的导入、未定义的名称、重复定义）
- `I` — isort（导入排序）
- `UP` — pyupgrade（现代 Python 语法：`X | None`、`list[]`、`from __future__ import annotations`）
- `B` — flake8-bugbear（常见 bug 模式：可变默认值、`assert False`、裸 except）
- `W` — pycodestyle 警告（尾随空白符、空行问题）

排除的规则：
- `B008` — 不要在参数默认值中执行函数调用（对 FastAPI 的 `Depends()` 模式过于嘈杂）
- `E501` — 行过长（由格式化器处理）
- `UP037` — 从类型注解中移除引号（与 `from __future__ import annotations` 冲突）

**运行：**
```bash
cd backend
uv run ruff check app tests          # 仅检查
uv run ruff check --fix app tests    # 自动修复安全问题
```

**提交前门禁：** 每次提交前运行 `uv run ruff check app tests`。CI 也应将其作为必检项运行。

### 9.2 前端：TypeScript + ESLint

前端使用 TypeScript 严格模式和 ESLint（如已配置）。至少：
- 所有 TypeScript 文件必须编译无错误：`npx tsc --noEmit`
- 构建必须成功：`npm run build`

### 9.3 提交前检查清单

提交任何更改前：

```bash
# 后端更改
cd backend
uv run ruff check app tests          # 必须 0 错误通过
uv run pytest -q                     # 必须 599 测试通过

# 前端更改
cd frontend
npx tsc --noEmit                     # 必须 0 错误通过
npm run build                        # 必须无错误生成 dist/

# Git 卫生
git diff --cached                    # 审查你的暂存更改
```

### 9.4 .gitignore 规则

以下内容必须被 gitignore（已配置）：
- `backend/var/` — 运行时数据（上传、临时文件、SQLite 数据库）
- `.env` 和 `.env.docker` — 包含密钥的实际环境文件
- `*.pyc`、`__pycache__/` — 编译后的 Python 文件
- `frontend/dist/` — 构建产物
- IDE 目录（`.vscode/`、`.idea/`）

以下内容必须被提交：
- `uv.lock` — 精确的后端依赖版本
- `package-lock.json` — 精确的前端依赖版本
- `.env.example` 和 `.env.docker.example` — 模板（无密钥）
- `alembic.ini` 和 `migrations/` — 数据库 Schema 历史

---

## 10. Git 工作流

### 10.1 提交信息

遵循约定式提交格式：

```
<type>(<scope>): <description>

[optional body]
```

类型：
- `feat` — 新功能
- `fix` — Bug 修复
- `refactor` — 代码重构（无行为变更）
- `test` — 添加或更新测试
- `docs` — 文档变更
- `chore` — 维护任务（依赖、配置、脚本）
- `style` — 格式化、空白符（无逻辑变更）

范围：`backend`、`frontend`、`infra`、`docs`、`scripts`、`tests`

示例：
```
feat(backend): add notifications resource with CRUD endpoints
fix(frontend): correct token refresh interceptor 401 loop
refactor(backend): extract notification logic from routes into service
test(backend): add security boundary tests for notifications API
docs: add development guide covering full workflow
chore(backend): update ruff to 0.7.0
```

### 10.2 分支策略

- `main` — 生产就绪代码。受保护。仅通过 PR 合并。
- `develop` — 活跃开发的集成分支。
- 功能分支：`feat/<description>`（如 `feat/add-notifications`）
- 修复分支：`fix/<description>`（如 `fix/token-refresh-loop`）
- 发布分支：`release/<version>`（如 `release/v0.2.0`）

### 10.3 不应提交的内容

- `.env` 和 `.env.docker` — 包含真实密钥
- `backend/var/` — 运行时数据
- `frontend/dist/` — 构建产物
- IDE 配置文件（`.vscode/`、`.idea/`）
- 操作系统文件（`.DS_Store`、`Thumbs.db`）
- 任何包含 API 密钥、密码或令牌的文件

### 10.4 Pull Request 检查清单

- [ ] `uv run ruff check app tests` 通过（0 错误）
- [ ] `uv run pytest -q` 通过（全部 599 测试）
- [ ] `npx tsc --noEmit` 通过（前端类型检查）
- [ ] 新端点有对应的测试（正向路径 + 安全边界）
- [ ] 状态变更操作写入了审计日志
- [ ] 新 Schema 的响应模型使用 `ConfigDict(from_attributes=True)`
- [ ] 新依赖通过 `uv add` / `npm install @version` 添加（非手动编辑）
- [ ] Alembic 迁移已包含（如果 Schema 有变更）
- [ ] 无硬编码的 URL、路径或密钥
- [ ] `uv.lock` 已更新（如果 `pyproject.toml` 有变更）
- [ ] `package-lock.json` 已更新（如果 `package.json` 有变更）

---

## 快速参考

### 开始开发

```bash
# 后端
cd backend
uv sync
cp .env.example .env   # 编辑为你本地的设置
uv run uvicorn app.main:app --reload --port 8000

# 前端
cd frontend
cp .env.example .env   # 如有需要，编辑 VITE_API_BASE_URL
npm install
npm run dev

# 或使用便捷脚本
cd /path/to/repo
./scripts/start-dev.sh
```

### 提交前运行检查

```bash
cd backend && uv run ruff check app tests && uv run pytest -q
cd frontend && npx tsc --noEmit && npm run build
```

### 入职时需要阅读的关键文件

1. `DWG-Agent企业平台技术规范.md` — 技术规范（第 5、6、7、21 节与开发者最相关）
2. `CLAUDE.md` — Agent 指令文件，包含所有约定和文件映射
3. `docs/architecture.md` — 系统架构
4. `docs/api.md` — API 参考
5. `docs/deployment.md` — 本地开发与部署设置
6. `docs/database.md` — 数据库 Schema
7. `docs/security.md` — 安全架构
8. `docs/roadmap.md` — 阶段交付路线图
9. `backend/app/core/config.py` — 所有配置项
10. `backend/app/core/exceptions.py` — AppHTTPException 定义
11. `backend/app/main.py` — FastAPI 应用的组装方式
12. `backend/app/api/v1/router.py` — 路由的挂载方式
