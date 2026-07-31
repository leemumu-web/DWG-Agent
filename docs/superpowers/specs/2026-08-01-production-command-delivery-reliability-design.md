# 生产命令并发与消息可靠投递设计

## 目标

生产链路中的关键按钮必须在弱网、响应丢失、重复点击、多标签页和多个账号并发操作时收敛到同一个权威结果。数据库已经接受的任务不能因为 API 进程在提交后退出而永远停在 `queued`，重复消息也不能重复执行 CAD 或文件副作用。

本设计优先覆盖生产项目创建、输入转换、输入冻结、阶段执行、阶段确认、工作流取消和 Job 重试。底层 Job 投递基础设施同时替换现有直接投递入口，使其他现有 Job 创建接口共享同一可靠性边界；所有前端可见 mutation 按钮必须进入可靠性清单，并在可以证明安全时复用同一套幂等、回执和权威状态确认方法。

## 已确认现状与缺口

- 前端 React Query 只对查询做最多两次瞬时故障重试；mutation 不会全局重试，当前按钮主要依靠 `isPending` 防止同一标签页重复点击。
- Job 的现有唯一键是 `(created_by, task_type, request_key)`。生产输入转换的 `request_key` 虽然由批次和文件确定，但不同账号仍能各创建一条 Job。
- 输入批次和工作流阶段已有 `SELECT ... FOR UPDATE`，同一事务内的竞争可串行化，但串行化之后仍需要跨账号共享的资源级逻辑键。
- 当前投递顺序是：提交业务事务，再直接调用 Celery。API 若在两步之间退出，Job 已经是 `queued`，却没有消息；再次提交会复用 Job 且不再投递。
- 当前 MySQL SQLAlchemy transport、`task_acks_late`、`task_reject_on_worker_lost` 和 Job 的 `queued -> running` 条件更新能阻止多数重复执行，但不能填补“已提交、未发布”的双写窗口。
- 现有 stale-job 恢复只处理长时间没有心跳的 `running` Job，不处理从未进入 broker 的 `queued` Job。

## 核心不变量

1. 同一生产资源上的同一逻辑动作，不因操作者不同而创建第二套逻辑 Job。
2. 每一个 Job attempt 必须在同一数据库事务中拥有恰好一条持久投递意图。
3. 只要业务事务提交，dispatcher 最终会持续尝试投递；API 进程是否存活不影响最终投递。
4. dispatcher 允许至少一次发布；worker 必须以数据库条件抢占保证业务副作用有效一次。
5. 响应丢失后，同一客户端幂等键只能返回原结果或权威当前状态，不能创建新资源。
6. 未明确声明幂等的写请求、上传流和外部副作用不得自动重放。
7. 审计记录保留真实操作者；资源级去重不能通过伪造 `created_by` 来实现。

## 数据模型

### Job 资源级逻辑键

`jobs` 增加可空的 `operation_key`，并增加唯一约束 `(task_type, operation_key)`。它与现有用户请求键分工如下：

- `request_key`：同一账号、同一 API 请求的重放边界，保留现有兼容语义。
- `operation_key`：共享业务资源上的逻辑执行边界，不包含操作者。

生产输入转换使用 `workflow-input:{batch_id}:item:{item_id}`；工作流自动阶段使用 `workflow:{workflow_id}:stage:{stage_code}`。失败重试沿用同一 Job，通过 `attempt` 形成新执行世代，不创建第二条逻辑 Job。

`create_or_reuse_job` 增加显式 `operation_key` 参数。普通用户任务不传该参数时行为不变；传入后通过预读、唯一约束和嵌套事务处理并发插入，真实 MySQL 下使用 locking current read 读取胜者。

### 持久投递意图

新增 `job_dispatches` 表，每行对应一个 Job attempt；同一批量转换的多行共享 `dispatch_uid`。

主要字段：

- `dispatch_uid`：一次单任务或批量消息的稳定 UUID。
- `job_id`、`job_attempt`：唯一约束，保证每个 attempt 只有一条投递意图。
- `task_type`、`pipeline`、`dispatch_mode`：提交时冻结的投递快照；`dispatch_mode` 为 `single` 或 `conversion_batch`。
- `status`：`pending`、`leased`、`delivered`、`failed`。
- `delivery_attempts`、`available_at`：有界退避状态。
- `lease_token`、`lease_expires_at`：dispatcher 崩溃恢复边界。
- `celery_task_id`、`delivered_at`、`last_error_code`、`last_error_message`：可观测事实。

唯一约束为 `(job_id, job_attempt)`；`dispatch_uid`、`status + available_at` 和 `lease_expires_at` 建索引。错误字段只保存白名单错误类别和有界消息，不保存 DSN、凭据或任意异常栈。

### API 命令回执

