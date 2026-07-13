# 全栈工作流验证

> **范围：** Nginx、FastAPI、MySQL、Celery SQL transport、storage、frontend retry/SSE/download
> **最近文档审计运行：** 2026-07-14
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

## 6. 2026-07-13 PR #1 选择性吸收与数据监视证据

本轮没有直接合并冲突 PR，也没有修改其远程分支。实现从当前 `main` 延伸，吸收字段扩容、流程桥接和前端工具思路，重写了 DXF 预览、权限聚合、事务登记和前端状态管理。真实运行探针使用独立上传文件，没有处置既有扫描异常。

| 门禁 | 结果 | 实际覆盖 |
|---|---|---|
| PR 审查 | pass | PR #1 共 6 个提交、22 个文件，基于旧主干且冲突；拒绝双 Alembic head、回改历史迁移、Celery/Kombu DDL、旧 8000 端口、失配 npm/uv lock、PNG 慢渲染和前端 N+1 |
| Backend Ruff | pass | `app`、`tests` 与全量 verifier；DXF SVG、缓存/流水、Excel Final 聚合/分页/迁移和 Compose 契约 |
| Backend 全量 | **866 passed，5 skipped** | SQLite 隔离 API/service/security/state；15 条既有 dependency/deprecation warning，无失败 |
| Documentation checker | pass | 91 个 OpenAPI path / 110 个 operation、中文文档、端口、28 张模型表和当前 Alembic head |
| Alembic | pass | 单一 head `9c4e7b1a2d60`；`alembic check` 无待生成 operation；空 MySQL schema 完整升级并验证 28 张业务表/种子数据 |
| Infrastructure / Compose | **110/110 pass** | 活动 MySQL 37 张运行时表；生产 Compose、workers profile、开发覆盖及 workers profile 均可合并 |
| Stage tests | **28 + 28 + 259 passed** | dwg2dxf、dxf2dwg、Excel Final multi_split |
| Frontend install/build | pass | `npm ci`、TypeScript 6、Vite 8；DXF 预览与 Excel Final 控制台 production bundle |
| Playwright 全量 | **72 passed，1 skipped** | 真实本地 API；新增鉴权 DXF Blob、Excel Final 精确总览/工具/详情、失败重试和 DXF→Excel 桥接；未提供外部真实 XLS 样本路径的条件用例按设计跳过 |
| DXF 性能对照 | pass | 同一约 5 MiB / 21,117 文档实体样本：PR Matplotlib PNG 约 27.28 秒；当前 ezdxf SVG recording 约 1.724 秒，输出约 2.62 MiB |
| Local + MySQL 探针 | pass | 文件 #891 首次生成、二次缓存命中；SVG 1,573,087 bytes；`preview_generate` 与 outbound `preview` 均 succeeded 且实际字节一致 |
| MinIO + MySQL 探针 | pass | 当前源码临时 API 连接健康 Compose MinIO 与真实 MySQL；真实 3.26 MiB DXF #894 生成 SVG #895，二次缓存命中；MinIO object listing 显示 registered=true，生成/出库均 succeeded 且 1,888,900 bytes |
| UI 规范与截图 | pass | 最新 Web Interface Guidelines 自审；输入标签/名称、图标按钮、focus-visible、reduced-motion、服务端分页、控制台弃用警告清理；截图 `output/playwright/excel-final-data-console-final.png` |

真实 MySQL 曾在第一次浏览器预览中暴露 `REPEATABLE READ` 快照问题：来源行锁事务先开始，独立 storage session 后创建流水，旧快照再 `SELECT ... FOR UPDATE` 新流水时触发 MySQL 1020。当前实现先在调用者事务持久化 `preview_generate` 意图并提交，再渲染/推进独立状态，最后在来源锁定事务中写对象登记和完成流水；失败由独立结算保留 `failed`，对象写后业务回滚仍触发补偿删除。

MinIO 探针没有重建或替换运行 33 小时的 Compose 容器。它启动当前源码的临时 8011 API，连接现有健康 MinIO 容器和真实 MySQL，完成探针后立即停止；因此证明的是当前代码对真实 MinIO/MySQL 的登记和读取闭环，不是旧 Compose 镜像已经部署本次提交。

## 7. 2026-07-13 生产一致性二轮硬化证据

本轮在第 6 节的 PR 选择性吸收基础上，继续收紧 Excel Final 请求幂等、查询域、DXF 预览生命周期和前端可恢复监视状态。验证使用现有真实 MySQL、local storage 和运行中的 Compose MinIO；探针只删除自身创建的对象和合成 Job，没有处置既有业务文件或一致性 finding。

