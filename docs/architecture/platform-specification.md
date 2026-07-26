# DWG-Agent 企业平台技术规范

> 本规范已按仓库领域分层迁入架构文档。它描述必须长期保持的产品与运行边界；当前实现进度以[实现状态](implementation-status.md)和[当前验证证据](../verification/current.md)为准。

**版本：2.3**

**状态：实现同步规范；审计基线为 2026-07-21 的 `main` 工作树**

**适用范围：React、Nginx、FastAPI、MySQL、Celery、Local/MinIO 存储及仓库内 CAD/Excel Stage**

本文是平台设计与实现边界的规范性说明。实际端点由 FastAPI OpenAPI 生成，数据库结构由 SQLAlchemy 模型和 Alembic 迁移定义，部署行为由 `compose.yaml`、Dockerfile、Nginx 配置和脚本定义。文档与这些事实冲突时，应先修复实现或明确记录偏差，不能用文档掩盖差异。

## 1. 目标、角色与当前边界

平台面向企业内部操作员、工程师、复核员和审计员，提供 CAD/Excel 文件接入、异步处理、任务追踪、结果下载、复核和审计。

当前交付边界：

- 用户、角色、权限、项目成员、文件、图纸版本、任务、结果、复核与审计。
- MySQL 权威状态、Celery SQL broker/result、本地或 MinIO 对象存储。
- report/maintenance 框架任务及受功能开关保护的确定性 CAD、分类和 Excel 管线。
- 项目级工作流、多个 DWG + 单个 Excel 的生产输入、服务器 DWG→DXF、输入冻结、Steel DXF Classifier 1.2.0 分类分流、阶段推进、审计 API 和生产流程页面。
- 前端登录刷新、分页、轮询/SSE、重试和签名下载交互。

目标架构中已有契约但当前尚未实现：

- Agent 推理任务体、LangGraph/MCP 工具执行闭环。
- 图纸自动/人工拆板业务校验、CAM 工作包、Windows Node Agent、CAM Runner、SinoCAM Adapter 与正式结果接纳。
- RabbitMQ、事务 Outbox 与 Celery Beat；当前仍使用 MySQL SQLAlchemy transport 和显式维护请求。
- 已完成的公网 TLS、证书轮换、WAF、集中监控告警、日志汇聚、自动备份或灾难恢复。
- 任意规模的 Celery 横向扩容保证或高吞吐 broker SLA。

## 2. 设计原则

1. **MySQL 是唯一业务事实源。** 用户状态、token 吊销、密码变更、Agent memory、Job、JobStep、进度快照和审计均在 MySQL。
2. **Redis/Valkey 不在当前运行时。** 不存在 Redis 缓存、session、fallback、Pub/Sub、broker 或 result backend。
3. **对象字节与元数据分离。** Local/MinIO 保存字节；MySQL 保存 bucket、key、大小、摘要、所有者和关联关系。
4. **长任务越过 Celery 边界。** API 负责校验、持久化和投递；worker 负责领取、执行和条件落库。
5. **状态写入必须抗陈旧执行。** 所有 worker 关键更新都匹配当前 status 和 attempt。
6. **权限在服务端裁决。** Nginx、React 菜单、签名 URL 和对象 key 都不是授权边界。
7. **降级不能破坏正确性。** MySQL、broker 或存储不可用时显式失败，不允许退回进程内内存状态。
8. **能力声明区分四个维度。** 代码存在、默认启用、依赖可得和端到端验证必须分别说明。

## 3. 物理拓扑与网络

```text
Browser
  -> Nginx
     -> React static assets
     -> /api/v1, /health, development docs -> FastAPI
FastAPI
  -> MySQL: 25 model tables + Alembic + on-demand Celery runtime tables
  -> LocalStorage or MinIO
Celery worker
  -> MySQL broker/result + business state
  -> LocalStorage or MinIO
  -> Stage code / ODA child process
```

| 场景 | 入口 | API | 依赖暴露 |
|---|---|---|---|
| 本地 | Vite `5173` 或 Nginx `8080` | `127.0.0.1:8010` | MySQL `3306`；MinIO 可选 |
| Compose | 宿主 `80` -> Nginx 容器 `8080` | 内部 `backend-api:8010` | MySQL/MinIO 仅 internal network |

Compose 当前只发布 `${HTTP_PORT:-80}:8080`，不发布 443，也没有 Nginx TLS listener 或证书配置。公网部署前必须补齐 TLS termination、证书/私钥管理、HTTP 到 HTTPS 跳转、HSTS、证书轮换和真实握手测试。

