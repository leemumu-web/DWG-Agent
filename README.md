# DWG-Agent 企业级 CAD 智能处理平台

基于《DWG-Agent 企业平台技术规范》（`DWG-Agent企业平台技术规范.md`，2,455 行，25 节，v1.0）落地的工程实现。

**当前阶段：Stage 1 平台骨架闭环** — 完整 RESTful API、RBAC 认证授权、项目管理、文件上传（DWG 校验）、任务生命周期、审计日志。

> 交接文档：`docs/` | 开发规范：`CLAUDE.md` | 部署配置：`infra/`

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | React 19 + TypeScript + Vite + Ant Design 6 + TanStack Query + Zustand |
| 后端 | Python 3.12 + FastAPI + SQLAlchemy 2.x（同步）+ Pydantic v2 |
| 数据库 | MySQL 8.x（运行）+ SQLite 内存（测试隔离） |
| 缓存/记忆 | Redis / Valkey 9.1 |
| 文件存储 | 本地 FS（开发）/ MinIO（Docker 生产，adapter 已启用） |
| 异步任务 | Celery（Stage 1 worker-report 假任务，Agent/DXF/CAD 队列后续接入） |
| Agent | LangGraph + MCP + OpenAI-compatible LLM（Stage 2，API 边界就绪） |
| 部署 | Docker Compose 9 服务编排 + Nginx 网关 |
| 包管理 | uv（Python）+ npm（前端），依赖全部锁定 |

## 当前实现

- **64 个 RESTful API 端点**，11 个路由模块，全部在 `/api/v1` 下，复数名词
- **完整 RBAC**：5 表 IAM 模型，7 个全局角色 + 4 个项目级角色，原子状态转换
- **认证**：JWT HS256 + jti 黑名单，Argon2id 密码哈希，HttpOnly refresh cookie，时序攻击防御
- **项目管理**：CRUD + 成员管理（project_owner/engineer/reviewer/viewer），级联激活检查
- **文件上传**：DWG 头校验（AC1012-AC1032），1024 字节最小 + 512MB 最大，流式 SHA-256/MD5，HMAC 签名下载 URL
- **图纸版本管理**：版本递增 + 当前版本指针，预览占位
- **任务生命周期**：queued → running → succeeded/failed/cancelled，状态守卫，取消/重试
- **结果复核**：approved/rejected 决策，待复核列表
- **审计日志**：33 种操作类型，super_admin/auditor 角色保护，不可变
- **安全加固**：12/18 渗透测试 bug 已修复，CORS 收紧，异常不泄漏 traceback，路径穿越防护
- **Alembic 迁移**：2 个版本（17 张表 initial + TimestampMixin 修复），`db.sh migration-test` CI 验证
- **Redis 已部署**：Valkey 9.1，redis_client + redis_memory + cache_service 就绪，双轨测试（FakeRedis + 真实）
- **Docker Compose**：9 服务编排，worker-report 默认启动，Agent/DXF 与 monitoring profiles，Dockerfile 多阶段构建
- **前端**：10 个页面 + 11 个 API 客户端模块 + 8 个通用组件，路由级权限守卫
- **307 测试**（20 个测试文件），ruff 0 错误

暂不实现（Stage 2-4）：Agent 内部逻辑、DWG→DXF 转换、ezdxf 解析、Windows ZWCAD Worker。Celery report 假任务与 MinIO 存储后端已接入；本地开发默认 `STORAGE_BACKEND=local`，Docker 默认 `STORAGE_BACKEND=minio`。

## 快速启动

```bash
# 1. 环境准备
sudo pacman -S redis mysql    # Arch Linux；其他发行版对应包名
sudo systemctl enable --now redis mysqld

# 2. 配置
cp .env.example .env
# 编辑 .env，修改所有 CHANGE_ME_* 值

# 3. 数据库
bash scripts/db.sh start         # 启动 MySQL
bash scripts/db.sh setup-user    # 创建数据库用户（首次）
bash scripts/db.sh init          # 创建库 + Alembic 迁移 + 种子数据

# 4. 启动
bash scripts/start-dev.sh        # 后端 :8000 + 前端 :5173 HMR
```

访问 `http://127.0.0.1:5173`，默认账号 `admin` / `SuperAdminPass1`。

详细说明见 `docs/deployment.md`。

## 目录结构

