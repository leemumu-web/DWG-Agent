# 开发

> 英文对应文档：[../development.md](../development.md)

## 前置依赖与初始化

- Python 3.12 和 `uv`
- 与 `frontend/package-lock.json` 匹配的 Node.js/npm
- MySQL 8.x 或兼容 MariaDB
- 网关验证使用 Nginx，Compose/MinIO 验收使用 Docker

```bash
cp .env.example .env
cp .env.example backend/.env
bash scripts/db.sh setup-user
bash scripts/db.sh init
cd backend && uv sync --frozen
cd ../frontend && npm ci
```

运行时 `.env` 必须使用 MySQL。测试显式设置 `DATABASE_URL=sqlite://`，每个测试使用内存 `StaticPool` 隔离。

## 仓库地图

```text
backend/app/api/v1/       FastAPI 路由和 dependency 边界
backend/app/services/     业务状态转换
backend/app/models/       SQLAlchemy model
backend/app/schemas/      Pydantic 请求/响应
backend/app/storage/      Local/MinIO adapter
backend/app/workers/      Celery app 和 task wrapper
backend/migrations/       Alembic 历史
frontend/src/api/         Axios client 和分页 helper
frontend/src/features/    页面工作流
frontend/tests/e2e/       Playwright 浏览器/API 测试
Stages/                    CAD/Excel 处理工程
infra/                     Nginx/MySQL/部署验证
scripts/                   本地运维和文档生成
```

## 运行

```bash
bash scripts/start-dev.sh
# 前端 :5173，API :8010

bash scripts/start-all.sh --rebuild
# Nginx :8080 -> API :8010
```

本地脚本不要使用 8000；它是容器内部 API 端口。若 Vite 使用 5174，为 Playwright 设置相应 `PLAYWRIGHT_FRONTEND_BASE_URL`。

## 后端工作流

1. Route 校验输入并调用权限 helper。
2. Service 负责状态转换和事务语义。
3. commit 后再投递 Celery。
4. Worker 原子领取 `queued + attempt`。
5. 每次 worker 更新携带捕获的 attempt。
6. 对象写入登记 rollback compensation。
7. 公共错误稳定且脱敏。

worker 失败处理不得在当前 session 有未提交 step 时再开第二 session。失败 step 和 job 终态属于同一事务。

## API 与分页

SQL 列表使用 `paginate_scalars()`，稳定排序追加 ID。不得加载全部行再在 Python 切片。权限过滤属于 SQL，尤其是 files 和 jobs。

路由变更后：

```bash
cd backend && uv run python ../scripts/generate_api_docs.py
cd .. && make docs-check
git diff -- docs/api.md docs/zh/api.md
```

## 前端工作流

- 使用 `apiClient`，不要重复认证/refresh fetch 逻辑。
- 非幂等上传不做网络层自动重试。
- 文件下载每次重试重新获取签名 URL。
- 仅在确需全部数据时使用 `fetchAllPages()`。
- access 状态放在 `sessionStorage`。
- 纯图标按钮提供 `aria-label` 和 tooltip。
- 浏览器测试从 `.ant-table-tbody` 选择行 checkbox，不能误选表头全选。

## Celery 开发

队列为 `report/dxf/dxf2dwg/dxf2excel/excel_final/agent/cad`。MySQL SQL transport 不支持 remote-control fanout，健康检查不要使用 `celery inspect`。

worker 启动先创建 Kombu 表、关闭 bootstrap channel、添加队列顺序索引，再启动 consumer。ready marker 只能由 `worker_ready` 创建。

## 测试

```bash
cd backend
uv run ruff check app tests
uv run pytest -q

cd ../frontend
npm run build
PLAYWRIGHT_FRONTEND_BASE_URL=http://127.0.0.1:5173 \
PLAYWRIGHT_API_BASE_URL=http://127.0.0.1:8010 \
npx playwright test  # 默认通过 Nginx http://127.0.0.1:8080
```

真实 Excel Final 流程：

样本必须是 Tekla 制表符/空白文本导出，或包含钢构清单必需列的 Excel 工作簿；普通 `.xls`/`.xlsx` 文件属于预期失败用例。

```bash
PLAYWRIGHT_EXCEL_SAMPLE_PATH=/absolute/path/to/sample.xls \
PLAYWRIGHT_FRONTEND_BASE_URL=http://127.0.0.1:5173 \
PLAYWRIGHT_API_BASE_URL=http://127.0.0.1:8010 \
npx playwright test tests/e2e/excel-final-flow.spec.ts
```

## TDD 与调试

复现、捕获失败边界、先加回归并确认正确失败、实现最小修复、再运行相关和全量套件。多组件故障必须在 Nginx、API、DB、broker、worker、storage 和 browser 边界收集证据。

## 数据库变更

```bash
bash scripts/db.sh revision "message"
bash scripts/db.sh migrate
bash scripts/db.sh migration-test
cd backend && uv run alembic check
```

Alembic autogenerate 不管理 Celery-owned 表及其 sequence 表。Kombu 必要索引由运行时维护。`alembic check` 必须报告没有新 upgrade operation；迁移新增的 ORM 索引也必须存在于模型 metadata。

## 生成与临时文件

不要提交 `.playwright-cli`、`frontend/test-results`、`frontend/dist`、backend storage、本地 `.env` 或临时 output。持久测试和文档放在跟踪目录。
