# 运维

## 运维边界

这是当前仓库本地与 Compose 拓扑的 runbook。它不声称监控、备份调度、TLS、日志汇聚、滚动升级或灾难恢复已经自动化。操作员必须在 Git 外记录环境凭据、存储位置、保留期、责任人和恢复目标。

## 启动、状态与停止

本地受管拓扑：

```bash
bash scripts/start-all.sh
bash scripts/status.sh
bash scripts/doctor.sh --since-minutes 60
bash scripts/stop-all.sh
```

`start-all.sh` 按需构建前端，启动已实现队列 worker和承载有界运维/每日归档任务的 `maintenance` worker、FastAPI `8010` 与本地 Nginx `8080`。`agent`、`cad`、`dispatch` 只有预留路由，不启动空 worker。`start-dev.sh` 用 Vite 替代 Nginx/静态服务。脚本按 Celery app、queue 和 node name 识别 worker；pidfile 只是跟踪辅助，不是唯一进程身份。每个启动脚本还传入队列/并发环境元数据，供控制平面写入 MySQL 活动记录；它不构成分布式 lease。

后端代码晚于当前 Uvicorn 进程时，`status.sh` 报告“运行代码已过期”并返回非零。此时使用 `bash scripts/start-all.sh --restart-backend`；它只优雅停止 cwd 为本仓库 `backend/` 的 Uvicorn，未知进程占用 8010 时拒绝操作。前端源码、依赖清单或构建配置晚于 `dist/index.html` 时，普通 `start-all.sh` 会重新构建，也可用 `--rebuild` 强制执行。

Compose 拓扑优先使用带环境预检的包装脚本：

```bash
bash scripts/docker.sh check
bash scripts/docker.sh up
bash scripts/docker.sh up-workers
bash scripts/docker.sh status
bash scripts/docker.sh smoke
bash scripts/docker.sh logs
```

仅需要转换 worker 时才执行第二个 `up`。预留队列没有 task，也没有常驻消费者。

离线服务器发布不保留仓库，使用安装目录中的稳定入口：

```bash
/opt/dwg-agent/scripts/server-deploy.sh up /opt/dwg-agent
/opt/dwg-agent/scripts/server-deploy.sh status /opt/dwg-agent
/opt/dwg-agent/scripts/server-deploy.sh smoke /opt/dwg-agent
/opt/dwg-agent/scripts/server-deploy.sh down /opt/dwg-agent
```

`install` 升级会保留既有 0600 `.env.docker`，但更新固定 Compose、SQL 初始化资源和镜像清单；
`down` 保留 MySQL、MinIO 和应用命名卷。不得在服务器安装目录解密并长期保留明文包；部署器只
在 `/tmp` 创建受控临时目录，并在成功或失败退出时清理。Docker 镜像内是可执行字节码而非
原始业务源码，这不改变运行功能，也不等于能对抗宿主 root 逆向。

## 健康信号解释

| 信号 | 能证明 | 不能证明 |
|---|---|---|
| `GET /health` 200 | FastAPI 进程可响应 | MySQL、存储、worker 或 Stage 可用 |
| `GET /health/ready` 200 | MySQL 和已配置存储探测通过 | 特定 feature flag、queue worker、ODA 或手册查询可用 |
| worker healthy | Celery PID 存活且发出 `worker_ready` | 队列 task 实现或 Stage 依赖可用 |
| MySQL healthy | server 接受 root ping | 应用授权、迁移 head、broker 索引或查询延迟正确 |
| MinIO live | server 进程响应 | bucket、凭据、对象持久化或下载授权可用 |
| Nginx `HTTP_PORT` 可达 | HTTP 网关和静态根响应 | TLS 已配置；Compose 不发布 443 |
| `GET /api/v1/system/infrastructure` | 管理员可见 DB/storage/目录即时概览 | 自动备份、对象与 metadata 完全一致或恢复可用 |
| `GET /api/v1/excel-final/health` | 当前业务数据库/存储类型与 Excel Final 依赖分项状态 | worker 一定消费任务或任意输入 schema 一定受支持 |

