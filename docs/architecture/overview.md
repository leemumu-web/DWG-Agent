# 架构

## 系统上下文

DWG-Agent 是面向内部 CAD/Excel 接入、异步处理、结果复核和审计的操作平台。Nginx 提供 React SPA 并代理请求；FastAPI 负责校验和授权；MySQL 是权威状态；Celery 执行长任务；Local FS 或 MinIO 保存字节。

```text
Browser
  -> Nginx :8080 本地 / :80 Compose 宿主
     -> React SPA
     -> FastAPI :8010 本地 / backend-api:8010 Compose
        -> MySQL 业务 + Celery runtime 表
        -> LocalStorage 或 MinIO
Celery workers（无入站监听端口）
  -> MySQL + storage + processing Stages
```

## 仓库全局规划

仓库按“稳定入口—应用装配—技术平台—业务 owner—独立算法—部署设施—外部执行合同—验证证据”组织。目录名不是能力完成证明，真实状态必须同时查看实现状态、公开合同和验证证据。

| 分区 | 当前真实职责 | 进入/调用规则 | 能力状态依据 |
|---|---|---|---|
| `frontend/src/app` | Router、Provider、应用壳 | 只经 feature `index.ts` 装配 | production build + 前端合同/E2E |
| `frontend/src/shared` | HTTP、认证会话、通用 UI/样式 | 不得反向依赖 feature | Node 架构检查 |
| `frontend/src/features/*` | 11 个页面/API/type/组件 owner | 跨 feature 只经公共入口 | 后端合同 + Playwright |
| `backend/app/bootstrap` | FastAPI、router、model/task registry、seed | 唯一 composition root | OpenAPI/ORM/Celery 快照 |
| `backend/app/platform` | config、database、http、messaging、observability、security、storage seam | 不得导入业务 module | AST 边界测试 |
| `backend/app/modules/*` | 业务 route/model/schema/use case/task 与公开 interface | 跨域只经目标 `interface.py` | 表/operation/task 唯一 owner |
| `backend/app/integrations` | 将来真正跨产品、跨域共享的 adapter 边界 | 当前无可执行 adapter | automation capability contract |
| `Stages/*` | 独立版本 CAD/Excel/分类算法包 | 由领域 adapter/worker 调用，不拥有平台权限 | 各 Stage 自有测试 + 真实样本验收 |
| `infra/*` | gateway、database、storage、messaging target、operations、verification | 配置必须与 Compose 现实一致 | Compose/Nginx/infra verifier |
| `scripts/*` | 稳定操作 facade 与分类实现 | 根命令稳定，implementation 进入 `lib/cad/docs/storage/windows` | Shell/脚本回归 + 真实 status |
| `windows/*`、`agents/*` | 外部 Node/CAM/Agent 交接合同 | 核心执行仍留白 | `placeholder` / `external` 状态 |
| `backend/tests/*`、`frontend/tests/e2e/*` | 与生产 owner 镜像的回归证据 | support 不放业务断言 | pytest/Playwright 收集与执行 |
| `docs/*` | 架构、参考、操作与验证事实 | 生成内容与人工结论分别维护 | docs checker + 分区 README 门禁 |

每个维护分区的就地 `README.md` 必须回答：现有文件/接口或任务是什么、业务如何进入并输出什么、依赖哪个 owner、不能负责什么、哪些能力仍未实现。全局 owner 和目标差距由[模块目录](module-catalog.md)、[追溯矩阵](traceability.md)与[实现状态](implementation-status.md)统一说明；局部 README 不得自行改变状态口径。

运行时没有 Redis/Valkey。token 吊销、密码变更检查、Agent memory、Job 进度、SSE 快照、broker message 和 task result 均使用 MySQL。

## 部署现实

| 属性 | 本地 | Compose |
|---|---|---|
| 用户入口 | Vite `5173` 或 Nginx `8080` | Nginx 宿主 `80` |
| API | `127.0.0.1:8010` | 内部 `backend-api:8010` |
| 存储 | 默认 local，MinIO 可选 | 内部 MinIO |
| TLS | 无 | 无；Compose 只发布 `${HTTP_PORT:-80}:8080`，不发布 443 |
| 运行时文档 | development/debug 时可用 | production settings 下关闭 |