## 4. 组件职责

| 组件 | 负责 | 不负责 |
|---|---|---|
| Nginx | 单一入口、SPA、代理、限流、SSE buffering 控制、网关错误 | 用户/项目授权、当前 TLS 终止 |
| React | 操作流程、查询缓存、有限重试、下载触发 | 权限最终裁决、持久业务状态 |
| FastAPI route | HTTP schema、dependency auth、envelope | 长耗时转换、跨层业务规则复制 |
| Service | 事务编排、权限复用、状态机和存储补偿 | HTTP 展示和 UI 状态 |
| Celery task | 调用 service、返回执行摘要 | 自建业务状态或绕过 attempt 条件 |
| MySQL | 业务事实、broker/result、迁移版本 | 大对象字节、高吞吐消息语义 |
| Local/MinIO | 不透明对象字节 | 用户身份、项目关系和授权 |
| Stage | 确定性 CAD/Excel 领域处理 | 平台认证、任务生命周期、对象授权 |

## 5. 配置与环境

- Pydantic Settings 从当前进程工作目录的 `.env` 读取；本地根脚本同时维护根 `.env` 和 `backend/.env`，数据库字段必须一致。
- `DATABASE_URL` 是可选兼容覆盖；未设置时由 `MYSQL_*` 生成 DSN。运行时应使用 MySQL；SQLite 只允许测试 fixture 显式覆盖。
- Celery broker/result 始终从有效 MySQL DSN 派生，不提供独立 `CELERY_BROKER_URL` 配置口径。
- 功能开关默认全部关闭：`AGENT_ENABLED`、`REMNANT_INVENTORY_ENABLED`、`DXF_PIPELINE_ENABLED`、`DXF2DWG_PIPELINE_ENABLED`、`DXF2EXCEL_PIPELINE_ENABLED`、`DXF_CLASSIFICATION_PIPELINE_ENABLED`、`DXF_SPLIT_PIPELINE_ENABLED`、`EXCEL_FINAL_PIPELINE_ENABLED`、`CAD_WORKER_ENABLED`。
- `APP_ENV=production` 且 `DEBUG=false` 时禁用 OpenAPI、Swagger 和 ReDoc，并使用通用 500 消息。
- `REFRESH_COOKIE_SECURE` 默认随 `APP_ENV`；公网只能使用 TLS + Secure cookie。HTTP 私网覆盖为 `false` 是风险接受，不是推荐生产配置。

完整字段、默认值和敏感性见[配置参考](../reference/configuration.md)。

## 6. 数据库与连接

- 当前 Alembic head 为 `b7e2c9a4d610`，SQLAlchemy/Alembic 管理 47 张模型表。
- 空迁移 schema 加 `alembic_version` 为 48 张；Celery/Kombu 按需创建 8 张 runtime 表，全部存在时最多 56 张。不能把 56 当成每个时刻的固定表数；Celery 表不由 Alembic 所有。
- API 进程池由 `DB_POOL_SIZE=2`、`DB_POOL_MAX_OVERFLOW=2`、`DB_POOL_TIMEOUT_SECONDS=30` 和 `DB_POOL_RECYCLE_SECONDS=3600` 控制。
- Celery 自有 engine 每进程使用更小的 pool，并启用 `pool_pre_ping`、LIFO、recycle 和 `READ COMMITTED`。
- `kombu_message` 需要 `(queue_id, timestamp, id, visible)` 索引，降低跨队列扫描和锁范围。
- `drawings` 与 `drawing_versions` 存在循环 FK；`alembic check` 当前会产生 SQLAlchemy 排序 warning，但无待生成操作。迁移必须显式处理 FK 创建/删除顺序。
- FastAPI lifespan 中的 `init_db()` 异常会被记录后继续启动；Compose Docker CMD 在 Gunicorn 前显式执行迁移和 seed。因此 readiness 而非“进程存在”才表示数据库可用。

## 7. 任务状态机与 attempt

```text
queued -> running -> succeeded
                  -> failed
queued/running    -> cancelled
failed/cancelled  -> retry -> queued (attempt + 1)
```

强制规则：