| 门禁 | 结果 | 实际覆盖 |
|---|---|---|
| Backend Ruff | pass | `app`、`tests`、全量 verifier、迁移/文档生成脚本 |
| Backend 全量 | **879 passed，5 skipped** | SQLite 单元/集成回归加真实 MySQL 并发幂等用例；15 条既有 dependency/deprecation warning，无失败 |
| MySQL 并发重放 | **连续 5 次 pass** | 两个独立 Session 同键竞争只产生一个 Job；失败者在 savepoint rollback 后以 locking current read 越过旧 consistent snapshot 读取胜者 |
| Alembic | pass | 单一 head `d5e8a1c4b720`；活动库增量升级、`alembic check` 无待生成 operation；空 MySQL 完成 13 个 revision，验证 28 张业务表、唯一约束与种子数据 |
| Local + MySQL 事务探针 | pass | Excel file #903 / Job #1080 重放未复制对象；DXF #904 / SVG #905，677 bytes；上传、预览生成、鉴权出库、源删除失效和软删除流水均 succeeded |
| MinIO + MySQL 事务探针 | pass | 使用 Compose 内部 MinIO endpoint 与 `.env.docker` 对应凭据；Excel file #906 / Job #1081、DXF #907 / SVG #908，677 bytes；同一组入库/出库/失效操作全部 succeeded，探针对象已清理 |
| Infrastructure / Compose | **110/110 pass** | MySQL、MinIO、Nginx、生产/开发 Compose 和 worker 契约；没有为了探针发布 MinIO 9000/9001 |
| Stage tests | **28 + 28 + 259 passed** | dwg2dxf、dxf2dwg、Excel Final multi_split |
| Frontend build | pass | TypeScript 6、Vite 8；幂等提交、URL 状态、真实后端健康标签和标题区对比度修复进入 production bundle |
| Playwright 全量 | **72 passed，1 skipped** | 73 条浏览器场景；Excel Final 数据控制台、历史/刷新恢复、Local/SQLite 健康文案、CORS 幂等头、转换桥接和视觉回归；外部真实 XLS 样本未配置的成功链路按设计跳过 |
| 浏览器实景复核 | pass | 1440×1000 管理员会话无 console error；后端实际报告 `MySQL 权威数据 · 本地对象存储`，最近刷新、分页、搜索和任务区可见；说明文字计算颜色为 `rgb(185, 206, 216)` |
| Documentation checker | pass | 生成 API 合同、91 个 path / 110 个 operation、数据库 head/表数、配置边界、操作探针与交叉链接一致 |

本轮全量回归首次暴露了一个只在真实 MySQL 并发下出现的竞态：两个请求都在唯一键提交前做普通查询，竞争失败者虽然回滚了 nested transaction，但外层 `REPEATABLE READ` 的 consistent snapshot 仍看不到刚提交的胜者，偶发再次抛出 1062。修复不是吞掉异常或重试 INSERT，而是在唯一冲突后执行 `SELECT ... FOR UPDATE` current read，再核对原请求参数；连续聚焦运行和包含该用例的后端全量均通过。

浏览器还证明自定义 `Idempotency-Key` 会触发 Vite 跨源预检；后端现精确允许该请求头，并由回归测试防止后续删除。健康接口和前端不再把 SQLite/local 环境写成 MySQL/MinIO。Compose MinIO 默认只在内部网络可达，宿主 `.env` 的 `localhost:9000` 和密钥不能代替 `.env.docker`；本次使用临时容器 IP 验证真实 MinIO，没有改写密钥或扩大端口暴露。

最终全页截图位于 `output/playwright/excel-final-production-observability.png`。它显示真实数据库/存储标签、刷新时间、全局指标、上传登记、跨批次检索、批次分页和近期任务；标题说明文字使用项目自有类名控制对比度，不依赖 Ant Design 6 当前渲染出的 HTML 标签。

## 8. 2026-07-14 双向 CAD 转换吞吐证据

本轮使用 24 逻辑 CPU 的本地工作站、ODA File Converter AppImage、独占持久 Xvfb 和用户指定目录 `/home/Creeken/Paper/CAD_research/Data/十份排版/排版1/C区域四节钢柱（宝冶）/2.零件图/1：1零件图`。目录实际包含 135 个 `.dwg`（包括合并图）；所有测量均校验输出数量，并检查 DXF `SECTION/$ACADVER` 头或 DWG `AC10` magic。

旧实现的 16 文件逐文件基线为串行 60.313 秒、16/16 成功；直接把旧 `xvfb-run -a` 调到并发 2/4/8 时分别只有 13/16、11/16、8/16 成功，失败来自 display 选择和清理竞态。改为一个持久 Xvfb 后，同一逐文件 workload 在并发 1/2/4/8 下为 12.334/6.388/3.280/1.937 秒，均 16/16 成功；反向为 12.424/6.344/3.395/1.865 秒，均 16/16 成功。

生产批量路径不为每文件启动一次 ODA，而是按版本目录批处理并自适应分片。可重复命令为：

```bash
cd backend
uv run python ../scripts/benchmark_cad_conversion.py \
  --input-dir '/home/Creeken/Paper/CAD_research/Data/十份排版/排版1/C区域四节钢柱（宝冶）/2.零件图/1：1零件图' \
  --concurrency 1,2,4,8 --direction roundtrip --mode batch \
  --json-output ../output/cad-benchmark-full-tuning.json
```

