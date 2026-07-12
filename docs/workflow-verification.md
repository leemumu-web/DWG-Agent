# 全栈工作流验证

> **范围：** Nginx、FastAPI、MySQL、Celery SQL transport、storage、frontend retry/SSE/download
> **最近文档审计运行：** 2026-07-12
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
  -> FastAPI :8010 本地 / :8010 internal
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

生成 OpenAPI 当前包含 88 个 path、107 个 operation。只读 verifier 检查 liveness、readiness、login、精确分页 files/Jobs read 和受管 process topology；它不创建处理 Job/工作流、不上传文件、不中断存储，也不验证签名 result digest。

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

## 5. 2026-07-12 数据控制台与存储事务证据

本轮首先在本地 MySQL/local storage 上执行只读全量扫描，再用独立 Compose project、独立空 MySQL/MinIO 卷和新后端镜像执行可变更 E2E；没有对本地真实异常执行清理。

| 门禁 | 结果 | 实际覆盖 |
|---|---|---|
| Backend 全量回归 | **841 passed，5 skipped** | SQLite 隔离 API/service/security/state；15 条 dependency/deprecation warning，无失败 |
| Backend Ruff | pass | `app` 与 `tests`；新增账本、扫描、处置、分页和 Celery 冷启动代码 |
| 聚焦 backend 回归 | **190 passed，2 skipped** | 流转模型/service、Local/MinIO adapter contract、上传/生成/ZIP/下载、扫描/四类处置、data-admin API、迁移、连接池和前端 source contract |
| Documentation checker | pass | 中文文档集、生成 API、链接、端口、数据库表数/head、仓库边界和生产文档行为 |
| Alembic 模型漂移 | pass | `alembic check` 无新增 upgrade operation；保留已知 drawings/version 环依赖 warning |
| 空库迁移脚本 | pass | 临时 MySQL schema 从零升级至 `6d2f8a9c1b40`，验证 28 张业务表、种子数据并清理临时库 |
| Infrastructure / Compose 静态门禁 | **110/110 pass** | Nginx、11 个 Compose service、Dockerfile、活动 MySQL 37 张完整运行时表、环境与文件契约；`docker compose config --quiet` 通过 |
| Frontend build | pass | TypeScript 6 + Vite 8；数据控制台、任务、审计和转换页服务端分页 |
| Playwright 全量 | **69 passed，1 skipped** | 真实本地 API：认证/API contract、上传、失败重试、流式 ZIP、签名下载、双向转换页、Jobs 和数据控制台；指定真实 XLS 样本不存在的成功链路按设计跳过 |
| Playwright 数据控制台 | **2 passed** | 最终源码与真实本地 API：五页签、文件/对象/中文流水详情、中文状态筛选、open finding、处置空选择提示、无 console error；Jobs/Audit 首次只请求 `page=1&page_size=20` |
| 本地只读扫描 | pass | scan #1：548 条登记、12,778 个对象、2 个缺失、12,232 个未登记、0 个大小不符、79 个软删除保留；未执行真实处置 |
| 空卷 Compose | pass | MySQL/MinIO/API/report worker healthy；迁移到 `6d2f8a9c1b40`，首次 Kombu 表/索引无 metadata-lock 自锁 |
| MySQL + MinIO 可变更 E2E | pass | 3 次真实上传入库；1 次签名下载出库且 SHA-256 一致；扫描得到 1 missing、2 untracked、1 retained；四种处置全部执行并将 4 个 finding 标记 resolved |

空卷首次验证先暴露了旧实现的真实缺陷：Kombu `queue_declare` 后 session 未提交，随后 `CREATE INDEX ix_kombu_message_queue_timestamp_id_visible` 等待同一 worker 连接持有的 metadata lock，worker 永远没有 ready marker。修复后显式 commit/close channel session，再执行索引；从全新卷重建后 report worker 正常 healthy 并消费扫描任务。

全量浏览器复验又发现 Excel 便捷上传复用了认证依赖已经开启的 MySQL `REPEATABLE READ` Session，却在独立事务中新建/推进流水，再回旧快照执行 `SELECT ... FOR UPDATE`，稳定触发 MySQL 1020。现在 Excel `/upload` 与 `/upload-and-process` 先在当前事务登记意图并提交，再写对象和业务元数据；失败仍由补偿监听器删除对象并结算流水。ZIP 浏览器用例同时移除了“列表第一条一定已经拥有双格式结果”的历史数据假设，改为明确请求必然存在的源格式；后端仍严格拒绝不完整 ZIP，没有放宽一致性规则。

前端最终截图位于 `output/playwright/data-console-final.png`。截图来自最后一次通过的浏览器回归，状态、动作和风险提示均已中文化，处置动作在未选中同类 finding 时保持禁用并明确提示。隔离 Compose 前端镜像构建曾因 Docker Hub `node:22-alpine` IPv6 metadata 请求超时失败；后端新镜像正常构建，真实 API/MySQL/MinIO/Celery 验证不依赖该失败步骤，源码前端由本地 `npm run build` 和 Vite + Playwright 验证。

## 6. 2026-07-11 基线证据

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

## 7. 历史集成记录

仓库此前记录了 2026-07-11 fresh-volume 集成运行，观察为：

- MySQL 从空 volume 迁移到 `a74c2e9f1d30` 并创建 queue-claim index。
- report Job 通过 API -> MySQL broker -> Celery -> MySQL state -> MinIO，下载 SHA-256 匹配。
- MinIO 中断使 readiness 返回 503 且 database 保持 `ok`；恢复后旧对象仍存在。
- 真实 attempt-2 probe 拒绝 legacy 单参数 message，只在 `(job_id, 2)` 投递后完成。

以上四项仅作为 2026-07-11 的带日期历史证据保留。当时没有重启正在运行的本地 FastAPI，实时 `/openapi.json` 仍是旧进程加载的 71 path/88 operation，因此当时新增 route 只由 TestClient/OpenAPI 生成与迁移测试证明。2026-07-12 的当前证据已经由本节第 5 节取代：当前源码为 88 path/107 operation，并已用重启后的本地 API、真实浏览器以及独立 MySQL/MinIO/Celery 环境验证数据控制台链路。通用工作流的自动 Job/产物接线仍是独立范围，完成后仍需对应集成测试和实时取消/恢复证据。

## 8. 故障定位

1. 记录 revision、request ID、Job ID/attempt、时间、flag、sample digest 和准确 entry URL。
2. 不先重启，先检查 `bash scripts/status.sh`、`/health` 和 `/health/ready`。
3. 检查第一处 API/worker/storage/MySQL error，不只看 browser 最终消息。
4. 确认 `alembic current`、Job/JobStep state、queue worker identity 和 Stage source 可用性。
5. 比较 `files.sha256`、storage byte 和 downloaded byte。
6. 区分 browser fixture coverage、真实 backend call 和真实 worker/object result。
7. 增加聚焦 regression，再重跑全部受影响层和必要 E2E 场景。

禁止通过启用内存 fallback、关闭 authorization、接受任意 spreadsheet 内容，或把 skipped scenario 描述为 verified 来让门禁通过。