Compose 在网络隔离、非 root backend/frontend、健康依赖和持久卷方面接近生产形态，但没有 TLS、secret manager、自动离机备份、可观测栈、滚动多副本部署或已测灾难恢复，不能标记为完整生产平台。

## 归属边界

| 层 | 负责 | 禁止负责 |
|---|---|---|
| Nginx | 入口、SPA fallback、代理限制、SSE 传输设置 | 业务授权或当前 TLS 声明 |
| 前端 | 工作流 UX、有限重试、query cache、下载编排 | 最终权限裁决或持久状态 |
| Module route/dependency | HTTP 校验、auth context、envelope | 重复 domain transaction |
| Module application/service | transaction、permission、状态转换、storage compensation | UI 状态或 broker-specific 业务事实 |
| Worker/task | attempt 领取和 Stage 编排 | 无条件状态写入或内存 fallback |
| MySQL | 业务事实、audit row、Celery broker/result | 对象字节或高吞吐 broker 承诺 |
| Storage | 由 bucket/key 标识的不透明字节 | 用户/项目访问规则 |
| Stage | 确定性 CAD/Excel 转换 | 平台 auth、Job ownership 或 public error |

后端代码按三个方向分层：`app/bootstrap` 是唯一 composition root，负责 FastAPI、HTTP router、seed、模型与 Celery 任务装配；`app/platform` 只提供配置、数据库、HTTP、消息、日志、token 与 Local/MinIO 技术 seam；`app/modules` 拥有业务规则和数据。平台层有 AST 门禁禁止导入业务模块，其他业务模块只能经目标模块的 `interface.py` 使用能力。

前端采用同一领域词汇做纵向切片：`src/app` 只装配 Router、Provider 和应用壳层，`src/shared` 只放无业务归属的 HTTP/认证/错误边界/通用 UI，`src/features` 下的 11 个功能目录共同拥有页面、API、类型、领域 hook 和组件。跨功能调用只能经过目标目录的 `index.ts`，shared 不得反向依赖 feature；Node 架构门禁在每次 production build 前拒绝旧横向目录和私有深层导入。URL、请求字段、React Query 行为与 FastAPI 授权边界不因目录迁移改变。

identity 已集中 `/auth`、`/users`、`/roles`、六张 RBAC/token 表和认证/用户逻辑；projects 已集中 `/projects`、`/drawings`、四张目录表、成员权限和版本服务；files 已集中 `/files`、四张登记/流转/扫描事实表、项目范围访问、登记、导出和补偿；jobs 已集中 `/jobs`、`/results`、`/reviews`、四张任务/结果/复核表、attempt 状态机、投递补偿、当前状态 SSE 和 stale 恢复。CAD 转换/预览、Steel DXF 分类、Excel Final 和生产工作流分别进入 `cad_processing`、`dxf_classification`、`excel_processing` 与 `workflows`。operations 进一步按 audit、daily archive、data catalog、storage reconciliation、control plane 分层；automation 把已交付的三张表/会话记忆/API 与未实现执行契约分开。跨域调用只经各模块 `interface.py`；旧 `api/models/schemas/services/workers` 横向业务源码和空 Agent/MCP/ZWCAD 适配器已经退出。HTTP method/path/function 集合、表名与权限结果由机器契约锁定。Local/MinIO adapter 及其选择/健康仍是 platform seam，不导入 ORM 或文件权限；Celery platform 只提供通用 callback seam，由 bootstrap 注册 Job 恢复和控制平面 observer，不反向导入业务模块。

## 同步请求路径

```text
Browser -> Nginx -> FastAPI dependency auth -> service -> MySQL -> envelope response
```

列表端点在 SQL 中执行访问过滤、`COUNT(*)`、稳定排序和 `LIMIT/OFFSET`。UI guard 和 Nginx location 不替代 API 检查。生产未处理错误只在服务端记录，并返回通用 envelope。

## 工作流编排路径

```text
项目成员
  -> WorkflowRun
     -> 有序 WorkflowStageRuns
        -> 可选绑定 Job(job_id, attempt)
        -> 版本化 WorkflowArtifacts(file/result)
```

工作流是项目范围内的薄编排层，不是另一套队列或存储。兼容的 `excel_delivery`、`file_delivery` 之外，`linux_production` 提供从输入冻结、DXF 分类分流到交付归档的九阶段服务器框架。Job/JobStep 仍是执行事实源，File/AnalysisResult 仍是产物事实源；工作流只绑定匹配 attempt 并保存引用。

