# 开发

## 工具链

| 领域 | 工具链 | 锁定/安装 |
|---|---|---|
| Backend | Python 3.12、uv、FastAPI、SQLAlchemy 2、Pydantic 2 | `cd backend && uv sync --locked` |
| Frontend | Node/npm、React 19、TypeScript 6、Vite 8 | `cd frontend && npm ci` |
| Excel Final Stage | Python >=3.11、独立脚本 | `cd Stages/excel_final && uv sync --locked` |
| ODA Stages | Python >=3.12 加外部 AppImage runtime | 每个 Stage 执行 `uv sync --locked` |

backend lock 包含 `Stages/` 下 editable path dependency。clean environment 当前被损坏 `Stages/dxf2excel` gitlink 阻断；禁止把已填充工作树中的成功当成 clone 可复现性。

## 仓库地图

| 路径 | 归属 |
|---|---|
| `backend/app/api/` | HTTP dependency 与 routing |
| `backend/app/services/` | transaction、permission、orchestration |
| `backend/app/workers/` | Celery 配置和 task entrypoint |
| `backend/app/storage/` | local/MinIO byte adapter |
| `backend/migrations/` | Alembic 所有的业务 schema |
| `frontend/src/api/` | typed HTTP client、auth refresh、download |
| `frontend/src/features/` | workflow page |
| `Stages/` | 可独立运行的 domain processor |
| `infra/` | Nginx、MySQL 初始化、部署验证 |
| `scripts/` | 本地生命周期、DB 和文档工具 |
| `third_parts/` | 上游/vendored code；默认不是平台 module |

## 本地运行

```bash
# Vite :5173、FastAPI :8010、五个已实现队列 worker
bash scripts/start-dev.sh

# Built SPA 经 Nginx :8080 -> FastAPI :8010
bash scripts/start-all.sh
```

端口 `8010` 是容器内部端口（与本地一致）。Vite 选择其他端口时使用其输出 URL，直连测试时设置 Playwright override。接近生产的浏览器工作优先走 Nginx `8080`。

## Backend 变更规则

- Route 处理 HTTP schema/dependency；service 负责业务 transaction；task 调用 service。
- 沿用仓库现有 sync SQLAlchemy 模式。
- 每个 worker claim/progress/terminal 写入必须匹配 status 和 attempt。
- 文件字节只经过 storage adapter，metadata 存 MySQL。
- commit 前写入的存储对象必须加入 session compensation。
- 复用资源 permission helper；SQL 列表过滤不能退化成逐行 N+1 检查。
- 禁止把 traceback、DSN、child stderr、secret 或 host path 放进客户端可见错误。
- 禁止增加 Redis/Valkey 或内存正确性 fallback 掩盖依赖失败。

FastAPI lifespan seed initialization 在本地运行时是 best-effort。Docker 在 Gunicorn 前执行 migration/seed。测试必须按实际模式判断，不能假设进程启动就代表 ready。

## API 变更

使用标准成功/错误 envelope 和精确 SQL pagination。排序列表追加稳定 ID tie-breaker。路由变更要求：

1. schema/service/route 测试；
2. permission 与负例；
3. `make docs-generate`；
4. 行为或边界变化时更新对应中文文档；
5. `make docs-check`。

运行时 `/docs` 和 `/openapi.json` 只用于 development/debug。生成 Markdown API 参考才是生产可读清单。

## Frontend 变更

- Nginx 后使用相对 API request；只在 Vite 直连开发时使用 `VITE_API_BASE_URL`。
- Access 状态属于 `sessionStorage`；refresh/SSE 依赖 HttpOnly cookie。
- Axios 401 interceptor 执行一次共享 refresh，禁止递归重试 login/refresh。
- React Query retry 适用于 query；单文件 download 有独立的一次重试/新签名循环。
- UI guard 改善导航，但永远不替代 API authorization。
- Polling/SSE 必须在 terminal Job state 停止或稳定。
- 可见工作流变化与失败/重试行为增加 Playwright 覆盖。

## Worker 变更

队列为 `report`、`dxf`、`dxf2dwg`、`dxf2excel`、`excel_final`、`agent` 和 `cad`，但只有前五个有 task 实现。禁止把工作路由到占位 module。

MySQL SQL transport 缺少 fanout remote control。健康使用进程身份和 worker-ready marker。增加 task 时，应分别测试 routing、eager execution、真实 broker dispatch、attempt claim、failure mapping、stale execution、cancellation 和 object cleanup。

活动 session 有未提交 JobStep 时，禁止在 failure handler 打开第二个 session。除非 service 明确定义 compensating boundary，failure step 和 terminal Job state 应一起 commit。

## 数据库变更

```bash
cd backend
uv run alembic revision --autogenerate -m "description"
# 检查生成 operation 和循环 FK 行为。
cd ..
bash scripts/db.sh migration-test
cd backend && uv run alembic check
```

Alembic 当前拥有 28 张 SQLAlchemy 模型表，其中包含三张工作流表和三张流转/扫描表；8 张 Celery runtime-owned 表不纳入应用迁移生成。测试从空 MySQL upgrade；破坏性变更还需测试代表性已填充副本。`migration-test` 不验证 downgrade。

## 测试层级

```bash
# Backend 静态与隔离 API/service 测试
cd backend
uv run ruff check app tests ../tests/run_full_verify.py
uv run pytest -q

# 聚焦 Stage 测试
cd ../Stages/dwg2dxf && uv run pytest -q
cd ../dxf2dwg && uv run pytest -q
cd ../excel_final && uv run pytest -q multi_split/tests

# MySQL/infrastructure
cd ../..
bash scripts/db.sh migration-test
bash infra/verify.sh
docker compose config --quiet

# Frontend
cd frontend
npm run build
npx playwright test
```

SQLite 测试是快速逻辑检查，不证明 MySQL concurrency 或 migration。mocked Playwright route 验证 UI contract，不证明 MinIO/Celery。影响发布的 pipeline 变更还需要真实 Nginx/MySQL/worker/storage/sample 工作流。

## 调试顺序

1. 复现最小失败路径，并记录 request ID、Job ID、attempt、endpoint 和时间。
2. 检查 `/health/ready`、受管进程、flag 和 Stage 源码/依赖可用性。
3. 找第一处 backend/worker error，不只看最终 frontend symptom。
4. 检查权威 Job/JobStep row 和 storage object/digest。
5. 用聚焦回归验证假设，再修改行为。
6. 运行窄测试，再运行完整受影响层和端到端门禁。

## 文档与生成文件

`docs/api.md` 是生成文件；修改 generator，不手改文件。项目详细文档只维护 `docs/` 下的中文版本，不再创建旧双语目录或英文镜像。生成 frontend `dist`、Playwright trace、本地 storage、`.env*` secret、virtualenv、cache、log 和 test artifact 禁止提交。

组件特定算法属于其 Stage 文档。平台文档应链接并说明集成边界，而不是复制数百行算法步骤。
