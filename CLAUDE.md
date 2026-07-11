# DWG-Agent 仓库协作说明

本文件约束代码协作代理。用户状态入口是 `README.md`，规范性设计是 `DWG-Agent企业平台技术规范.md`，详细文档只维护 `docs/` 下的中文版本。

## 基线事实

```text
Browser -> Nginx :8080 local / :80 Compose -> FastAPI :8010 local / :8000 internal
                                                    |-> MySQL
                                                    |-> MinIO or local storage
Celery workers <-> MySQL SQL transport/result backend -> tracked/external Stages
```

- MySQL 是业务数据、token 吊销、Agent memory、Job/steps/progress、broker 和 result 的权威来源。
- Redis/Valkey 不存在；禁止增加会改变正确性的 cache/fallback。
- 已实现队列为 `report`、`dxf`、`dxf2dwg`、`dxf2excel` 和 `excel_final`。
- `tasks_agent.py` 与 `tasks_cad.py` 是占位；保持 `AGENT_ENABLED=false`、`CAD_WORKER_ENABLED=false`。
- 四条转换 flag 默认 false；worker healthy 不代表管线可用。
- Compose 只发布 HTTP `${HTTP_PORT:-80}:8080`，不发布 443，也没有 TLS。
- `Stages/dxf2excel` 是缺少 `.gitmodules` 和可达对象的损坏 gitlink；已填充本机目录不是 clean-clone 证据。
- 生产配置关闭 OpenAPI/Swagger/ReDoc。
- 当前 Alembic head 为 `e4a1c7f2b930`；25 张模型表，Celery runtime 全部创建后最多 34 张表。
- 通用工作流是人工编排骨架，公开路径尚未自动创建 Job 或挂接产物。

## 工程规则

- 使用 Python 3.12 和 `backend/` 锁定的 `uv` 依赖；前端使用 `npm ci`。
- route 负责 HTTP；service 负责事务/不变量；Celery task 调用 service。
- worker 的领取、进度、终态、取消和补偿写入都匹配 status + attempt。
- storage adapter 管字节；MySQL 管元数据与 SHA-256；commit 前对象写入必须有 rollback compensation。
- 复用 file/Job/result/project 权限 helper；SQL 列表必须先过滤权限再分页。
- 禁止向客户端暴露 traceback、child stderr、DSN、secret、host path 或签名凭据。
- 禁止提交 `.env`、`.env.docker`、本地 storage、browser trace、log、virtualenv 或生成测试输出。
- `third_parts/` 是上游/外部所有权，不自动等同于平台交付能力。
- 没有直接证据时，禁止声称生产、TLS、不可变审计、自动备份、Agent、CAD worker 或 Stage 全兼容。

## 文档规则

- 先修改 route/test，再执行 `make docs-generate`。
- 只维护 `docs/*.md` 中文文档；禁止恢复旧双语目录或英文镜像。
- 本地 API 示例使用 `8010`；容器 `8000` 仅内部；本地 Nginx 为 `8080`；Compose 公共 HTTP 默认 `80`。
- 分别说明代码存在、默认 flag、外部依赖、验证层级/日期和剩余边界。
- 算法细节放在已跟踪 Stage 文档；平台集成写入 `docs/processing-pipelines.md`。
- 不修改 `third_parts/` 上游文档来制造平台能力声明。
- 完成前执行 `make docs-check`。

## 验证门禁

```bash
make docs-check

cd backend
uv run ruff check app tests ../tests/run_full_verify.py
uv run pytest -q
uv run alembic check
cd ..

cd Stages/dwg2dxf && uv run pytest -q && cd ../..
cd Stages/dxf2dwg && uv run pytest -q && cd ../..
cd Stages/excel_final && uv run pytest -q multi_split/tests && cd ../..
bash scripts/db.sh migration-test
bash infra/verify.sh
docker compose config --quiet

cd frontend
npm run build
npx playwright test
```

Stage 代码/文档变化时运行对应聚焦测试。完整工作流声明还需要真实 Nginx、MySQL、Celery、MinIO、有效输入、重试/SSE/下载和中断恢复证据。

## 关键路径

| 用途 | 路径 |
|---|---|
| FastAPI 应用 | `backend/app/main.py` |
| API router | `backend/app/api/v1/router.py` |
| 运行配置 | `backend/app/core/config.py` |
| DB engine/session | `backend/app/db/session.py` |
| Job 状态机 | `backend/app/services/job_service.py` |
| 工作流状态机 | `backend/app/services/workflow_service.py` |
| Celery 配置 | `backend/app/workers/celery_app.py` |
| 存储适配 | `backend/app/storage/` |
| 迁移 | `backend/migrations/versions/` |
| 前端 API | `frontend/src/api/` |
| Compose/Nginx | `compose.yaml`、`infra/nginx/` |
| 运维脚本 | `scripts/` |
| 文档治理 | `scripts/check_docs.py`、`scripts/generate_api_docs.py` |
