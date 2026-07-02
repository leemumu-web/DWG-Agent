# 第一阶段本机开发框架 Review

审查对象：`complete_framework_stage1_no_docker`。

审查结论：本包是“阶段一平台骨架闭环”的本机开发版本。它不使用 Docker，不接入 Agent 内部逻辑，不实现 DWG/DXF 解析，不实现中望 CAD Worker。当前目标是验证前后端分离、RESTful API、认证/RBAC、项目、文件、任务、结果、审计这条最小链路。

## 1. 本步明确实现范围

已实现并保留可运行代码的部分：

- FastAPI 后端工程初始化。
- Python 版本约束固定为 3.12：`backend/pyproject.toml` 为 `>=3.12,<3.13`，并新增 `backend/.python-version`。
- uv 依赖锁文件：`backend/uv.lock` 已提交，Python 约束同步为 `>=3.12, <3.13`。
- SQLite 本机开发数据库；MySQL 仅保留 `DATABASE_URL` 切换能力。
- Super Admin 初始化。
- 登录、当前用户、用户管理、角色、权限列表。
- 项目、项目成员。
- DWG 文件上传到本地存储，校验后缀、大小并计算 SHA-256/MD5。
- 文件短期下载 URL 和本机下载端点。
- 图纸、图纸版本。
- 任务创建、任务步骤、任务结果。
- 本机假任务：用 FastAPI BackgroundTasks 将任务从 `queued` 推进到 `succeeded`，并生成 JSON 结果文件。
- 结果详情、结果下载 URL、复核记录、待复核列表。
- 审计日志写入和查询。
- Agent API 边界：`AGENT_ENABLED=false` 时明确返回 503。
- React + TypeScript + Vite 前端骨架。
- Axios API client、TanStack Query、Zustand、Ant Design。
- 登录页、工作台、项目页、文件页、任务页、图纸/复核/用户/审计占位页。
- docs、agents、cad-worker、infra、scripts 目录占位。

## 2. 本步明确未实现范围

以下内容没有完成，本包也不声称完成：

- Docker Compose、Nginx、MySQL、Redis、MinIO 生产编排。
- Celery 真队列和 Worker 进程。
- MinIO 对象存储真实接入。
- Agent 内部 LangGraph、LLM、MCP、Redis memory。
- DWG 转 DXF。
- ezdxf 解析。
- 中望 CAD / Windows C# Worker。
- 项目级细粒度权限闭环。
- refresh token、HttpOnly Cookie、登录失败限流、禁用用户后 token 失效。
- 分片上传。
- SSE 任务事件流。
- 前端完整 CRUD 表单和完整权限控制。
- 前端自动化测试。
- Alembic 正式 migration 版本文件。

## 3. 本轮 Review 修正项

- 修正 Python 版本管理：由 `>=3.11,<3.14` 改为 `>=3.12,<3.13`。
- 新增 `backend/.python-version`，固定 uv 本机解释器选择为 3.12。
- 同步 `backend/uv.lock` 顶部 Python 约束为 `>=3.12, <3.13`。
- 修正前端依赖版本：移除 `latest`，将 `package.json` 依赖固定为 `package-lock.json` 中的解析版本。
- 更新 `package-lock.json` 根依赖声明，确保 `package.json` 与 lock 文件一致。
- 新增 `GET /api/v1/files/{file_id}/download`，补齐下载 URL 指向的真实本机下载端点。
- 新增文件上传、下载、审计、非 DWG 拒绝、Agent 禁用边界测试。
- 调整 Ruff 配置为阶段一可执行规则，并修复 import、未使用依赖、异常链等问题，使 `ruff check app tests` 通过。
- 更新 `docs/api.md`，补充实际下载端点。

## 4. 实际检查结果

后端检查：

```text
python -m compileall -q app tests     通过
ruff check app tests                  通过
pytest -q                             5 passed, 1 warning
```

前端检查：

```text
npm ci                                通过
npm run build                         通过
npm audit                             0 vulnerabilities
```

前端构建存在 Vite chunk size warning，原因是 Ant Design 等依赖被打入首包；这不是阶段一功能错误，后续做页面拆包时处理。

## 5. 依赖与版本一致性

后端：

- `pyproject.toml` 固定 Python 3.12 系列。
- `.python-version` 固定为 `3.12`。
- `uv.lock` 已提交。
- 当前审查环境只有 Python 3.13，且无法联网下载 uv-managed Python 3.12，因此本轮无法在容器中实际执行 `uv sync --locked` 的 3.12 运行时验证。
- 代码语法、单测、Ruff 在已创建的本地虚拟环境中通过；在真实开发机上需要先安装 Python 3.12 后执行 `uv sync --locked` 做最终解释器级验证。

前端：

- `package.json` 不再使用 `latest`。
- `package-lock.json` 与 `package.json` 根依赖版本一致。
- `npm ci` 和 `npm run build` 均通过。

## 6. 第一阶段验收对应关系

已达到的本机开发验收：

- 用户能登录。
- 管理员能创建账号。
- 用户能上传 DWG。
- 用户能创建任务。
- 任务能从 `queued` 到 `succeeded`。
- 结果文件能通过本地下载端点下载。
- 审计日志能查询。

未达到的生产阶段验收：

- Docker Compose 启动 nginx/backend/mysql/redis/minio。
- 文件上传到 MinIO。
- Celery 假任务。
- Alembic migration 正式执行链。

原因：当前用户明确要求初始阶段不使用 Docker，因此这些能力只保留接口、配置或目录边界。