人工输入的当前权威契约是多个 DWG 加恰好一个 Excel，DXF 由服务器转换产生。工作流登记会
重读对象并复核大小、摘要和真实格式；转换同步只接受绑定 attempt 的服务器派生 DXF；冻结
后 files 删除保护经工作流公开接口查询不可变引用。早期结构图中的人工 DXF 上传文字已被此
后续确认规则取代。

公开 route 已接通 Steel DXF Classifier 与唯一 `excel_stage1` Job，按工作流/阶段幂等创建、commit 后投递、详情查询同步 Job 并幂等挂接结果产物。Excel 阶段只读取冻结清单的 `source_excel`，浏览器不能另选文件；DXF→Excel 只保留独立工具。分类分流逐图保存 MySQL 来源/输出关系，并把命名规范化 DXF、JSON 报告和 CSV 清单存入 MinIO；图纸拆板、CAM 工作包、Windows Node Agent/SinoCAM 和结果接纳保持带输入输出契约的 placeholder/external 阶段。详见[Linux 生产工作流框架](workflow.md)。

Excel Final 的创建边界由客户端 `Idempotency-Key`、端点作用域后的 `jobs.request_key` 和 `(created_by, task_type, request_key)` 唯一约束组成。普通重放返回原 Job 且不重复 dispatch；唯一键竞态在数据库层收敛；同键不同参数被拒绝。MySQL `REPEATABLE READ` 下，唯一键竞争失败者回滚 savepoint 后必须用锁定 current read 读取胜者，不能复用竞争前已经固定的 consistent snapshot。`upload-and-process` 先以同一逻辑键复用 files transfer saga 与 StoredFile，再创建或复用 Job，因此响应丢失不会制造第二个对象。执行成功同时登记结果对象、File、AnalysisResult 和 batch/part/component；失败、取消或 stale 恢复通过 Excel 公开清理接口移除本 attempt 的临时关系行。失败 Job 的业务重试仍在原 Job 上递增 attempt，不与请求重放混用。这是单文件 Excel Final 切片；跨全部图纸的最终屏障、左右进合并和自动汇总仍未实现。

## 异步请求路径

```text
POST
  -> feature flag + input + access validation
  -> INSERT Job(queued, attempt=N) + COMMIT
  -> 向 MySQL SQL transport 发布 (job_id, attempt)
  -> worker conditional claim
  -> attempt-scoped JobSteps 和 progress
  -> source bytes -> Stage -> result bytes
  -> files + AnalysisResult + optional domain rows
  -> conditional terminal update
```

投递补偿只更新仍 queued 的 attempt。重试递增 attempt；旧 message/worker 不能领取或更新新 attempt。worker 启动恢复处理长期 stale running Job，但不是持续 heartbeat。

## SSE 路径

原生 EventSource 携带短期 HttpOnly `dwg_sse_token` cookie。FastAPI 在 streaming 前认证并授权 Job，轮询 MySQL，发送当前 attempt 的 snapshot/progress/terminal event。重连以新的权威快照开始；不存在 event-ID replay 或 Pub/Sub 保证。

Nginx 关闭 SSE buffering/cache 并延长 read/send timeout。前端在短暂 CONNECTING 时交给浏览器重连，在 terminal event 或 hard close 时关闭。

## 下载路径

```text
Bearer request -> permission check -> 300-second HMAC path
Bearer download -> permission + expiry + signature check -> storage stream
```

前端单文件最多尝试两次，每次重新签名。只重试网络、403、408、429 和 5xx。ZIP 下载使用认证 POST stream，不共享该重签名循环。数据库 SHA-256 是完整性依据。

## 存储一致性

MySQL 的 `files` 是业务登记事实源，Local FS/MinIO 是字节事实源；两者不能共享单一 ACID 事务，因此由 `file_transfers` saga 协调：

```text
独立短事务写 prepared/in_progress 意图
  -> 写对象
  -> 同一业务事务写 files/结果/audit，并结算 succeeded
  -> 业务回滚时删除新对象
       -> 删除成功: failed
       -> 删除失败: compensation_required
```