| 目录分片 | DWG -> DXF | DXF -> DWG | 结果 |
|---:|---:|---:|---|
| 1 | 1.836 s / 73.537 files/s | 1.719 s / 78.551 files/s | 双向 135/135 |
| 2 | 1.358 s / 99.382 files/s | 1.377 s / 98.012 files/s | 双向 135/135 |
| 4 | **1.196 s / 112.840 files/s** | 1.260 s / 107.158 files/s | 双向 135/135 |
| 8 | 1.284 s / 105.117 files/s | **1.209 s / 111.628 files/s** | 双向 135/135 |

综合两向吞吐、CPU 余量和 8 路开始出现的启动竞争，生产默认选择最多 4 个分片、每片至少 8 文件；Celery queue concurrency 8 用于多个批次之间的吞吐。两者含义不同，不能把 8×4 当作单批次固定并发。实时进度使用一个聚合 SSE 连接观察最多 200 个文件，500 ms 推送变化；页面只取消当前方向/文件夹范围的 active Job。

当前源码重启后只保留本地 `dxf`/`dxf2dwg` topology，Compose 的同名 worker 已停止，`scripts/status.sh` 无重复消费者警告。通过 Nginx `:8080` 的真实 32 文件闭环结果如下：DWG -> DXF 批量提交 0.088 秒，SSE 从 32 queued 经 32 running 和分段完成到 32 succeeded 共 6 帧、4.040 秒；派生 DXF 上传 2.730 秒，DXF -> DWG 提交 0.063 秒，SSE 共 7 帧、4.030 秒。32 个 DXF 和 32 个 DWG 均经 result、签名 URL 和 Nginx 实际下载，分别通过 DXF 文本头和 DWG `AC10` magic 校验。另一个 2 文件批次被作用域取消 2/2，再提交后 SSE 1.513 秒收敛为 2/2 succeeded，未调用管理员全局取消。

第一次真实批处理还暴露了 SQLite 测试未覆盖的 MySQL 1020：ODA 已成功，但结果持久化事务先读取 Job/源文件，再由独立事务创建并推进 `file_transfers`，旧 `REPEATABLE READ` 事务锁定该 transfer 时报告“Record has changed since last read”，导致 32/32 结果登记失败。修复按既有 DXF preview 成功模式，在结果 metadata 事务开始前由调用者事务登记 transfer intent 并提交，再推进 transfer、写对象、登记 StoredFile/AnalysisResult 和完成 Job；前后向成功测试要求 `save_bytes_as_file` 显式收到 `transfer_uid`。修复后的上述 32+32 下载闭环证明该错误未复现。

最终自动门禁为 backend **896 passed、6 skipped**（15 条既有 dependency/deprecation warning）、Ruff 无错误、Alembic 无待生成 operation；两个 ODA Stage 各 **30 passed**；TypeScript 6 + Vite 8 production build、Compose config 和文档一致性均通过。双向转换页的实时浏览器聚焦回归为 **8 passed、2 skipped**：单文件上传确实调用批量 Job API，“继续任务”调用批量入口，active 可见性和范围统计均通过；两个“点击全部暂停”用例因各自页面加载时没有 active Job 按设计跳过，作用域取消由前述真实 2 文件 API 闭环和后端授权/状态测试覆盖。

## 9. 2026-07-11 基线证据

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

## 10. 历史集成记录

仓库此前记录了 2026-07-11 fresh-volume 集成运行，观察为：

- MySQL 从空 volume 迁移到 `a74c2e9f1d30` 并创建 queue-claim index。
- report Job 通过 API -> MySQL broker -> Celery -> MySQL state -> MinIO，下载 SHA-256 匹配。
- MinIO 中断使 readiness 返回 503 且 database 保持 `ok`；恢复后旧对象仍存在。
- 真实 attempt-2 probe 拒绝 legacy 单参数 message，只在 `(job_id, 2)` 投递后完成。

以上四项仅作为 2026-07-11 的带日期历史证据保留。当时没有重启正在运行的本地 FastAPI，实时 `/openapi.json` 仍是旧进程加载的 71 path/88 operation，因此当时新增 route 只由 TestClient/OpenAPI 生成与迁移测试证明。2026-07-13 的当前证据见第 6 节：当前源码为 91 path/110 operation，并已用当前源码 API、真实浏览器和真实 MySQL/MinIO 验证预览与登记链路。通用工作流的自动 Job/产物接线仍是独立范围；DXF→Excel 页面的显式 Excel Final 桥接不改变该边界。

## 11. 故障定位

1. 记录 revision、request ID、Job ID/attempt、时间、flag、sample digest 和准确 entry URL。
2. 不先重启，先检查 `bash scripts/status.sh`、`/health` 和 `/health/ready`。
3. 检查第一处 API/worker/storage/MySQL error，不只看 browser 最终消息。
4. 确认 `alembic current`、Job/JobStep state、queue worker identity 和 Stage source 可用性。
5. 比较 `files.sha256`、storage byte 和 downloaded byte。
6. 区分 browser fixture coverage、真实 backend call 和真实 worker/object result。
7. 增加聚焦 regression，再重跑全部受影响层和必要 E2E 场景。

禁止通过启用内存 fallback、关闭 authorization、接受任意 spreadsheet 内容，或把 skipped scenario 描述为 verified 来让门禁通过。