应同时使用 readiness 和一笔代表性业务交易。不要把 `/health` 改成深度依赖检查；依赖中断时 liveness 仍需有用。

## 日志与首次响应

本地日志位于 `/tmp`：

```bash
tail -n 200 /tmp/dwg-agent-backend.log
tail -n 200 /tmp/dwg-agent-worker-report.log
tail -n 200 /tmp/dwg-agent-nginx-error.log
```

Compose 日志：

```bash
docker compose logs --since=15m backend-api worker-report mysql minio nginx
docker compose --profile workers logs --since=15m worker-dxf worker-dxf2dwg worker-dxf2excel worker-dxf-classification worker-excel-final
```

重启前保留首个异常、request ID、Job ID/attempt、worker node、依赖状态和时间戳。当前日志没有集中保留或关联后端；`/tmp` 日志会在重启时丢失，容器日志保留取决于 Docker logging driver。

## HTTP 4xx、499 与上传拥塞

仓库前端统一解析 API 错误信封：工人界面只显示经过过滤的中文原因、建议动作和“请求编号”；`error.code` 只供页面内部选择恢复动作，不展示给工人。422 会把字段名翻译成业务中文并展开最多三项原因。文件夹上传保留每个失败文件的名称和原因，不再只报告失败数量；下载返回 JSON 错误时使用同一规则。Traceback、数据库驱动、容器名、本机路径、URL 和后端响应原文不得进入前端。

若生产页面仍只显示状态码，通常表示网关返回了非 JSON 页面或运行中的前端构建已过期。先运行 `bash scripts/status.sh`，再按提示执行 `bash scripts/start-all.sh --rebuild`。维护人员使用弹窗中的请求编号对照 `doctor.sh` 输出；后台日志只供维护人员查看，不得复制到工人界面。

ODA 容器继续把 `/tmp` 作为加固临时文件系统。`worker-dxf` 与 `worker-dxf2dwg` 必须设置 `TMPDIR=/app/var/appimage-tmp`，启动脚本会创建该目录，使 AppImage 在应用工作卷内解包执行。若任务在 70% 左右以返回码 127 失败，应先检查该环境变量、目录写权限和 Xvfb，不要反复重提同一图纸。

```bash
bash scripts/doctor.sh --since-minutes 60
NGINX_ACCESS_LOG=/path/to/access.log bash scripts/doctor.sh --log-only
```

`doctor.sh` 去除查询串后按状态、方法和路径聚合，显示最近时间与有限 request ID。它不会输出签名、批次名、Cookie 或 Authorization。判断规则：

| 状态 | 运维含义 | 首要动作 |
|---|---|---|
| 401 | access/refresh 会话无效，可能是正常过期 | 对照登录/刷新时序；不要把鉴权失败改成 200。 |
| 404 | 对象不存在、SSE 固定测试 ID 或运行路由缺失 | 先区分固定测试探针；真实 ID 再查权限与软删除。 |
| 405 | 客户端已调用新路径，但运行 FastAPI 未加载该 method | 运行 `status.sh` 检查代码/进程漂移，再受控重启。 |
| 409 | 状态冲突；ZIP 常见于请求格式不完整或对象不一致 | 根据 `error.code` 区分 `FILE_EXPORT_FORMAT_UNAVAILABLE` 与 `STORAGE_INCONSISTENT`。 |
| 422 | 请求 schema 或业务参数不合法 | 修正客户端请求；保留后端校验。 |
| 499 | Nginx 发现客户端在响应前断开，不是应用返回 | 检查页面切换、AbortController、SSE 关闭和慢上传；单独统计。 |
| 5xx | 服务端或依赖故障 | 用 request ID 查 FastAPI 首个堆栈，修根因后再重试。 |

