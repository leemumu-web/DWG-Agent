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
| 6 | 资源级 RBAC / 项目成员校验缺失 | 项目、文件、图纸、任务、结果、复核接口补项目成员/角色校验与越权回归测试 |
| 7 | 文件上传与下载安全缺口 | 补空 DWG 拒绝、DWG 头校验回归、文件读下载归属校验 |
| 8 | 登录前端长期保存 token / 预填默认密码 | 前端改用 `sessionStorage` 会话级保存并移除默认密码预填 |
| 9 | 前端核心页面占位 | 补图纸、待复核、用户、角色权限、审计日志、个人中心基础列表/详情页 |
| 10 | Refresh token / 修改密码接口未实现 | 登录设置 HttpOnly refresh cookie，`/auth/tokens/refresh` 签发新 access token，`/auth/password` 支持旧密码校验后修改 |
| 11 | download-url 非真正短期 URL / 上传 MIME 未校验 | download-url 增加 HMAC `expires/signature`，上传增加 DWG MIME allowlist |
| 12 | Nginx `/admin/*` 拦截 SPA 管理路由 | 从敏感路径拦截中移除 `/admin`，保留 `wp-admin/phpMyAdmin/config` 等探测路径 |

---

## 待处理 — 需单独工作流

### P0 — 安全与权限

#### 1. 资源级 RBAC / 项目成员校验 (Critical)

**原始现状:** 项目、文件、任务、结果、复核接口只校验登录(`get_current_user`)，不校验项目成员身份或资源归属。任何登录用户可枚举/修改全局资源。

**2026-07-03 更新:** 已处理。`projects/files/drawings/jobs/results/reviews` 已接入项目成员/项目角色或文件归属校验，新增 `backend/tests/test_api_regressions.py` 覆盖跨项目读取、下载、复核、文件列表泄漏等回归。

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

**原始现状:**
- 上传只校验 `.dwg` 扩展名，不校验文件头/MIME
- `download-url` 返回永久直连，无短期签名/过期
- 下载端点无资源权限校验

**2026-07-03 更新:** 已补 DWG 头校验、0 字节 DWG 拒绝、下载端点资源权限校验。短期签名 URL / HMAC TTL 仍未实现，当前仍是登录态保护下的 300 秒语义 URL。

**涉及文件:** `backend/app/services/storage_service.py:17,50`, `backend/app/api/v1/files_api.py:52`

**修复方向:**
- 上传时校验 DWG 文件头 (前 6 字节: `AC1012`~`AC1032`)
- download-url 改为短期签名 URL (HMAC + TTL)
- 下载加资源权限校验

---

#### 3. Token 策略违反规范 (High)

**原始现状:**
- access token 存入 `localStorage` (XSS 风险)
- 登录表单预填 `admin/admin123456`
- refresh token 端点未实现 (返回 501)
- `JWT_REFRESH_TOKEN_EXPIRE_DAYS` 配置项缺失

**2026-07-03 更新:** 已改为前端 `sessionStorage` 会话级保存并移除默认密码预填；登录会设置 HttpOnly refresh cookie，`/auth/tokens/refresh` 已可签发新 access token。服务端 access token 黑名单仍未实现，因此登出后旧 access token 在过期前仍可用。

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
- pytest 实测 **181 passed, 0 failed**（2026-07-03，本轮新增自我禁用/自我移除角色/DWG 版本头/唯一约束竞态/脚本入口回归测试）
- API 测试已通过 `conftest.py` 使用每测试用例独立的内存 SQLite test double，不再污染运行数据库
- 运行数据库口径为 MySQL；测试替身不能作为部署数据库结论

**涉及文件:** `backend/tests/conftest.py`

**修复方向:**
- 对需要验证 MySQL 方言/事务/约束的测试，单独接入 MySQL test schema
- CI 中禁止误连生产/开发 MySQL，只允许专用 test schema

---

### P2 — 前端完善

#### 7. 前端权限与页面占位 (Medium)

**原始现状:**
- `PermissionGuard` 不判断具体权限
- 缺项目详情、图纸详情、任务详情、角色权限、个人中心等页面
- 上传组件无进度/重试
- 任务创建按钮固定为冒烟测试

**2026-07-03 更新:** 已补全局角色路由门禁，并将图纸、待复核、用户、角色权限、审计日志、个人中心从占位替换为基础可用页面。项目/图纸/任务详情页、上传进度/重试和真实任务创建表单仍待后续前端增强。

**涉及文件:** `frontend/src/components/PermissionGuard.tsx`, `frontend/src/app/router.tsx`

---

## 修复顺序建议

```
1. P0-1: 资源级 RBAC + 越权测试         ← 安全底线
2. P0-2: 文件安全 (DWG 头校验 + 签名 URL)
3. P0-3: Token 策略 (httpOnly / 去除默认密码)
4. P1-6: 测试隔离（当前 181 passed，避免环境依赖）
5. P1-4: Celery app + Stage 1 dummy task
6. P1-5: Alembic 初始 revision
7. P2-7: 前端详情页 + 权限守卫
```

---

## 当前状态快照（2026-07-02 终版）

```
ruff:         All checks passed
pytest:       181 passed, 0 failed
compose:      docker compose config → OK（env_file → .env.docker）
nginx:        nginx -t → OK（需手动启动 backend+nginx 后全链路 OK）
MySQL:        :3306 为运行数据库目标，backend/.env 与 .env.example 已统一为 mysql+pymysql
              pytest 使用内存 SQLite test double，仅用于单元/API 隔离
Redis:        :6379 运行，Valkey 9.1.0, backend health: ok
Docker 部署:  cp .env.docker.example .env.docker && docker compose up -d（尚未实测）
```