新增 `api_command_receipts`，用于响应可能丢失但可安全重放的 JSON 命令。

主要字段：`command_uid`、`actor_user_id`、`endpoint_scope`、`idempotency_key`、`request_sha256`、`response_status`、`response_json`、`resource_type`、`resource_id`、时间字段。唯一约束为 `(actor_user_id, endpoint_scope, idempotency_key)`。

回执与业务变化在同一事务提交。相同键和相同请求摘要返回原响应；相同键和不同摘要返回 `409 IDEMPOTENCY_KEY_REUSED`。校验失败或事务回滚不留下成功回执。响应快照必须有大小上限，且不得包含文件字节、令牌、签名 URL 或敏感配置。

资源级操作即使来自不同账号，也先通过 `operation_key` 和行锁收敛；命令回执不承担跨账号授权或数据共享。

## 第一阶段 DWG 转 DXF 数据流

1. 前端为当前输入版本创建稳定命令键，提交 `conversion-requests`。
2. 后端校验项目权限并锁定 `workflow_input_batches`。
3. 对每个 DWG 输入项，按资源级 `operation_key` 创建或复用 Job。
4. 已是 `queued`、`running` 或 `succeeded` 的 Job 不产生新 attempt；`failed` 或 `cancelled` 的 Job 通过现有状态机增加 attempt。
5. 对每个需要投递的新 attempt，写入 `job_dispatches`；同一请求的行共享 `dispatch_uid` 和 `conversion_batch` 模式。
6. Job、输入项绑定、审计、命令回执和投递意图一起提交。
7. API 返回 `202`、权威 Job 列表、`command_uid`、是否复用及当前投递状态；API 不再同步等待 broker 发布。
8. dispatcher 按 `dispatch_uid` 取得整批投递意图，继续调用现有批量 ODA 消息入口。
9. worker 对每个 Job attempt 执行现有条件抢占。重复批量消息中的已运行、已完成或旧 attempt 被跳过。

这个边界保留现有批量 ODA 吞吐，不把 5000 个输入退化成 5000 次独立 ODA 启动。

## Dispatcher

新增一个复用 backend 镜像的 `dispatcher` Compose 服务，运行单用途 Python 入口，不构建新镜像。

- 单进程、单数据库连接预算、较低 CPU/内存/PID 上限。
- 依赖 MySQL、MinIO 和 backend API 健康；API 完成 Alembic 与 broker schema 准备后才启动。
- 使用短事务和 `FOR UPDATE SKIP LOCKED` 租用最早可用的 `dispatch_uid`，提交租约后再执行 broker I/O，禁止持锁跨网络调用。
- 发布成功后按 `lease_token` 标记整组 `delivered`；失败则增加次数并按带抖动指数退避重新进入 `pending`。
- broker 或网络瞬时故障不把 Job 立即改成业务失败。结构不支持、任务类型非法等永久错误才标记 `failed`，同时以条件更新终止仍处于同一 queued attempt 的 Job。
- 租约超时后可被重新获取。若进程在发布成功后、标记成功前退出，消息可能重发；worker 条件抢占保证有效副作用只有一次。
- 使用稳定 Celery `task_id`，便于日志、broker 行和 outbox 关联；不能把相同 `task_id` 当作 broker 自带去重保证。

不直接由业务路由写 `kombu_message`。Celery 内部表结构不是业务事务 API，直接耦合会破坏升级边界。

## 数据库异常策略

- `1205` lock wait timeout、`1213` deadlock、连接中断和 pool timeout 映射为结构化、可观测错误。
- 只有通过命令回执或资源级逻辑键证明可重放、且不包含上传流/外部副作用的小事务，才允许最多两次、每次使用全新事务上下文的后端重试。
- 其他事务完整回滚，返回 `409 CONCURRENT_STATE_CHANGED` 或 `503 DATABASE_TEMPORARILY_UNAVAILABLE`，并带 `Retry-After` 和请求编号。
- 连接失效时让 SQLAlchemy invalidation 生效；不在已失败 Session 上继续执行。
- 保留 `pool_pre_ping`、连接回收、LIFO 和 READ COMMITTED。新增 dispatcher 后重新计算总连接预算，必须低于服务器 MySQL `max_connections` 的安全水位。

## 前端命令行为

- 普通 mutation 默认不重试。只有调用显式 `reliableCommand` 的端点才带稳定 `Idempotency-Key`，并允许最多两次瞬时故障重试。
- 键按端点、资源和规范化请求摘要保存在 session storage；成功确认后清除。不确定结果时保留，刷新或重连后继续确认。
- `408`、`429`、`502`、`503`、`504` 和无响应网络错误可重试；尊重 `Retry-After`，使用全抖动指数退避。
- `400`、`401`、`403`、`404`、`409` 业务冲突、`413`、`415`、`422` 不自动重试。
- 按钮的 `isPending` 继续防止本标签页双击，但不作为正确性边界。
- 页面必须显示“正在确认受理”“等待投递”“正在执行”“已完成”“需要处理”；断网恢复后先查命令/资源状态，避免让用户猜测是否需要再次点击。