2026-07-18 的文件夹上传 500 由浏览器同时提交 8 个文件、超过 API 默认 `DB_POOL_SIZE=2 + DB_POOL_MAX_OVERFLOW=2` 引发 QueuePool 超时。当前通用文件夹上传和双向 CAD 页面并发限制为 4。若修改连接池或前端并发，必须一起做负载测试；不要只延长 pool timeout 掩盖容量不匹配。

ZIP 弹窗调用 `POST /api/v1/files/download-zip/preview` 显示每种格式的可用数量，只允许提交覆盖全部所选文件的格式。预检与下载之间仍可能变化，因此正式下载保持严格 409；失败后弹窗不关闭，操作员应重新预检，不得要求后端静默漏文件。

## 质量门禁入口

```bash
bash scripts/verify.sh quick
bash scripts/verify.sh full --allow-blocked
```

`quick` 覆盖 Shell、ruff、聚焦后端/脚本、生成文档和前端构建。`full` 追加完整后端、Alembic、基础设施、Compose、Stage、隔离迁移和浏览器测试。`--allow-blocked` 只作用于 sudo、Windows/ODA 或外部 Stage 等明确可选依赖，不会把代码或测试失败改写为通过。完整命令和退出码见 [`scripts/README.md`](../../scripts/README.md)。

## 数据库操作

本地 MySQL 命令：

```bash
bash scripts/db.sh status
bash scripts/db.sh check
bash scripts/db.sh tables
bash scripts/db.sh backup /secure/path/dwg_agent.sql.gz
bash scripts/db.sh migration-test
bash scripts/db.sh clean          # 清理 migration-test 残留临时库 + 退役 var/app.db
bash scripts/db.sh reap-storage --dry-run   # 预览软删除对象回收（见 database.md §6.5）
```

`migration-test` 创建并删除临时 schema，并顺带清理历史崩溃残留的临时库；当前目标为 `b7e2c9a4d610` 和 47 张模型表，额外验证生产输入、分批导出、完整备份留存、DXF 分类、DXF 拆板及复核决定、余料库存、控制平面与每日归档账本、`files.purged_at`、`jobs.request_key`/唯一约束及种子数据兼容；它不测试 downgrade 或生产数据迁移时长。需 `sudo mariadb` 的子命令先经 `ensure_sudo` 预检，无 TTY 且凭据未缓存时快速失败而非挂起。

迁移前：

1. 验证最近数据库和对象存储备份可恢复。
2. 停止应用写入，或进入有记录的维护窗口。
3. 针对准确 revision 运行 `migration-test`。
4. 仅执行一次 `alembic upgrade head`，监控耗时与锁，然后运行 readiness/schema 检查。
5. 启动 worker/API，并执行一笔代表性上传/处理/下载。

当前 `drawings`/`drawing_versions` 循环 FK 会产生 Alembic autogenerate 排序 warning。`alembic check` 仍应报告无新操作；不能因为已知 warning 就忽略新的 operation。

## 备份与恢复

仓库有本地 MySQL helper和 Compose `scripts/docker.sh` 备份/恢复命令，但没有调度、离机复制、加密、PITR 或自动恢复演练。可恢复集合必须包括：

| 数据 | 必要内容 |
|---|---|
| 应用 MySQL | 全部业务表、`alembic_version` 和当前 Celery runtime 表 |
| 对象存储 | 每个已配置 bucket，包括 original、derived、report、按需 temporary 和 DXF bucket |
| 手册库 | 由唯一可信 `/home/Creeken/Paper/CAD_research/五金手册.xls` 确定性生成并逐值审计的 `hardware_handbook` |
| 密钥/配置 | 部署值的加密副本；禁止提交真实 `.env.docker` |
| 证据 | 备份时间、应用 revision、迁移 head、对象快照标记、checksum 和恢复测试结果 |

默认 Compose 备份：