上传、ZIP 每个有效条目、worker 生成文件和 DXF SVG 预览缓存均走该路径。预览缓存以源文件 ID、SHA-256 前缀和 renderer 版本分组，MySQL 登记 SVG 文件，MinIO/Local 保存字节；缓存命中仍由对象 `stat` 验证。锁内二次命中写 `preview_cache_reuse`，明确表示零字节复用而非生成。源 DXF 软删除在同一数据库事务中把对应预览登记标为 deleted 并写 `preview_invalidate`；物理 SVG 仍遵循保留期/对账/purge。下载/ZIP/预览出库在响应流开始前登记 outbound 意图，iterator 正常耗尽、客户端中断或存储读取失败后按实际字节独立结算。软删除只更新登记并保留对象；恢复会清空 `deleted_at`。永久清理先锁定 finding/关联登记并重检对象，再删除对象；只有元数据提交成功后才把独立流水结算为成功，提交失败留下 `compensation_required`，不声称原子回滚了不可恢复字节。

local 后端 `put_fileobj` 经临时文件、`fsync` 和原子 `os.replace` 落盘。MinIO/local 都实现 stat、exists 和游标分页清单。`operations/storage_reconciliation` 的 report-queue task 异步生成 files 域拥有的 `storage_scan_runs` 与异常 `storage_scan_findings`，分类为对象缺失、未登记对象、大小不符和软删除对象保留。管理员可在七页签数据控制台执行带签名预检 token、5 分钟有效期、实时摘要重检、批量数量/字节上限和幂等键的四种处置；审计员只有读取与预检权限。`reap-storage` 仍用于保留期回收和脚本化维护，不替代扫描/处置账本。