- Celery 消息携带 `(job_id, attempt)`；兼容的单参数旧消息只代表 attempt 1。
- worker 通过 `id + queued + expected attempt` 原子领取。
- progress、完成、失败、取消、投递补偿和 stale recovery 均匹配 status + attempt。
- `job_steps.attempt` 保存完整世代历史；默认 SSE 只展示当前世代。
- API commit Job 后再投递 Celery。投递失败只能补偿仍为 queued 的同一 attempt，不能覆盖已被 worker 领取的任务。
- `CELERY_STALE_JOB_TIMEOUT_SECONDS` 超时的 running Job 在 worker 启动时被条件标记 `CELERY_WORKER_LOST`；这是恢复机制，不是精确的 worker 心跳。
- SQL transport 的整个 worker/主机丢失不能依赖消息重新投递完全恢复，必须结合 stale recovery 和人工重试。

## 7.1 通用工作流状态机

`workflow_runs`、`workflow_stage_runs` 与 `workflow_artifacts` 表达项目级人工流程、顺序阶段和 file/result 引用。当前模板为 `excel_delivery` 与 `file_delivery`，公开 API 支持创建、列表、详情、启动、人工完成阶段和取消。

强制边界：

- 工作流必须属于项目，写操作只允许项目 owner/engineer，其他项目成员只读；
- `WorkflowRun` 是业务编排元数据，`Job`/`JobStep` 仍是异步执行事实；
- 公开 route 已为已实现阶段绑定 `(job_id, job_attempt)`、同步匹配 attempt 的状态并挂接 file/result；
- `source_intake` 分步接收一个 `.xls`/`.xlsx` 单文件与一个 DWG 文件夹，人工 DXF 被拒绝；混合文件夹确认后只上传 DWG，DXF 必须由服务器转换并登记后才能冻结；
- 通用 completion 不能绕过输入冻结或自动阶段执行；placeholder/external 阶段必须提交符合契约的交接产物；
- 取消工作流会协调活动 Job，但外部子进程的强制终止能力仍取决于具体 Stage；
- 拆板虽已形成默认关闭的服务器纵向切片，但真实 MinIO/MySQL、代表性 BH/BOX、人工复核和 Excel 交接验收尚未完成；CAM、Windows/SinoCAM、结果接纳和确定性交付清单完成前，整体不得称为生产自动闭环。

## 8. Celery 队列边界

| 队列 | task 状态 | 默认部署 |
|---|---|---|
| `report` | `run_stub_job` 已实现，用于框架任务 | Compose core、本地脚本 |
| `dxf` | DWG -> DXF 已实现 | `workers` profile、本地脚本 |
| `dxf2dwg` | DXF -> DWG 已实现 | `workers` profile、本地脚本 |
| `dxf2excel` | DXF -> Excel task 与普通跟踪 Stage 已实现 | `workers` profile、本地脚本 |
| `dxf_classification` | Steel DXF Classifier 1.2.0 task 已实现 | `workers` profile、本地脚本；flag 默认关闭 |
| `dxf_split` | Steel DXF Split 1.5.2 整批 task 已实现 | `workers` profile、本地脚本；无入站端口，flag 默认关闭 |
| `excel_final` | Excel Final task 已实现 | `workers` profile、本地脚本 |
| `remnant_convert` / `remnant_parse` | 余料转换与解析 task 已实现 | `workers` profile |
| `agent` | module 仅占位，无 Celery task | Compose 有占位 worker；本地脚本不启动 |
| `cad` | module 仅占位，无 Celery task | 无 Compose/local worker |

SQLAlchemy transport 不支持 fanout remote control；不得用 `celery inspect` 作为健康检查。Compose worker ready 条件是 PID 1 命令行含 Celery 且 `worker_ready` 信号已写 `/tmp/dwg-celery-ready`。这只证明进程已连接 broker，不证明特定业务 task 存在或依赖可用。

## 9. 存储与一致性

`STORAGE_BACKEND` 只能是 `local` 或 `minio`。默认 bucket/key 口径包括：

- `dwg-original`、`dwg-derived`
- `dxf-original`、`dxf-derived`
- `dwg-reports`、`dwg-temp`

上传边界：

- 允许 `.dwg/.dxf/.zip/.xls/.xlsx`，但扩展名只决定入口，不能证明业务内容有效。
- DWG 必须有支持的 AC header 且至少 1024 bytes。
- 流式限制文件大小并计算 SHA-256/MD5；ZIP 必须限制 entry 数、总解压大小和路径穿越。
- 对象先写、数据库后 commit 时，SQLAlchemy session 记录待补偿对象；rollback 删除对象，commit 清除记录。
- 存储写失败必须返回稳定错误，不允许改写到未配置的本地 fallback。