```bash
bash scripts/docker.sh backup /secure/backups/dwg-agent-YYYY-MM-DD
```

该命令导出 `dwg_agent` 与 `hardware_handbook`，归档 MinIO volume 并生成 `SHA256SUMS`。MinIO Server 极简镜像不提供 `tar`；脚本使用无网络的临时后端工具容器继承数据卷，不新增常驻服务。它不停止 writer，也不生成数据库与对象的原子快照；严格恢复点必须在维护窗口先停止写入。Compose service name 不支持 `worker-*` wildcard，人工停服时必须逐个列出服务。

只在隔离或维护环境恢复；脚本要求全部 Compose 服务已停止，并会清空目标 MinIO volume 后解包：

```bash
bash scripts/docker.sh down
bash scripts/docker.sh restore /secure/backups/dwg-agent-YYYY-MM-DD
bash scripts/docker.sh up
bash scripts/docker.sh smoke
```

随后运行 `alembic current`，把代表性 `files.sha256` 与字节比较，并执行完整工作流。恢复 broker row 可能重新引入 queued delivery；启动 worker 前检查 queued/running Job 和 broker table。脚本不备份 `app_var`、live secret 或镜像，恢复成功也不等于已有 RPO/RTO 证据。

## 存储事故

存储不可用时：

1. 确认 `/health` 保持 200，`/health/ready` 报告 database `ok`、storage `error`。
2. 停止提交新文件 Job；不要切换到未规划的本地 fallback。
3. 检查 endpoint、凭据、网络、bucket 是否存在和 volume 状态，不记录 secret。
4. 恢复存储，验证无需重启 FastAPI 即恢复 readiness，再下载事故前对象并比较 SHA-256。
5. 复查中断期间失败的 Job，只从受支持终态重试。

数据库事务 rollback 前写入的对象会自动尝试补偿删除；失败写入 `file_transfers.status=compensation_required`。软删除对象和崩溃孤儿由数据控制台的一致性扫描发现，不能仅凭 bucket 总数差额推断具体对象。`bash scripts/db.sh reap-storage --include-orphans` 仍是保留期维护工具，必须先 dry-run；永久清理应优先在控制台选择 finding、预检并确认，禁止对真实 bucket 直接批量猜测删除。

## 生产流程分批导出与释放

入口位于生产流程 Stage A3 “图纸分类与拆板”的 `03 · 图纸拆板与独立校验` 卡片标题栏右侧。它不在下方“生产产物与证据”汇总区。操作员可勾选四类数据：

| UI 标签 | 机器类型 | ZIP 一级目录 | 数据来源 |
|---|---|---|---|
| 原 DXF | `classified_dxf` | `原DXF/` | 当前分类 attempt 的分类后 DXF |
| 正常拆板 DXF | `processed_dxf` | `正常拆板DXF/` | 当前拆板 attempt 独立校验通过的正常图 |
| 原 Excel | `source_excel` | `原Excel/` | 已冻结生产输入中的唯一原 Excel |
| 产出 Excel | `stage1_excel` | `产出Excel/` | 当前 Excel 第一阶段成功 Job 的结果 |

目录内使用数据库登记的 `original_name`，不翻译、不加前后缀、不自动处理重名；同一目录发生不区分大小写的文件名冲突时，服务端返回 409，操作员应先核对登记，不能手工修改服务器对象键规避。

标准操作：

1. 点击“分批导出”，核对每类文件数与总量并勾选需要的数据。
2. 点击“生成并下载 ZIP”。响应从 Local/MinIO 直接流向浏览器，不会先在服务器磁盘生成临时 ZIP。
3. 等页面显示“服务端已完整发送 ZIP”，在本地打开 ZIP，检查四个固定目录和代表性文件。
4. 只有确认本地副本可用后，点击“已保存，删除服务器文件”，再完成第二次不可恢复确认。
5. 页面显示释放字节数后，在数据控制台核对 `workflow_export_purge` 流水、`files.purged_at` 和对象 `stat`。

