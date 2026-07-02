# Stage 1 深度审计 — 待处理项

> 审计日期: 2026-07-02
> 范围: 全栈 / 配置 / 安全 / 测试
> 修复策略: infra 层面问题本次修复；业务逻辑/安全/测试需单独工作流

---

## 本次已修复（infra 层面）

| # | 问题 | 修复 |
|---|------|------|
| 1 | `docker compose config` 失败 — Docker 配置依赖根 `.env`/shell 插值 | 新增 `.env.docker.example` 模板，真实 `.env.docker` 已 gitignore，Compose 服务统一使用 `env_file: .env.docker` |
| 2 | Ruff I001 import 排序 | `ruff check --fix` → All checks passed |
| 3 | `start-all.sh` 使用 `fuser -k` 杀进程 | 改为报错退出，提示用户手动处理 |
| 4 | 运行时 `var/` 文件被纳入版本控制 | `.gitignore` 增加 `var/`，并从索引移除本地 SQLite/上传产物 |
| 5 | MySQL/Redis/MinIO 继承整份 Docker 应用密钥 | 保留 `env_file: .env.docker` 防止根 `.env`/shell 插值污染，同时在中间件服务中清空 JWT、super admin、跨中间件密码等无关敏感变量 |

---

## 待处理 — 需单独工作流

### P0 — 安全与权限

#### 1. 资源级 RBAC / 项目成员校验 (Critical)

**现状:** 项目、文件、任务、结果、复核接口只校验登录(`get_current_user`)，不校验项目成员身份或资源归属。任何登录用户可枚举/修改全局资源。

**涉及文件:**
- `backend/app/api/v1/projects_api.py` — list/get/update/delete 无成员过滤
- `backend/app/api/v1/files_api.py` — list/download 无归属校验
- `backend/app/api/v1/jobs_api.py` — 任务无项目隔离
- `backend/app/api/v1/results_api.py` — 结果无权限过滤
- `backend/app/api/v1/reviews_api.py` — 复核提交无角色校验

**修复方向:**
- 添加 `require_project_member(role)` 依赖注入
- 列表接口按 `project_id` + 成员身份过滤
- 越权测试覆盖

---

#### 2. 文件上传安全不达标 (High)

**现状:**
- 上传只校验 `.dwg` 扩展名，不校验文件头/MIME
- `download-url` 返回永久直连，无短期签名/过期
- 下载端点无资源权限校验

**涉及文件:** `backend/app/services/storage_service.py:17,50`, `backend/app/api/v1/files_api.py:52`

**修复方向:**
- 上传时校验 DWG 文件头 (前 6 字节: `AC1012`~`AC1032`)
- download-url 改为短期签名 URL (HMAC + TTL)
- 下载加资源权限校验

---

#### 3. Token 策略违反规范 (High)

**现状:**
- access token 存入 `localStorage` (XSS 风险)
- 登录表单预填 `admin/admin123456`
- refresh token 端点未实现 (返回 501)
- `JWT_REFRESH_TOKEN_EXPIRE_DAYS` 配置项缺失

**涉及文件:** `frontend/src/stores/auth.store.ts:11`, `frontend/src/features/auth/LoginPage.tsx:27`, `backend/app/api/v1/auth_api.py:42`

**修复方向:**
- Token 改用 httpOnly cookie 或至少 sessionStorage
- 移除默认密码预填
- 实现 refresh token 端点

---

### P1 — 平台核心能力

#### 4. Celery / Worker / Agent 不可运行 (Critical)

**现状:**
- `celery_app.py` 只有占位注释，无 Celery app 实例
- `langgraph`, `minio`, `ezdxf` 依赖缺失
- Compose worker 命令用 `uv run celery`，但 Dockerfile runtime 阶段无 `uv`
- `import celery` / `import langgraph` 均失败

**涉及文件:** `backend/app/workers/celery_app.py`, `backend/pyproject.toml`, `compose.yaml:56`, `backend/Dockerfile`

**修复方向 (阶段二):**
- 实现 `celery_app.py` (Celery app + config)
- 添加 `celery`, `langgraph`, `langchain-openai` 到 pyproject.toml
- Dockerfile 改用单阶段 (保留 uv) 或 CMD 用 venv 中的 celery 二进制
- Compose worker 命令改为 venv 路径

---

#### 5. Alembic 只是壳 (High)

**现状:** 无实际 revision 文件，`init_db()` 用 `Base.metadata.create_all()` 建表。

**涉及文件:** `backend/app/db/init_db.py:42`, `backend/migrations/env.py`

**修复方向:**
- 生成初始 revision: `alembic revision --autogenerate -m "initial"`
- `init_db()` 改为先 `create_all`(开发) + `alembic upgrade head`(生产)

---

#### 6. 测试不隔离 (Medium)

**现状:**
- pytest 实测 **153 passed, 0 failed**（2026-07-02 终版，之前 2 个失败因 `backend/.env` 中 MySQL 变量与测试默认值冲突，现已通过）
- 测试共用当前 SQLite DB + 本地 storage，无临时 fixture，仍有污染风险
- 测试依赖当前 `backend/.env` 的隐式值，跨环境不稳定

**涉及文件:** `backend/tests/conftest.py`

**修复方向:**
- 添加临时 DB fixture (`tmp_path` + SQLite)，禁止测试污染 `var/`
- CI 中禁用真实 Redis/MySQL

---

### P2 — 前端完善

#### 7. 前端权限与页面占位 (Medium)

**现状:**
- `PermissionGuard` 不判断具体权限
- 缺项目详情、图纸详情、任务详情、角色权限、个人中心等页面
- 上传组件无进度/重试
- 任务创建按钮固定为冒烟测试

**涉及文件:** `frontend/src/components/PermissionGuard.tsx`, `frontend/src/app/router.tsx`

---

## 修复顺序建议

```
1. P0-1: 资源级 RBAC + 越权测试         ← 安全底线
2. P0-2: 文件安全 (DWG 头校验 + 签名 URL)
3. P0-3: Token 策略 (httpOnly / 去除默认密码)
4. P1-6: 测试隔离（当前 153 passed，避免环境依赖）
5. P1-4: Celery app + Stage 1 dummy task
6. P1-5: Alembic 初始 revision
7. P2-7: 前端详情页 + 权限守卫
```

---

## 当前状态快照（2026-07-02 终版）

```
ruff:         All checks passed
pytest:       153 passed, 0 failed
compose:      docker compose config → OK（env_file → .env.docker）
nginx:        nginx -t → OK（需手动启动 backend+nginx 后全链路 OK）
MySQL:        :3306 运行，dwg_agent 17 表已建，admin 记录存在
              注意: 当前 backend 使用 SQLite（本地开发），非 MySQL
Redis:        :6379 运行，Valkey 9.1.0, backend health: ok
Docker 部署:  cp .env.docker.example .env.docker && docker compose up -d（尚未实测）
```