下载闭环：

1. Bearer 鉴权请求短期签名 URL。
2. 下载端点再次校验 Bearer、当前资源权限、expires 和 HMAC；签名本身不是授权。
3. 前端仅对网络错误、403、408、429 和 5xx 再试一次，并在每次尝试前重新签名。
4. `files.sha256` 是下载完整性依据；对象恢复后必须再次比对。
5. 普通单文件下载有重签名逻辑；ZIP 下载走 POST blob 流程，不复用该重签名循环。

## 10. 身份、认证与授权

- 密码由 `pwdlib` 推荐 Argon2id 参数哈希；不存在用户和密码错误都执行一次验证以降低用户枚举时序差异。
- access token 默认 30 分钟；refresh cookie 默认 14 天，HttpOnly、SameSite=Lax、path 为 `/api/v1/auth`。
- SSE cookie 使用 access token，HttpOnly、SameSite=Lax、path 为 `/api/v1/workflows/jobs`。
- access/refresh token 类型严格区分；JTI 吊销与 `password_changed_at` 检查直接查询 MySQL。
- 前端 access token 和用户快照存于 `sessionStorage`；这降低跨 tab 持久化，但不能防止同源 XSS 读取 access token。
- 全局角色为 `super_admin/admin/engineer/reviewer/operator/viewer/auditor`；项目成员角色为 owner/engineer/reviewer/viewer。
- 文件读取允许全局项目管理员、上传者或关联活跃项目成员。删除仅允许上传者或管理员。
- Result 详情、下载 URL 和复核继承父 Job；无项目 Job 仅管理员和创建者可访问。
- Agent run 启用后仍按创建者、管理员或关联项目成员隔离。

## 11. API 与错误契约

- API 前缀为 `/api/v1`；当前 OpenAPI 为 178 个 path、206 个 operation。
- 成功 envelope 为 `{data, meta}`；分页增加 `{pagination}`，总数来自 SQL `COUNT(*)`。
- 错误 envelope 为 `{error: {code, message, details}, meta}`。
- request ID 接受传入 `X-Request-ID` 或由 API 生成，并写回响应。
- `DEBUG=true` 的未处理 500 响应可能包含异常字符串，只能用于受控开发；生产必须 `DEBUG=false`。
- 运行时交互文档仅在 development 或 debug 模式存在。生产 API 参考应使用仓库生成的 `docs/reference/api.md`。

## 12. SSE

`GET /api/v1/workflows/jobs/{job_id}/events` 在开始 streaming 前执行普通 Job 权限检查。EventSource 使用 HttpOnly cookie，不在 URL 传 access token。服务循环查询 MySQL Job 和当前 attempt steps，发送权威 snapshot、progress 和 terminal event；断线重连重新发送当前快照，不承诺 `Last-Event-ID` 回放。Nginx 对该 location 关闭 buffering/cache 并设置一小时 read/send timeout。

## 13. 处理 Stage

| Stage | 主仓库归属 | 外部依赖 | 可复现性 |
|---|---|---|---|
| `dwg2dxf` | 普通跟踪目录 | ODA、Xvfb/FUSE | 源码和 ODA AppImage 已跟踪；仍需许可与真实样本验证 |
| `dxf2dwg` | 普通跟踪目录 | ODA、Xvfb/FUSE | 同上 |
| `dxf2excel` | 普通跟踪目录 | ezdxf/pandas/openpyxl | 源码与锁文件可从干净 clone 重放；历史外部验证 corpus 不随仓库分发 |
| `steel_dxf_classifier_v1.1.0` | 普通跟踪目录 | ezdxf | 平台锁定 1.2.0 I/O 契约；真实分类准确率仍需代表性样本 |
| `steel_dxf_split_v1.5.2` | 受控运行时源码切片 | ezdxf/shapely/matplotlib/openpyxl | 平台按不可变 CLI 子进程调用；包内发布证据保留，DXF corpus、上游测试和报告不随父仓库分发；BH/BOX 来源合同与中文输出后缀由适配层复核 |
| `excel_final` | 普通跟踪目录 | pandas/openpyxl/xlrd、手册 MySQL | backend 通过隔离子进程调用，不作为包导入 |