关闭弹窗、下载中断、状态仍为 `prepared/downloading/download_failed`、未执行第二次确认，均不会删除服务器文件。清理期间若有 workflow stage 处于 queued/running，服务端拒绝删除。成功清理会物理删除所选对象及其 DXF SVG 预览缓存，清空短期导出清单和下载能力；`files` 小型墓碑与生产账本继续保留外键历史，但不包含可恢复字节。

若接口返回 `WORKFLOW_EXPORT_PURGE_FAILED`、流水错误码为 `WORKFLOW_EXPORT_PURGE_PARTIAL`，或流水状态为 `compensation_required`，立即停止对该流程继续写入，保存 request ID、export UID 和 transfer UID，在“文件登记/存储对象”逐项核对清单范围并运行一致性扫描。对象删除不可回滚，不得通过直接 SQL 把墓碑改回 available；确认剩余对象后从同一导出记录安全重试或按存储事故流程处置。

## 终态生产批次完整备份与整批释放

存储水位接近阈值时，从已结束的生产批次详情页点击“完整备份与释放空间”。不要在 MinIO
控制台按前缀猜测删除，也不要把每日归档当成该批完整备份。标准步骤：

1. 核对页面列出的正式文件数、预览缓存数和预计释放量；存在活动任务、缺失登记、对象不一致
   或跨批次共享引用时，预检会给出稳定错误码并禁止继续。
2. 生成并下载完整备份。ZIP 从 Local/MinIO 直接流向浏览器，逐文件核对大小和 SHA-256；
   必须等待页面显示“服务端已确认完整备份发送完毕”。
3. 在本地真正打开 ZIP，抽查 `输入/`、`阶段产物/` 和 `其他结果/`。管理员勾选已核对，输入
   `DELETE WORKFLOW <id>` 后才可提交永久清理。
4. 清理由 maintenance worker 异步执行。弹窗可关闭；重新打开会读取 MySQL 中最近状态。
   `purge_queued/purging` 时不要重复提交。
5. 成功后在流转流水核对 `workflow_retention_export` 和 `workflow_retention_purge` 均为
   `succeeded`，并核对对象已不存在、`files.purged_at` 已填写。Workflow、输入、Job、分类和
   拆板关系必须仍可查询。

入队失败表示删除尚未开始，所有对象应完整保留。删除中断或数据库墓碑提交失败时，页面显示
`purge_failed`，流水可能为 `compensation_required`；保留 request ID、export UID 和 transfer
UID，先运行一致性扫描，不得手工把登记改成已删除或直接清 bucket。确认存储恢复后从同一完整
备份重试，幂等删除会跳过已经不存在的对象，并在全部目标完成后统一提交墓碑。

## 图纸分类选择导出

Stage A3 卡片标题栏的“导出”与“分批导出”是两个独立功能。“导出”只下载当前拆板
attempt 对应的分类后原始 DXF，不删除服务器文件。弹窗提供四个多选项：

| UI 标签 | 选择规则 | ZIP 一级目录 |
|---|---|---|
| 未通过的 BH | 当前拆板 attempt 中分类类型为 BH 且自动处理未通过 | `未通过的BH/` |
| 未通过的 BOX | 当前拆板 attempt 中分类类型为 BOX 且自动处理未通过 | `未通过的BOX/` |
| PL | 当前分类 attempt 中精确类型为 PL、且未被自动接纳 | `PL/` |
| 其他 | 其余未被自动接纳的 PX、待确认、不可读或其他类型 | `其他/` |

点击“下载所选 DXF”后，ZIP 从 Local/MinIO 直接流向浏览器，不在服务器生成临时 ZIP。
自动接纳的 BH/BOX 不会混入；报告、Excel、DWG 和拆板产出也不包含在内。叶子文件名保持
数据库登记值；若同一类别有同名文件，只增加中间隔离目录。该功能没有清理确认步骤，下载
完成或中断都不会修改 `files.status`、`purged_at` 或底层对象。