数据控制台现有七个页签。每日归档与一致性处置使用不同安全语义：归档按 `Asia/Shanghai` 自然日生成带签名的冻结清单，maintenance worker 流式读取源对象，在 `dwg-reports/daily-archives/YYYY-MM-DD/` 新增 ZIP 和 JSON manifest，并把两者登记到 `files`/`file_transfers`；不移动或删除源对象。历史归档前缀自动排除，避免递归打包。相同清单复用成功结果，活动任务复用运行记录；源文件状态、摘要或对象可读性变化时整次失败，不返回貌似完整的 ZIP。完整操作边界见[运维指南](../guides/operations.md#数据控制台运行手册)。

## MySQL 与 Celery

broker 为 `sqla+mysql+pymysql://...`，result backend 为 `db+mysql+pymysql://...`，都从有效应用 MySQL DSN 派生。Celery engine 使用 `READ COMMITTED`、有界 pool、pre-ping、LIFO 和 recycle。`kombu_message(queue_id, timestamp, id, visible)` 按队列缩小 message claim。

SQL transport 没有 fanout remote control。worker 健康使用 Celery PID 加 `worker_ready` marker，不使用 `inspect`。控制平面额外将 Celery lifecycle/task signal 最佳努力写入 `worker_runtimes` 和 `control_plane_events`；其 `last_seen_at` 是活动观测而非 broker lease，超过阈值才在管理端显示 stale。管理员可在“运行与通信”查看 SQL broker ready 行数、Worker、运维消息和事件。result row 在 24 小时后过期；业务 Job 历史没有自动保留策略。

`dispatch` 是已启动可观察的 queue/process 身份预留；`maintenance` 已承载人工 stale Job 恢复和每日归档两个有界任务。它们不等同于 durable outbox 或 Celery Beat。未来 Windows Node Agent 的 HTTP 注册、heartbeat、事件 envelope 已通过 `/api/v1/control-plane/contracts/windows-node-agent` 发布 draft，但认证、lease fencing、Named Pipe CAD runner 和命令投递均未实现。

`maintenance` 任务都由已认证请求显式提交：管理员经 `POST /api/v1/control-plane/maintenance/reconcile-stale-jobs` 恢复超过 stale timeout 的 running Job；每日归档经 data-admin 预检/提交端点冻结文件 ID 后异步生成新对象。两者的完成/失败都写控制平面事件。当前没有周期调度，不启用 Celery Beat；stale 恢复不扫描对象，每日归档只新增产物而不修改源对象。

前端以 React Query 统一处理读取请求：仅对网络或 5xx 进行有界重试，认证/校验/权限类 4xx 不会无意义重放。应用级错误边界保护单一路由渲染异常，浏览器 offline 提示保留当前表单与表格状态；各业务页面仍负责展示其 API 的具体错误和恢复动作。

worker 启动时的恢复是分层的。`task_acks_late` 配合 `task_reject_on_worker_lost`，在 prefork child 死亡但 worker 父进程存活时重新投递任务。由于 Kombu 在消息被 reserve 的瞬间（child ack 之前）就把 `kombu_message` 行标为 `visible=False`，启动时的 broker 清理只删除 timestamp 早于 `CELERY_STALE_JOB_TIMEOUT_SECONDS` 两倍的 invisible 行，因此绝不会销毁仍在运行或等待重投的任务消息。当整个 worker 或主机死亡时，SQL transport 无法恢复投递；`reconcile_stale_running_jobs` 是权威兜底：把 `updated_at` 早于 stale 超时的 `running` Job 标记为失败，以便重试。

空库首次启动时，Kombu channel 建表事务必须显式 commit/close 后才能创建 `kombu_message(queue_id, timestamp, id, visible)` 索引；否则第一个连接持有 metadata lock、第二个连接等待 DDL，会让 worker 永久停在 ready 之前。2026-07-12 的空卷 Compose 回归覆盖了这一顺序。


## 处理边界

| 管线 | 队列 | 实现 | 交付限制 |
|---|---|---|---|
| framework smoke | `report` | 可执行 stub result | 不是 report Agent |
| DWG -> DXF | `dxf` | ODA service/task | flag 关闭；外部 ODA/runtime/样本兼容性 |
| DXF -> DWG | `dxf2dwg` | ODA service/task | flag 关闭；外部 ODA/runtime/样本兼容性 |
| DXF -> Excel | `dxf2excel` | service/task 与父仓库跟踪 Stage | flag 关闭；大规模验证 corpus 不随源码分发 |
| Steel DXF classification | `dxf_classification` | 1.2.0 Stage、task、两张账本表、显式逐图语义、Workflow 分类目录与 DXF-only 下载；DXF/JSON/CSV 均登记 | flag 关闭；分类准确率需真实样本，且不等于拆板 |
| Excel Final | `excel_final` | 隔离 Stage + 关系化导入 | flag 关闭；需要内容 schema 和手册库 |
| Agent | `agent` | 三张表、会话 memory、API 与 capability contract | 无注册 task/执行器；queue/process 只能是 contract-only |
| Windows CAD | `cad` | 配置与外部 Node/CAM/协议合同 | 无 task、worker、service 或 Compose node |

步骤名、格式、输出和启用检查见[Linux 生产工作流](workflow.md)。

## 安全模型

全局角色为 `super_admin/admin/engineer/reviewer/operator/viewer/auditor`；项目成员增加 owner/engineer/reviewer/viewer 范围。管理员访问是明确规则。文件读取要求管理员、上传者或关联活跃项目成员。结果和复核继承 Job 边界；无项目 Job 仅管理员或创建者可读。

access token 位于 `sessionStorage`，因此同源 XSS 仍是威胁。refresh/SSE token 是 HttpOnly、SameSite=Lax cookie。公网部署需要真实 TLS 和 Secure cookie，而当前 Compose 尚未交付。

## 健康与可观测性

- `/health` 仅 liveness。
- `/health/ready` 分别报告 MySQL 和已配置 storage，任一失败返回 503。
- 通用 worker 健康只证明 broker 连接，不证明 pipeline dependency。
- 本地日志使用 `/tmp`；Compose 使用 container stdout/stderr。
- metrics、distributed tracing、central logging、alerting、SLO 和自动保留均未实现。

## 架构约束

- MySQL/storage 失败时禁止增加进程内正确性 fallback。
- 没有显式迁移设计时，禁止让 broker 凭据脱离权威 MySQL DSN。
- 尚未实现的 Agent/CAD/拆板/Windows 能力保持 flag 关闭，但保留目标接口、输入输出和错误契约；不得把占位写成完成，也不得在重构中删除目标边界。
- 生产 workflow 只能把唯一 `excel_stage1` 描述为已接线 Excel 自动阶段；独立 DXF→Excel 不能写成 workflow 阶段，placeholder/external 阶段在真实实现与验证前禁止描述为生产闭环。
- 修复 `Stages/dxf2excel` 归属前，禁止声称 clean-clone/Docker 可复现。
- Nginx 有已测试 TLS listener 和证书生命周期前，禁止声称 HTTPS。
- worker 规模超过有界 SQL transport 时评估 RabbitMQ，同时保持 MySQL 为业务事实。
