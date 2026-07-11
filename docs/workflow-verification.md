# 全栈工作流验证

> **范围：** Nginx、FastAPI、MySQL、Celery SQL transport、storage、frontend retry/SSE/download
> **最近文档审计运行：** 2026-07-11
## 1. 证据层级

| 层级 | 能证明 | 不能证明 |
|---|---|---|
| Static/docs | source/config/link/schema 声明内部一致 | 进程启动或依赖可用 |
| SQLite/backend tests | 隔离 API/service/security/state 逻辑 | MySQL lock、migration、broker、MinIO、browser 行为 |
| Stage tests | 确定性 converter/parser unit 和 parity corpus | 每个真实 CAD/workbook 或平台集成 |
| MySQL/infra checks | 空 schema migration 与活动本地 schema/config 事实 | 完整 Job/object/browser 工作流 |
| Playwright contract/UI | API 可达和浏览器交互；部分测试使用 route fixture | 每个场景都使用真实 Celery/MinIO/有效业务文件 |
| Live E2E | 本次运行实际覆盖的部署路径和样本 | 未来 revision 或未测格式/中断 |

验收声明必须写明层级、环境、日期、样本和跳过项。

## 2. 必要生产形态路径

```text
Browser -> Nginx HTTP :8080 本地 / :80 Compose
  -> FastAPI :8010 本地 / :8000 internal
     -> MySQL 业务 + Celery runtime state
     -> Local FS 或 MinIO object
Celery worker <- MySQL queue -> Stage -> MySQL state + storage result
```

没有 Redis/Valkey。当前 Compose 只有 HTTP；HTTPS 不属于该已验证路径。修复 gitlink 前，`Stages/dxf2excel` clean-clone 可复现性也在验收范围外。

## 3. 可重复门禁

```bash
make docs-check

cd backend
uv run ruff check app tests ../tests/run_full_verify.py ../scripts/check_docs.py ../scripts/generate_api_docs.py
uv run pytest -q
uv run alembic check
cd ..

cd Stages/dwg2dxf && uv run pytest -q
cd ../dxf2dwg && uv run pytest -q
cd ../excel_final && uv run pytest -q multi_split/tests
cd ../..

bash scripts/db.sh migration-test
bash infra/verify.sh
docker compose config --quiet

cd frontend
npm run build
npx playwright test
```

本地 stack 已运行时，通过 Nginx 使用只读 verifier：

```bash
DWG_VERIFY_USERNAME=admin \
DWG_VERIFY_PASSWORD='<configured-password>' \
python tests/run_full_verify.py
```

新增工作流和基础设施端点后，生成 OpenAPI 当前包含 77 个 path、95 个 operation。只读 verifier 之前记录的 71-path 运行属于历史证据，路由变化后尚未重跑。除此之外，verifier 检查 liveness、readiness、login、精确分页 files/Jobs read 和受管 process topology；它不创建处理 Job/工作流、不上传文件、不中断存储，也不验证签名 result digest。

## 4. 必要端到端场景

| 场景 | 必要证据 |
|---|---|
| Clean checkout/build | fresh clone 恢复全部 Stage source；锁定 backend/frontend install 和 image build 通过 |
| Cold Compose | 空 MySQL/MinIO volume 到达 migration head，core/selected worker healthy |
| Authentication | Nginx login、access request、cookie refresh、logout/revocation 和 expired session |
| Job dispatch | API 创建 queued attempt；MySQL broker 投递给预期 worker；JobStep 和 terminal state 持久化 |
| Object closure | source/result `files` row 与 stored object、download SHA-256 一致 |
| Retry isolation | failed/cancelled Job 递增 attempt；旧 message/worker 不能更新 |
| SSE | HttpOnly cookie 生效、当前 attempt snapshot 到达、reconnect 刷新、terminal 关闭 |
| Authorization | 拒绝跨项目和无项目 result/file/review 越权 |
| Download retry | 首次 signed fetch 以可重试状态失败；第二次获得不同且有效签名 |
| Storage outage | liveness 保持 200；readiness 503；恢复无需 API restart；旧对象保持完整 |
| Worker loss | stale running Job 变为 `CELERY_WORKER_LOST`，随后 retry 完成新 attempt |
| TLS | 真实 HTTPS handshake、redirect、Secure refresh/SSE cookie、signed download 和证书生命周期 |

