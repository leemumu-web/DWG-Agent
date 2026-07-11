# DWG-Agent 企业级 CAD 处理平台

DWG-Agent 是一套面向企业 CAD 文件处理、任务编排、结果复核和审计的全栈平台。当前代码已经打通 Nginx、React、FastAPI、MySQL、Celery、MinIO/本地存储以及 Excel Final 五金清单处理链路。

## 当前架构

```text
Browser
  -> Nginx :8080 (local) / :80,:443 (Compose)
  -> FastAPI :8010 (local) / :8000 (container)
  -> MySQL
       - 业务实体、RBAC、token 吊销、Agent memory
       - Job/JobStep/SSE 权威状态
       - Celery SQLAlchemy broker + result backend
  -> Celery workers
       - report, dxf, dxf2dwg, dxf2excel, excel_final
  -> Local FS (开发) 或 MinIO (Compose)
```

Redis/Valkey 已从运行时、依赖、Compose、脚本和数据路径中移除。需要一致性的请求直接访问 MySQL；SSE 轮询 MySQL；Celery broker/result URL 从有效 MySQL DSN 派生。

## 已实现能力

- React 19 + TypeScript + Vite + Ant Design 管理端。
- FastAPI + SQLAlchemy 2.x + Pydantic v2，同步 MySQL 会话。
- JWT access token、HttpOnly refresh cookie、MySQL token blacklist、密码变更失效和 RBAC。
- 项目、成员、文件、图纸版本、任务、结果、复核、审计和 Excel Final API。
- DWG -> DXF、DXF -> DWG、DXF -> Excel、Excel -> 零件清单 Celery 管线。
- `local`/`minio` 存储适配器、事务回滚对象补偿、短期签名下载、ZIP 下载。
- SQL 分页，精确总数与稳定排序；文件列表权限过滤在 SQL 中完成。
- 任务 `attempt` 世代：Celery 消息携带 attempt，重试递增 attempt，领取/更新均带条件，旧消息和旧 worker 不可处理新世代。
- `job_steps.attempt` 保留每次尝试历史；SSE 快照和前端时间线显示当前世代。
- 同一源文件存在多次成功转换时，文件/ZIP 解析确定性选择最新 Job 的最新可用结果。
- 结果详情、下载链接和复核沿用 Job 权限：无项目任务仅管理员或创建者可访问。
- Excel Final 以隔离子进程运行，支持 Tekla 制表符/空白文本导出和具有目标清单 schema 的 Excel 初始表；legacy `.xls` 由 `xlrd` 解析。对外错误不暴露 traceback 或主机路径。
- Compose 冷启动会导入 `hardware_handbook`，应用用户仅有该库 `SELECT` 权限。

Agent 内部推理和 CAD Windows worker 仍由功能开关禁用；其 API 边界存在，但禁用时返回 503。

## 端口约定

| 场景 | 前端 | API | 网关 | MySQL | MinIO |
|---|---:|---:|---:|---:|---:|
| 本地开发 | 5173（可回退 5174） | **8010** | 8080 | 3306 | 可选 |
| Docker Compose 内部 | Nginx 静态站点 | 8000 | 80/443 | 3306 | 9000 |

本地固定 API 端口是 `8010`。容器内 `8000` 是私有服务端口，不与本地约定冲突。

## 本地启动

```bash
cp .env.example .env
cp .env.example backend/.env
# 修改密码、JWT secret、MySQL 配置和所需 feature flags，并保持两个文件数据库字段一致。

bash scripts/db.sh setup-user
bash scripts/db.sh init
bash scripts/start-dev.sh
```

访问：

- 前端：`http://127.0.0.1:5173`
- FastAPI：`http://127.0.0.1:8010`
- Swagger：`http://127.0.0.1:8010/docs`
- Nginx 模式：`http://127.0.0.1:8080`

停止和诊断：

```bash
bash scripts/stop-all.sh
bash scripts/status.sh
bash scripts/db.sh status
```

worker 启停会按 app、队列和节点名发现已有进程；即使 `/tmp` pidfile 丢失，也不会重复启动同一受管 worker。

## Docker Compose

```bash
cp .env.docker.example .env.docker
# 替换全部 CHANGE_ME_* 值
npm --prefix frontend ci
npm --prefix frontend run build

docker compose up -d
docker compose --profile workers up -d
docker compose ps
```

Compose 默认启动 Nginx、FastAPI、MySQL、MinIO 和 `worker-report`；`workers` profile 启用其余队列。MinIO 镜像使用已验证 digest，不使用浮动 `latest`。worker healthcheck 同时验证 `worker_ready` marker 与 PID 1 命令行。

## 验证

```bash
cd backend
uv run ruff check app tests ../tests/run_full_verify.py
uv run pytest -q
uv run alembic check
uv run python ../scripts/check_docs.py

cd ../frontend
npm run build
npx playwright test

cd ..
cd Stages/excel_final && uv run pytest -q multi_split/tests && cd ../..
bash scripts/db.sh migration-test
bash infra/verify.sh
docker compose config --quiet
```

真实集成验收应至少覆盖：

1. Nginx -> FastAPI 登录和业务请求。
2. 空 MySQL schema 执行全部 Alembic 迁移。
3. Job 入 MySQL broker、Celery 消费、状态与步骤落库。
4. MinIO 上传、worker 读写、签名下载 SHA-256 一致。
5. MinIO 停机时 `/health/ready` 返回 503，恢复后持久对象仍可下载。
6. 浏览器下载首次 403 后重新获取签名并成功下载。

## 仓库结构

```text
backend/        FastAPI、ORM、服务、存储、Celery、Alembic、pytest
frontend/       React 管理端和 Playwright E2E
Stages/         CAD/Excel 独立处理阶段
infra/          Nginx、MySQL 初始化、部署验证
scripts/        启停、数据库和文档生成工具
docs/           英文文档
docs/zh/        与英文逐文件对应的中文文档
compose.yaml    生产形态编排
```

## 文档

- [企业平台技术规范](DWG-Agent企业平台技术规范.md)
- [英文文档索引](docs/README.md)
- [中文文档索引](docs/zh/README.md)
- [自动生成的 API 参考](docs/zh/api.md)
- [部署指南](docs/zh/deployment.md)
- [验证记录](docs/zh/workflow-verification.md)

端点变更后运行 `cd backend && uv run python ../scripts/generate_api_docs.py`，同一次生成 `docs/api.md` 和 `docs/zh/api.md`。