Excel Final 接受 Tekla 制表符/空白文本导出，或包含目标钢构清单 schema 的真实工作簿。legacy 二进制 `.xls` 通过锁定的 `xlrd` 读取；文本探测失败必须进入 Excel fallback。子进程 stdout 用结构化 JSON 与 backend 通信，完整 stderr 只进入 worker log。成功后同时写结果对象、`files`/`analysis_results` 和 Excel Final batch/part/component；客户端错误不得包含 traceback、DSN 或主机路径。

## 14. 前端运行语义

- Axios 对 401 只执行一次共享 refresh 请求，避免并发刷新风暴；登录和 refresh 请求自身不循环重试。
- React Query 默认 query retry 为 2 次，指数退避上限 10 秒；这与下载的一次重签名重试是两个独立机制。
- Jobs/转换页面使用定时 refetch；打开 Job 详情时可同时使用 SSE。
- 生产项目工作台以“新建生产项目”为主入口；服务端在同一事务内创建 Project、项目所有者关系及其唯一 `linux_production` Workflow 并启动，随后进入独立工作流详情 URL。详情页按当前阶段完成生产文件夹整批上传、服务器转换、冻结、DXF 分类、整批拆板和冻结 Excel 第一阶段处理；拆板完成后分别提供正式成对结果与本批全部分类原图，未形成结果的单图显示明确原因且不进入正式交接，后续留白阶段只展示契约、交接产物和可恢复错误。
- Stage A3 “图纸拆板与独立校验”卡片标题栏提供分批导出，而不是把入口放在全局产物汇总。四个展示标签只负责 UI；服务端机器类型固定为 `classified_dxf`、`processed_dxf`、`source_excel`、`stage1_excel`，ZIP 一级目录固定为 `原DXF/`、`正常拆板DXF/`、`原Excel/`、`产出Excel/`，叶子文件名不得改写。
- 分批 ZIP 通过不可 seek 的流直接从 Local/MinIO 发往浏览器，不生成服务器临时 ZIP，也不让 Axios 在浏览器内整体缓存 Blob。路径级 HttpOnly 能力只允许访问本次下载 URL；流中断、关闭弹窗或能力过期都不删除源文件。只有服务端出库流水成功、状态变为 `downloaded`，且有写权限的创建者或管理员通过第二次不可恢复确认后，才物理删除所选对象和关联 DXF 预览缓存。
- EventSource 在 CONNECTING 状态交给浏览器自动重连；明确关闭或终态后停止。
- UI 权限守卫只控制显示，不替代 API 授权。

## 15. 健康、恢复与可观测性

- `/health` 只表示 API 进程存活，不探测外部组件。
- `/health/ready` 分别探测 MySQL 和当前 storage，任一失败返回 503；响应只给稳定状态消息。
- 管理员 `GET /api/v1/system/infrastructure` 显示数据库、存储桶对象计数与 MySQL 登记计数、文件目录和 `automated_backup=false`，只用于观察，不是备份保证。
- MinIO 恢复不要求重启 API；命名卷或外部持久存储中的旧对象应仍可读。
- worker 启动时清理过期 result rows、已消费 broker rows并协调 stale Job。
- 本地脚本按 Celery app、queue 和 node name 发现进程，pidfile 丢失时避免重复 worker。
- 当前日志主要写 stdout/container logs 或本地 `/tmp/dwg-agent-*.log`；没有集中日志、指标、告警、追踪或 SLA 实现。

## 16. Compose 与发布边界

核心服务为 `nginx/backend-api/mysql/minio/worker-report`；`workers` profile 增加 `worker-agent/worker-dxf/worker-dxf2dwg/worker-dxf2excel/worker-dxf-classification/worker-dxf-split/worker-excel-final/worker-remnant-convert/worker-remnant-parse/worker-maintenance/worker-dispatch`，总计 16 个 Compose 服务。

- backend 与 worker 共用非 root `appuser` 镜像。
- MySQL 和 MinIO 使用命名卷且不发布宿主端口。
- MinIO 固定 registry digest；MySQL 使用 8.4 tag，未固定 digest。
- backend 在 Gunicorn 前执行 Alembic upgrade 和 seed。
- Docker build 依赖各普通跟踪 Stage 实体源码；`Stages/dxf2excel` 与 `Stages/steel_dxf_split_v1.5.2` 均纳入构建上下文。
- Compose 没有 TLS、证书、监控、备份调度、滚动升级或多副本协调，不应直接标记为完整生产方案。仓库虽提供手工 backup/restore 命令，但没有跨 MySQL/MinIO 原子快照或自动演练。

