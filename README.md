# DWG-Agent 企业级 CAD 智能处理平台

基于《DWG-Agent 企业平台技术规范》（`DWG-Agent企业平台技术规范.md`，2456 行，25 节）落地的工程实现。
后端 Python **3.12**，前端 React 19 + TypeScript，基础设施按三阶段推进。

> 开发指南详见 `CLAUDE.md`，基础设施配置详见 `infra/`

## 当前实现范围

已实现：

- FastAPI 后端工程骨架
- SQLAlchemy 2.x ORM 模型 + `TimestampMixin`
- MySQL 作为本机与 Docker 运行数据库；pytest 仅用进程内 SQLite test double 做隔离
- RESTful `/api/v1` 路由结构，全部资源名使用复数名词
- 登录、当前用户、用户管理、角色、权限（JWT + argon2id）
- 项目、项目成员
- DWG 文件上传到本地存储，后缀白名单 `.dwg`、大小上限 512MB、流式 SHA-256/MD5 哈希
- 文件短期 download-url 与本机下载端点
- 图纸、图纸版本
- 任务创建，本机假任务从 `queued` 自动推进到 `succeeded`，生成 JobStep 与 JSON 结果文件
- 任务步骤、任务结果、结果复核记录
- 审计日志写入与查询（super_admin / auditor 角色保护）
- Agent / DXF / ZWCAD 处理边界占位，Agent API 明确返回 503
- React 19 + TypeScript + Vite 前端骨架
- Axios API client、TanStack Query、Zustand、Ant Design 6
- 登录页、工作台、项目页、文件页、图纸页、任务页、复核页、用户管理、审计日志页
- 权限守卫组件（路由级）
- **Nginx 网关** — 本地开发（`nginx.local.conf`）+ Docker 部署（`nginx.conf`），SPA fallback / 反向代理 / 限流 / 安全头
- **Docker Compose** — 9 服务编排（`compose.yaml`），profiles 分阶段启动
- **MySQL 本机** — MariaDB/MariaDB-compatible MySQL 运行数据库；Docker 部署使用 `.env.docker` 的服务名配置

已修正（详见 `docs/stage1-review.md`）：

- Python 版本固定为 `>=3.12,<3.13`，新增 `backend/.python-version`
- 前端 `package.json` 不再使用 `latest`，所有依赖固定为 `package-lock.json` 中的确切版本
- 补齐 `GET /api/v1/files/{file_id}/download` 本机下载端点
- `ruff check .` 通过；pytest 181 passed, 0 failed
- 深度审计完成 — 待处理项已归档 `docs/stage1-audit.md`
- Compose: `cp .env.docker.example .env.docker && docker compose up -d`（`.env.docker` 使用 Docker 服务名 + MySQL/Redis/MinIO）

暂不实现：

- Agent 内部 LangGraph 调用（阶段二）
- DWG → DXF 转换与 ezdxf 解析（阶段三）
- Windows ZWCAD Worker 实际调用（阶段四）
- Redis / MinIO / Celery 实际运行（compose 定义就绪，worker 代码占位）

## 目录结构

```text
complete_framework/
├── README.md
├── CLAUDE.md                   # Agent 开发指令
├── DWG-Agent企业平台技术规范.md  # 核心规范文档
├── .env.example                # 本地开发环境变量模板
├── .env.docker.example         # Docker Compose 环境变量模板
├── Makefile
├── compose.yaml                # Docker Compose 9 服务编排
├── backend/
│   ├── pyproject.toml         # Python >=3.12,<3.13
│   ├── uv.lock                # 已提交，锁定全部依赖
│   ├── .python-version        # 3.12
│   ├── app/
│   │   ├── main.py
│   │   ├── api/v1/            # 12 个路由模块
│   │   ├── models/            # 10 个 ORM 模型
│   │   ├── schemas/           # Pydantic v2
│   │   ├── services/          # 7 个 service
│   │   ├── core/              # config / security / exceptions / constants / logger
│   │   ├── db/                # base / session / init_db
│   │   └── ...
│   ├── tests/                 # 181 测试（13 文件）
│   └── migrations/            # Alembic（stage 1 尚未生成迁移文件）
├── frontend/
│   ├── package.json           # 无 latest，版本已锁定
│   ├── package-lock.json      # 与 package.json 一致
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── .env.example           # VITE_API_BASE_URL
│   └── src/
│       ├── api/               # 9 个 API 客户端模块
│       ├── app/               # router / layout / providers
│       ├── features/          # 8 个页面模块
│       ├── components/        # 8 个通用组件
│       ├── stores/            # Zustand auth store
│       ├── types/             # TypeScript 类型定义
│       └── hooks/
├── agents/                    # Agent 定义占位
├── cad-worker/                # Windows CAD Worker 占位
├── infra/                     # 基础设施配置（Nginx / MySQL / compose）
│   ├── nginx/
│   │   ├── nginx.conf         #   Docker 版（单文件自包含）
│   │   └── nginx.local.conf   #   本机开发版
│   ├── mysql/init.sql         #   MySQL 初始化脚本
│   └── verify.sh              #   基础设施验证脚本（98 测试点）
├── docs/
│   ├── stage1-review.md
│   ├── api.md
│   └── local-dev.md           #   本机开发详细说明
└── scripts/                   # MySQL / 一键启停 / 状态检查 / 开发模式
```