### 按钮可靠性清单

所有可见 `useMutation` 必须声明且只能声明以下一种策略：

- `reliable_command`：稳定幂等键、服务端同事务回执、允许有界瞬时故障重试。适用于创建、提交、确认、取消、重试等已完成服务端契约的 JSON 命令。
- `convergent_state`：通过资源级逻辑键、行锁、版本或目标状态收敛；响应不确定时先 GET 权威状态，不自动重放。适用于可以安全识别“已经完成”但尚未接入通用回执的状态动作。
- `transfer_session`：通过 file transfer 或 upload session 恢复，不自动重放 multipart/Blob 字节。
- `download`：每次重新获取签名或查询已生成结果；下载失败不改变业务状态。
- `local_only`：只改变浏览器状态，不发 HTTP 请求。

新增前端架构检查禁止业务组件直接使用未声明策略的 `useMutation`。后端按风险从生产链路扩展到所有可见按钮：所有 Job 创建/重试必须使用 outbox；有明确唯一资源或目标状态的按钮接入命令回执或收敛式确认；无法证明可重放的动作保留显式确认和状态查询，不能为了覆盖率强行启用自动重试。

## 可观测性

- 控制台增加 outbox 的 pending、leased、oldest_pending_age、retrying 和 failed 数量。
- dispatcher 健康检查同时验证主循环心跳和最近一次数据库成功访问。
- 日志固定包含 `request_id`、`command_uid`、`dispatch_uid`、`job_id`、`attempt`，不记录请求正文和凭据。
- 对 pending 最老年龄和永久失败提供明确告警阈值；普通短暂重试不刷屏。

## 迁移与兼容

- Alembic 增加 `operation_key`、`job_dispatches` 和 `api_command_receipts`；新列可空，旧调用保持兼容。
- 升级时为仍处于 `queued` 的当前 Job attempt 补一条投递意图。即使旧 broker 中已有消息，重复投递也由 Job 抢占门槛安全吸收。
- 现有直接投递调用统一改为事务内创建 outbox，不保留路由级“提交后直接发消息”分支。
- server Compose 服务数由 15 增至 16，启动顺序为 MySQL/MinIO、backend API、dispatcher、workers/nginx；发布脚本和健康门禁同步更新。
- 北京时间迁移和本迁移在同一最终维护窗执行，但数据库迁移脚本、验证清单和回滚检查分别记录。

## 测试门槛

### 单元与契约

- 相同 `operation_key` 跨账号复用同一 Job；请求键原有按账号语义不变。
- Job、审计和 dispatch 行任一失败时同事务回滚。
- 相同 Job attempt 不能插入第二条 dispatch；重试 attempt 可以插入新行。
- dispatcher 租约、超时回收、退避、永久错误和错误脱敏。
- 模拟“发布成功后抛异常”，重复投递仍只有一个 worker 抢占成功。
- 相同命令键同摘要返回原响应，不同摘要返回稳定冲突。

### 真实 MySQL 并发

- 两个不同账号同时点击同一输入批次转换，每个 DWG 只有一个 Job 和一个当前 attempt dispatch。
- 两个 dispatcher 同时竞争，只能有一个租用同一 `dispatch_uid`。
- 注入 1205/1213，证明只重放允许的幂等事务且没有重复审计、Job 或 outbox。
- 提交后模拟 API 退出，dispatcher 仍能发布并完成 Job。

### 浏览器与发布

- 连续双击、两个登录账号、服务端提交后截断 HTTP 响应、短时 503 和离线恢复。
- 容器整体重启后 pending outbox 自动恢复。
- 全量测试、现有库迁移、空库迁移、回滚演练和 no-build 服务器包验证全部通过。

## 回滚

- 切回旧版本前先停止新的业务写入，确认 outbox 没有 pending/leased 行，或让新 dispatcher 排空。
- 旧代码可忽略新增表和可空列；数据库 downgrade 只在确认没有仅存在于 outbox、尚未投递的 Job attempt 后执行。
- 任何无法证明是否已发布的消息按“可能已发布”处理，依靠 attempt 抢占吸收重复；不得手工删除 Job 或 broker 行来猜测状态。

## 非目标

- 不宣称网络和外部 CAD 工具能提供数学意义上的端到端 exactly-once。
- 不引入 RabbitMQ、Kafka 或新的镜像仓库依赖。
- 不让所有 HTTP 写请求获得全局自动重试。
- 不改变 Job attempt、结果血缘、DWG 仅在首阶段出现和后续只流通 DXF 的现有业务语义。