## 数据控制台运行手册

入口为 `/data-console`；旧 `/admin/infrastructure` 会跳转到该入口。页面只保留“生产任务”和“文件存储”两个页签，供生产人员管理现有任务与已登记文件，不展示数据库表结构或内部队列。

1. 在“生产任务”核对生产项目、当前阶段和进度；进入所属项目继续上传、分类、拆板或 Excel 整理。
2. 在“处理任务”查看所有真实 Job 状态与错误。管理员只能取消后台状态机允许取消的活动任务；失败或取消任务才显示重试。拆板必须回所属工作流重开，页面不提供后台会拒绝的直接重试。
3. 在“文件存储”按中文存储区和目录核对对象、大小及登记关系。上传由后端按文件类型自动归档，不承诺写进当前目录。
4. 只有已登记且有权限的文件显示下载、更改路径和软删除。未登记对象只作异常提示，不允许从页面直接处置。
5. 操作失败时保留页面给出的错误原因和请求编号。不要直接修改 MySQL 行或对象路径规避业务规则。

每日归档、存储一致性扫描和控制平面接口仍是后台维护能力，但不在生产人员的数据管理台展示；需要维护时按对应脚本/API 运维流程执行。完整页面字段和接口对应见[数据管理台使用说明](../operations/data-console.md)。

DXF 在线预览对象会以 `operation=preview_generate` 登记内部生成流水，并发生成的锁内缓存复用写 `preview_cache_reuse`；源文件变化、缓存对象丢失或源 DXF 软删除时写 `preview_invalidate`，浏览器读取写 `direction=outbound, operation=preview`。源删除后 SVG 物理对象仍处于保留期，但登记和内容端点必须不可用。排查预览时应同时核对源 DXF、SVG `files` 行、对象 `stat` 和流水；不要把弹窗能打开当作登记一致性的充分证据。

代表性上传/幂等/预览/删除事务探针：

```bash
cd backend
STORAGE_BACKEND=local .venv/bin/python ../scripts/storage/verify_transactions.py

# Compose MinIO 不发布宿主端口；只为探针读取内部地址和容器凭据，不打印 secret。
MINIO_IP=$(docker inspect complete_framework-minio-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
MINIO_ACCESS_KEY=$(sed -n 's/^MINIO_ACCESS_KEY=//p' ../.env.docker | head -n 1)
MINIO_SECRET_KEY=$(sed -n 's/^MINIO_SECRET_KEY=//p' ../.env.docker | head -n 1)
STORAGE_BACKEND=minio MINIO_ENDPOINT="http://$MINIO_IP:9000" \
  MINIO_ACCESS_KEY="$MINIO_ACCESS_KEY" MINIO_SECRET_KEY="$MINIO_SECRET_KEY" \
  .venv/bin/python ../scripts/storage/verify_transactions.py
```

脚本创建独立探针对象，验证 Excel 重放只登记一个文件/Job、DXF SVG 入库与鉴权出库、源删除联动和传输终态；结束时软删除登记、删除合成 Job 并物理移除仅由本次创建的对象。它不会处置既有 finding。宿主 `.env` 与 `.env.docker` 的 MinIO endpoint/凭据必须分别核对；`SignatureDoesNotMatch` 是凭据不一致，不是网络故障。

每次事故记录 scan ID、finding ID、transfer UID、request ID、操作人、时间、预检范围和最终对象 stat。不要把浏览器提示当作唯一证据，应同时查询流水详情、finding 状态和对象存储。

## Worker 与队列事故