TLS 和 clean-checkout 两行当前是已知失败，不是已完成验收项。

## 5. 最近运行证据

2026-07-11 文档审计运行使用已有本地 MySQL 和已经运行的本地 Nginx/FastAPI/五个已实现 worker；没有重启 stack 或重建 Compose volume。

| 门禁 | 结果 | 边界 |
|---|---|---|
| Documentation checker | pass | 中文单文档集、生成 API、link、table/head、HTTP/TLS、gitlink 和 production-doc contract |
| Backend Ruff | pass | application、test、verifier、documentation script |
| Backend pytest | **743 passed，3 skipped** | 15 个 dependency/deprecation warning；包含 69 条工作流测试；SQLite 隔离 |
| Alembic check | 无新 operation | 已知 `drawings`/`drawing_versions` cycle warning 仍存在 |
| MySQL migration test | pass | 空临时 schema -> `e4a1c7f2b930`；验证 25 张模型表并清理临时库 |
| Infrastructure verifier | **110/110 pass** | 活动 MySQL 为 34 张表；静态 Compose/Nginx/Dockerfile/env 契约；不含 TLS/build/restore E2E |
| Stage tests | **28 + 28 + 259 passed** | 分别为 dwg2dxf、dxf2dwg、Excel Final multi_split |
| 工作流框架测试 | **69 passed（包含于后端全量）** | service 状态机、HTTP 认证/访问/校验、生命周期、同步、重新计算 |
| Frontend build | pass | TypeScript 6 与 Vite 8 production bundle；尚无生产流程/基础设施页面 Playwright 用例 |
| Playwright | **68 passed** | 通过现有本地 Nginx/API/worker；含有效 Excel 样本、下载重签名、双向文件页和 Jobs UI；不覆盖新增 workflow API/UI |
| Live read-only verifier | 7 项历史通过 | 运行中旧进程仍为 71 path/88 operation；当前源码生成值为 77/95，新增 route 未经该进程验证 |

全量运行提供仓库已知有效 Tekla 清单，并通过成功 upload -> Celery -> result -> 首次下载失败 -> 新签名 digest 验证。另一个 `阚导出材料表.xls` 探针因缺少必要 `构件编号` 和 `数量` 列被正确拒绝；相关文件名/扩展名不足以证明输入有效。许多其他 Files/Jobs UI 测试使用确定性 route fixture，只证明 UI/API contract，不证明真实对象处理。

## 6. 历史集成记录

仓库此前记录了 2026-07-11 fresh-volume 集成运行，观察为：

- MySQL 从空 volume 迁移到 `a74c2e9f1d30` 并创建 queue-claim index。
- report Job 通过 API -> MySQL broker -> Celery -> MySQL state -> MinIO，下载 SHA-256 匹配。
- MinIO 中断使 readiness 返回 503 且 database 保持 `ok`；恢复后旧对象仍存在。
- 真实 attempt-2 probe 拒绝 legacy 单参数 message，只在 `(job_id, 2)` 投递后完成。

这些作为带日期历史证据保留。本轮已重跑空 MySQL 迁移、静态基础设施、后端全量和现有 Playwright，但没有重启正在运行的本地 FastAPI；实时 `/openapi.json` 仍是旧进程加载的 71 path/88 operation，不含 workflow 和 infrastructure 新 route。因而新增 route 的证据来自 TestClient/OpenAPI 生成与迁移测试，不是经 Nginx 的实时 E2E。通用工作流仍需要浏览器覆盖；自动 Job/产物接线完成后还需对应集成测试和实时取消/恢复证据。

## 7. 故障定位

1. 记录 revision、request ID、Job ID/attempt、时间、flag、sample digest 和准确 entry URL。
2. 不先重启，先检查 `bash scripts/status.sh`、`/health` 和 `/health/ready`。
3. 检查第一处 API/worker/storage/MySQL error，不只看 browser 最终消息。
4. 确认 `alembic current`、Job/JobStep state、queue worker identity 和 Stage source 可用性。
5. 比较 `files.sha256`、storage byte 和 downloaded byte。
6. 区分 browser fixture coverage、真实 backend call 和真实 worker/object result。
7. 增加聚焦 regression，再重跑全部受影响层和必要 E2E 场景。

禁止通过启用内存 fallback、关闭 authorization、接受任意 spreadsheet 内容，或把 skipped scenario 描述为 verified 来让门禁通过。