## 本机启动

### 推荐脚本入口

```bash
cp .env.example .env
cp .env.example backend/.env
# 修改 .env 和 backend/.env 中所有 CHANGE_ME_* 值，并保持两者一致

bash scripts/db.sh start        # 启动 MySQL/MariaDB 并验证应用凭据
bash scripts/db.sh setup-user   # 首次部署/密码变更时创建或更新 dwg_user 授权
bash scripts/db.sh init         # 创建表 + 种子数据（Super Admin、角色、权限）
bash scripts/start-all.sh       # 后端 + 前端构建产物 + Nginx :8080
bash scripts/status.sh          # MySQL/Redis/FastAPI/Nginx 全栈状态
```

可选验证：

```bash
cd backend
uv run ruff check app tests     # 代码风格检查
uv run pytest -q                # 181 测试 (181 passed, 0 failed)
```

### 后端手动启动

```bash
cd backend
uv python install 3.12          # 如果本机尚未安装 Python 3.12
uv sync --locked                # 按 uv.lock 精确安装
../scripts/db.sh init           # 确保 MySQL schema 与种子数据就绪
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 前端

```bash
cd frontend
cp .env.example .env            # Nginx 反代（空=相对路径），直连时改为 http://127.0.0.1:8000
npm ci                          # 依赖版本已锁定在 package-lock.json，无 latest
npm run build                   # TypeScript 类型检查 + Vite 生产构建（可选但建议先跑一次）
npm run dev                     # 开发服务器 http://127.0.0.1:5173
```

> `npm run build` 目前存在 Vite chunk size warning（Ant Design 等依赖打入首包），不影响功能，后续做页面拆包时处理。

可选验证：

```bash
npm audit                       # 已知 0 vulnerabilities
```

### Nginx（可选，统一入口）

```bash
# 前置：后端已启动（127.0.0.1:8000），前端已构建（frontend/dist/）
sudo nginx -c $(pwd)/infra/nginx/nginx.local.conf
# 访问 http://localhost:8080 — SPA + API 反代 + 限流 + 安全头
```

详见 `infra/nginx/README.md`。

### Docker Compose（阶段 B）

```bash
# 首次启动前：复制 Docker 专用模板并修改所有 CHANGE_ME_* 值
cp .env.docker.example .env.docker

# 前置：前端已构建
docker compose up -d                              # 核心服务
docker compose --profile workers up -d            # + Celery Workers（阶段二）
docker compose --profile monitoring up -d         # + Flower 监控
# 访问 http://localhost
```

### MySQL 本机

```bash
# MariaDB/MySQL 已配置，数据库 dwg_agent，17 张表，7 角色种子
bash scripts/db.sh start
bash scripts/db.sh check
bash scripts/db.sh shell        # 使用应用凭据进入数据库
bash scripts/db.sh logs         # 查看 MySQL/MariaDB systemd 日志
```

### 基础设施验证

```bash
bash infra/verify.sh             # Nginx / Docker Compose / Dockerfile / MySQL / 环境模板检查
```

### 默认账号

```text
username: admin
password: SuperAdminPass1
```

### 端到端验证流程

```text
1. curl -X POST http://127.0.0.1:8000/api/v1/auth/sessions \
     -d '{"username":"admin","password":"admin123456"}' → 取 access_token
2. 带 Authorization: Bearer <token> 上传 .dwg 文件 → 取 file_id
3. POST /api/v1/projects → 取 project_id
4. POST /api/v1/jobs → 任务从 queued 自动到 succeeded
5. GET /api/v1/jobs/{id}/results → 取 result
6. GET /api/v1/audit-logs → 查看操作记录
```

## 开发原则

- 初始阶段不依赖 Docker，本机开发直接启动。
- 运行环境使用 MySQL；pytest 中的 SQLite 仅是显式测试替身，不能作为运行数据库结论。
- 本地文件系统只用于开发验证，生产替换为 MinIO/NAS（storage 抽象层已预留）。
- RESTful API 路径以 `/api/v1` 为准，资源名使用复数名词。
- Agent、DXF、CAD Worker 均先保留边界，不把算法逻辑塞进 API 层。
- 前端不硬编码 API 地址，通过 `VITE_API_BASE_URL` 环境变量注入。
- 依赖版本全部锁定：后端 `uv.lock`，前端 `package-lock.json`，禁止使用 `latest`。