超时任务恢复是受保护的后台维护接口，不在生产人员的数据管理台提供按钮。管理员通过 `POST /api/v1/control-plane/maintenance/reconcile-stale-jobs` 手动提交一次恢复；该操作仅投递 `maintenance` queue 的 `reconcile_stale_jobs`，只会处理超过 `CELERY_STALE_JOB_TIMEOUT_SECONDS` 且仍为 running 的 Job。每次投递和完成都会写入 `control_plane_events`，不会自动启动周期维护、删除对象或修复业务格式错误。若维护队列不可用，API 返回 503 并保留 `enqueue_failed` 事件，先恢复 worker 后再按维护流程重试。

```bash
bash scripts/status.sh
ps -ef | rg 'celery.*app.platform.messaging.celery_app'
```

检查每个本地队列恰好有一个预期受管 node。SQL transport 没有可靠 fanout inspect 健康路径。worker 死亡可使 Job 保持 running，直到 `CELERY_STALE_JOB_TIMEOUT_SECONDS`；随后 worker 启动将其标记 `CELERY_WORKER_LOST`。使用创建新 attempt 的 retry API 前，先验证 Stage 和存储。

若空库 worker 一直 `health: starting/unhealthy` 且 `/tmp/dwg-celery-ready` 不存在，检查 MySQL `SHOW FULL PROCESSLIST`。`CREATE INDEX ix_kombu_message_queue_timestamp_id_visible` 若等待 metadata lock，说明 Kombu 建表连接未释放；当前版本已有显式 commit/close 回归，出现该现象通常意味着运行了旧镜像。记录 image digest，停止旧 worker，升级镜像后在隔离空卷重验，禁止手工杀连接后继续声称冷启动正常。

不要手工把 Job status 改成 succeeded。如必须清除 broker message，使用应用的 queue-aware cancellation 操作并保留逐队列 purge 结果；直接 SQL 删除需要事故记录和 Job reconciliation。

## 认证事故

- 改密通过 `password_changed_at` 使旧 access/refresh token 失效。
- 登出把当前 access token 和可用 refresh token JTI 写入 MySQL blacklist。
- 轮换 `JWT_SECRET_KEY` 会立即使全部 token 失效；按全 session logout 协调。
- 疑似数据库泄露超出应用层审计日志保证。保留外部数据库、代理、主机和备份日志。

不要在公网通过关闭 Secure cookie 修复 refresh。首先验证部署是否真实终止 TLS；当前 Compose 没有。

## 容量与保留

仓库没有经过测量的生产容量声明。MySQL 连接规划需计入 API worker、每个 Celery parent/child、Kombu/result backend、migration 和操作员 session。SQL broker 吞吐、ODA CPU/内存、Excel 工作簿大小、MinIO 带宽和 Nginx 上传并发都需要负载测试。

Celery result 在 24 小时后过期，业务 Job/JobStep、audit、file 和对象字节不会按时间自动删除。对象侧回收已有手动工具 `bash scripts/db.sh reap-storage`（软删除对象 + 孤儿，见 database.md §6.5）；磁盘水位可经 `GET /api/v1/system/infrastructure` 的 `capacity` 观察，Local 使用实际文件系统可用量，MinIO 使用集群原始总量/空闲量指标。达到 warning 时应主动完成终态批次完整备份与整批释放；critical 或 unknown 时先停止新增大批次并核对存储，不把 unknown 当成 0%。

## 发布与回滚检查表

1. 记录 Git revision、迁移 head、image/digest、flag 和依赖版本。
2. 通过文档、backend、Stage、migration、infrastructure、frontend 和 browser 门禁。
3. 备份并恢复测试 MySQL 与对象存储。
4. 迁移不向后兼容时在维护窗口部署。
5. 用真实样本验证 Nginx -> API -> MySQL -> Celery -> storage -> signed download。
6. 验证未授权访问、retry attempt 隔离、SSE reconnect 和 storage degradation。
7. 只有明确 schema 兼容时才回滚应用代码；禁止临时发挥执行生产 downgrade。

TLS、自动备份、metrics/alerts、集中日志和已记录 RPO/RTO，仍是公网或业务关键生产使用的发布阻断项。