## 17. 数据保护与审计边界

- `audit_logs` 通过应用 service 追加写入，API 没有更新/删除端点；但数据库没有 append-only trigger、WORM 存储、签名链或独立审计账号。因此它是**应用层追加约定**，不是防 DBA 篡改的不可变审计系统。
- 分批导出的“永久删除”针对对象字节：成功后 `files` 行保留为 `status=deleted, purged_at!=NULL` 的小型墓碑，用于维持 Drawing、Job、输入和拆板账本外键。墓碑中的文件名、大小与哈希不是可恢复副本，reaper 必须跳过；对应 workflow artifact 文件引用会被移除。
- MySQL 和对象存储必须形成一致恢复点；只恢复数据库会留下缺失对象，只恢复对象会留下孤儿。
- `scripts/docker.sh backup/restore` 提供 MySQL + MinIO 的手工单机基线；它不是跨系统原子快照，也没有调度、保留、加密、PITR 或 RPO/RTO 证据。执行环境必须补齐这些能力。
- `hardware_handbook` 只允许由唯一可信 `/home/Creeken/Paper/CAD_research/五金手册.xls` 确定性生成并通过逐值审计；运行时应使用只读账号，Compose 初始化只给应用用户授予该库 `SELECT`。

## 18. 测试与验收

最低静态/隔离门槛：

```bash
make docs-check
cd backend
uv run ruff check app tests ../tests/run_full_verify.py
uv run pytest -q
uv run alembic check
cd ..
cd Stages/dwg2dxf && uv run pytest -q && cd ../..
cd Stages/dxf2dwg && uv run pytest -q && cd ../..
cd Stages/excel_final && uv run pytest -q && cd ../..
bash scripts/db.sh migration-test
bash infra/verification/verify.sh
docker compose config --quiet
cd frontend && npm run build && npx playwright test
```

发布验收必须另外覆盖：

- 空 MySQL/MinIO volume 冷启动到 migration head。
- Nginx -> FastAPI 登录、refresh 和受权业务请求。
- API -> MySQL broker -> Celery -> Stage -> MySQL terminal state。
- 源对象、结果对象、数据库摘要和下载字节一致。
- failed/cancelled Job 的 attempt 递增重试，以及旧消息/旧 worker 拒绝。
- SSE cookie、当前 attempt 快照、断线重连和终态关闭。
- 首次签名下载 403 后重新签名并成功；非重试型 4xx 不重放。
- MinIO 中断时 readiness 503，恢复后对象摘要不变。

任何“已验证”记录都必须带日期、环境和范围，且不能代替代码变化后的重新执行。

## 19. 完成交付标准

| 未完成领域 | 完成所需证据 |
|---|---|
| TLS | 受控 TLS termination、80 跳转、HSTS、浏览器/openssl 握手和续期演练 |
| Linux 生产工作流闭环 | 已实现阶段需真实 MySQL/Celery/MinIO/browser E2E；拆板还需代表性 BH/BOX、人工复核与 Excel 交接验收，CAM、Windows/SinoCAM、结果接纳与交付清单仍需完成实现和故障恢复 |
| 运维 | 指标、告警、集中日志、备份调度、恢复演练、容量和保留策略 |
| RabbitMQ / Outbox / Beat | Compose 服务、持久卷、健康检查、事务投递、重连/恢复测试、周期调度和运行手册 |
| Windows 执行面 | Node Agent 认证与租约、fencing token、CAM Runner/Adapter、命令/结果协议和真实 Windows 故障恢复 |

核心算法尚未实现时只保留 API、schema、输入输出和错误契约，相关 flag 必须保持 false；这些目标能力不得因本轮目录重构被删除，也不得以占位状态计入完成率。

## 20. 文档治理

- 项目只维护 `docs/` 分类目录中的中文文档，不再创建旧双语目录或英文镜像。
- 路由变更后运行 `make docs-generate`；提交前运行 `make docs-check`。
- 组件 README 描述本目录的运行方式和边界；根 README 不复制完整内部算法。
- `third_parts/` 是上游/外部文档，不纳入项目中文化范围；平台只能记录集成边界，不能改写上游历史。
- 历史 Redis 内容只能作为迁移说明出现，不能重新成为当前依赖或 fallback 建议。
- 分支名、端口、迁移 head、路径依赖、功能开关和验证日期属于高漂移事实，修改后必须从仓库重新核对。