```
complete_framework/
├── README.md
├── CLAUDE.md                        # Agent 开发指令（代码约定、仓库地图）
├── DWG-Agent企业平台技术规范.md       # 核心规范文档（v1.0，25 节）
├── .env.example                     # 本地开发环境变量模板
├── .env.docker.example              # Docker Compose 环境变量模板
├── compose.yaml                     # Docker Compose 9 服务编排
├── Makefile                         # 常用命令快捷入口
├── backend/
│   ├── pyproject.toml               # Python >=3.12,<3.13
│   ├── uv.lock                      # 锁定依赖
│   ├── .python-version              # 3.12
│   ├── Dockerfile                   # 多阶段构建（非 root）
│   ├── app/
│   │   ├── main.py                  # FastAPI 入口 + 异常处理器 + CORS
│   │   ├── api/v1/                  # 11 路由模块，64 端点
│   │   ├── core/                    # config, security, permissions, redis, exceptions
│   │   ├── db/                      # session (连接池), init_db (种子)
│   │   ├── models/                  # 10 个 SQLAlchemy ORM 模型（17 表）
│   │   ├── schemas/                 # 10 个 Pydantic v2 模块
│   │   ├── services/                # 7 个 service（auth, user, job, storage, audit, redis_memory, cache）
│   │   ├── storage/                 # AbstractStorageBackend + local/minio 后端
│   │   ├── utils/                   # path_utils（路径穿越防护）, file_hash, time_utils
│   │   ├── agents/                  # Stage 2 占位
│   │   ├── mcp_client/              # Stage 2 占位
│   │   ├── workers/                 # Celery app + report stub task；Agent/DXF/CAD task 占位
│   │   ├── integrations/zwcad/      # Stage 4 占位
│   │   └── repositories/            # 占位
│   ├── tests/                       # 307 测试（20 个 test_*.py）
│   └── migrations/                  # Alembic（2 版本）
├── frontend/
│   ├── package.json                 # 版本锁定，无 latest
│   └── src/
│       ├── api/                     # 11 个 API 客户端模块
│       ├── features/                # 10 个页面模块
│       ├── components/              # 8 个通用组件
│       ├── stores/                  # Zustand
│       └── types/                   # TypeScript 类型
├── docs/                            # 7 个交接文档
│   ├── architecture.md              # 系统架构 + 实现状态矩阵
│   ├── api.md                       # 64 端点完整参考
│   ├── database.md                  # 数据库设计 + 表目录
│   ├── deployment.md                # 部署运维指南
│   ├── development.md               # 开发规范 + 教程
│   ├── security.md                  # 安全架构 + 渗透修复
│   └── roadmap.md                   # Stage 1-6 路线图
├── infra/                           # 部署配置
│   ├── nginx/                       # nginx.conf (Docker) + nginx.local.conf (本机)
│   ├── mysql/init.sql               # 数据库初始化
│   ├── redis/redis.conf             # Docker Redis 配置
│   ├── minio/                       # MinIO 配置占位
│   └── verify.sh                    # 基础设施验证
├── scripts/                         # 6 个 dev/ops 脚本
│   ├── db.sh                        # MySQL 管理（init/migrate/check/shell/logs）
│   ├── start-dev.sh                 # 开发模式启动
│   ├── start-all.sh                 # 全栈启动
│   ├── stop-all.sh                  # 优雅停止
│   ├── status.sh                    # 健康检查
│   └── lib.sh                       # 共享函数
├── agents/                          # Agent 定义占位（Stage 2）
└── cad-worker/                      # Windows CAD Worker 占位（Stage 4）
```

## 开发原则

- **规范优先**：所有设计决策以 `DWG-Agent企业平台技术规范.md` 为准。
- **运行数据库**：MySQL 8.x；pytest 使用内存 SQLite 隔离，不作为部署结论。
- **同步 API + 异步任务**：SQLAlchemy 2.x 同步 session + Redis 同步客户端；耗时任务通过 Celery worker 执行。
- **API 约定**：RESTful `/api/v1`，复数名词，语义化 HTTP 状态码，统一响应格式。
- **安全底线**：所有业务端点强制鉴权，RBAC 后端强校验，文件路径校验，异常不泄漏。
- **依赖锁定**：`uv.lock` + `package-lock.json` 全部锁定，禁止 `latest`。
- **代码质量**：ruff（E/F/I/UP/B），line-length=100，`from __future__ import annotations`。
- **前端不硬编码**：API 地址通过 `VITE_API_BASE_URL` 环境变量注入。

## 验证

```bash
cd backend
uv run ruff check app tests     # 代码风格（0 errors）
uv run pytest -q                # 307 测试（307 passed）

cd ../frontend
npm ci && npm run build         # 类型检查 + 生产构建
npm audit                       # 0 vulnerabilities

bash infra/verify.sh            # 基础设施验证（Nginx/Docker/MySQL/配置）
```
