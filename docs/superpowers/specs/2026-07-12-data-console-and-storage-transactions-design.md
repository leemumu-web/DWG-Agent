# 数据控制台与存储事务完善设计

**日期：** 2026-07-12  
**状态：** 已确认  
**范围：** MySQL 文件登记、MinIO/本地对象存储、入库与出库流水、一致性扫描与处置、前端数据控制台  
**不包含：** 任意 SQL 控制台、密码/Token/Celery 内部表浏览、自动清理现有孤儿对象、CAD/Agent 新能力

## 1. 背景与目标

平台已经使用 MySQL 保存业务元数据，使用本地文件系统或 MinIO 保存对象字节，并具备上传、下载、软删除、审计和基础设施概览。当前实现能完成基本文件流转，但还不能回答以下运营问题：

1. 一次入库是否同时完成了对象写入、MySQL 登记和审计？
2. 一次出库是否真的传输完成，还是只生成了下载链接或开始了响应？
3. 某个 MySQL 文件行对应的对象是否存在、大小是否一致？
4. 某个 MinIO/本地对象是否已经登记，能否安全补登记或清理？
5. 事务失败后的补偿是否成功，是否有需要人工处理的残留？
6. 管理员能否在不接触任意 SQL 和敏感表的前提下完成日常数据管理？

本设计交付一个面向业务文件的安全数据控制台，并把文件入库、生成、下载、ZIP 出库、恢复和清理纳入可查询的流转账本。MySQL 仍是元数据事实源；对象存储仍是字节事实源。两者无法共享单一 ACID 事务，因此使用有持久化意图、幂等操作和补偿状态的 saga 协调一致性。

## 2. 当前状态审计

### 2.1 已验证基线

- 后端测试：782 passed，5 skipped。
- Ruff：通过。
- 前端 TypeScript/Vite 构建：通过。
- 文档检查与 `docker compose config --quiet`：通过。
- 本机开发栈和 Compose 栈均在线；它们使用不同的 MySQL/存储实例。
- 本机 MySQL `files` 表有 548 行：469 行 `available`，79 行 `deleted`。
- 本机对象目录有 12,627 个对象：12,081 个没有对应 `(bucket, storage_key)` 文件行，2 个 `available` 文件行缺少对象。
- Compose MySQL 和 MinIO 当前为空，适合非破坏性的真实闭环验收。

### 2.2 已发现问题

1. 基础设施页只比较 `available` 文件行数量与全部对象数量，会把按保留策略存在的软删除对象误报成孤儿。
2. 页面只有桶级差额，没有文件、对象、流水和异常明细，也没有预检处置流程。
3. `audit_logs` 记录动作，但不能表达传输状态、字节数、中断、重试和补偿状态。
4. 下载在响应开始前写入 `files.download`，无法证明客户端实际接收完成。
5. ZIP 构建吞掉对象读取异常，可能返回缺少文件的压缩包并仍记录成功。
6. ZIP 构建把完整对象和完整压缩包放入内存，不适合大批量出库。
7. 对象写入后的数据库回滚能触发补偿删除，但补偿失败只写日志，没有持久化待办。
8. session 级待补偿列表没有区分嵌套事务范围。
9. 清理脚本在一批中出现混合成功/失败时，可能已经删除部分对象，却因错误计数不提交对应数据库删除，从而制造新的缺失对象。
10. 本地孤儿比较只比较 `storage_key`，没有同时比较 bucket；MinIO 没有对象明细扫描。
11. `files` 缺少 `(bucket, storage_key)` 唯一约束和明确的 `deleted_at`；保留期借用 `updated_at`。
12. 前端转换页使用 `fetchAllPages` 拉取全部文件和任务，数据增长后会造成高延迟和高内存占用。
13. 审计页只加载最近 200 行后在浏览器筛选，无法提供完整的服务端查询。
14. 页面不展示当前环境和实际数据源身份，开发栈与 Compose 栈同时在线时容易误判数据丢失。
15. 基础设施概览在数据库不可用时仍继续执行目录聚合查询，降级响应可能退化为 500。
16. 基础设施页每 30 秒全量枚举所有对象；对大型 MinIO 桶会形成持续负载。

## 3. 方案选择

### 3.1 方案 A：扩充现有审计日志

在 `audit_logs.after_json` 中补充方向、状态和字节数。优点是迁移少；缺点是 JSON 难以可靠查询状态机、幂等键、失败重试和一致性处置，也无法承担流式响应完成后的结算。该方案不采用。

### 3.2 方案 B：流转账本与一致性扫描

新增 `file_transfers`、`storage_scan_runs` 和 `storage_scan_findings`，保留 `files` 为业务文件事实源。存储 adapter 提供统一的对象分页列举、stat、存在性与删除能力。前端围绕总览、文件登记、存储对象、流转流水和一致性五个页签工作。该方案能覆盖目标，且不要求重写全部业务模型，因此采用。

### 3.3 方案 C：全量事件溯源

把每个文件状态变化写成不可变事件，并从事件投影当前状态。能力最强，但会牵动全部 API、worker、迁移和历史数据，超出当前目标所需复杂度。该方案不采用。

## 4. 权威边界与不变量

1. MySQL `files` 行是业务文件登记的权威来源；对象存储中的字节不能仅凭存在就成为业务文件。
2. 对象存储是文件字节的权威来源；MySQL 行不能证明对象实际存在。
3. 可下载文件必须同时满足：`files.status = available`、访问权限通过、对象存在。
4. `(bucket, storage_key)` 在 `files` 中必须唯一。
5. 每次入库、出库、生成、补登记、恢复和清理必须有一笔 `file_transfers` 流水。
6. `audit_logs` 记录操作者与管理动作；`file_transfers` 记录数据流转状态。二者职责不合并。
7. 补偿失败必须成为可查询状态，不能只写进程日志。
8. 一致性扫描只读；任何修复都必须经过预检、二次确认、并发重检和审计。
9. 扫描结果是某一时刻的快照，不替代执行前的实时重检。
10. 管理员可以读写；审计员只读；其他角色不能访问数据控制台。
11. API 不暴露密码哈希、Token、DSN、secret、宿主绝对路径、Celery 内部消息或任意 SQL。
12. 现有 12,081 个未登记对象不会因功能上线而自动删除或自动登记。

## 5. 数据模型

### 5.1 `files` 变更

- 新增 `deleted_at DATETIME NULL`，软删除时设置，恢复时清空。
- 新增唯一约束 `uq_files_bucket_storage_key(bucket, storage_key)`。
- 新增索引 `ix_files_status_deleted_at(status, deleted_at)`。
- 迁移前先检查重复 `(bucket, storage_key)`；当前只读审计结果为 0 组重复。
- 历史 `status = deleted` 行用当前 `updated_at` 回填 `deleted_at`；其他行保持 NULL。

### 5.2 `file_transfers`

| 字段 | 语义 |
|---|---|
| `id` | BIGINT 主键 |
| `transfer_uid` | 对外稳定 UUID，唯一 |
| `direction` | `inbound` / `outbound` / `internal` |
| `operation` | `upload`、`upload_zip`、`generated`、`download`、`download_zip`、`register_existing`、`restore`、`soft_delete`、`purge` |
| `status` | `prepared`、`in_progress`、`succeeded`、`failed`、`cancelled`、`compensation_required` |
| `file_id` | 可空 FK；失败发生在文件行创建前时为空 |
| `batch_ref` | 批量操作关联标识；同一 ZIP 或批量处置共享 |
| `actor_user_id` | 可空 FK；worker 系统操作为空 |
| `request_id` | HTTP request ID 或 worker correlation ID |
| `idempotency_key` | 操作者和操作范围内唯一，防止重复提交 |
| `bucket` / `storage_key` | 流转发生时的对象位置快照 |
| `original_name` | 流转发生时的显示名快照 |
| `expected_bytes` | 预期字节数，可空 |
| `transferred_bytes` | 实际完成字节数，默认 0 |
| `error_code` / `error_message` | 脱敏后的失败信息 |
| `started_at` / `finished_at` | 执行时间 |
| `created_at` / `updated_at` | 账本行时间 |

索引覆盖 `file_id`、`status`、`direction + created_at`、`operation + created_at`、`actor_user_id + created_at`。唯一约束覆盖 `transfer_uid` 和非空幂等范围。

### 5.3 `storage_scan_runs`

保存扫描的后端、范围 bucket、状态、发起人、开始/结束时间、扫描对象数、扫描文件数，以及正常、软删除保留、对象缺失、未登记对象、大小不符和错误计数。状态为 `queued/running/succeeded/failed/cancelled`。同一作用域只允许一个 active scan。

### 5.4 `storage_scan_findings`

只保存异常和软删除保留项，不保存全部正常对象。字段包括 `run_id`、finding 类型、bucket、key、关联 file ID、文件状态、数据库大小、对象大小、对象最后修改时间、resolution 状态、resolution action、处置人和处置时间。唯一约束覆盖 `(run_id, finding_type, bucket, storage_key)`。

## 6. 存储 adapter

`AbstractStorageBackend` 增加以下能力，本地与 MinIO 使用同一契约：

- `stat_object(bucket, key)`：返回大小和最后修改时间；不存在时抛 `StorageObjectNotFound`。
- `object_exists(bucket, key)`：由 stat 实现，不能吞掉连接错误。
- `list_objects(bucket, prefix, cursor, page_size)`：返回稳定排序的对象摘要和下一页 cursor。
- `iter_file(bucket, key)`：继续作为流式读取入口。
- `delete_object(bucket, key)`：幂等删除，不存在视为成功。

本地 cursor 基于规范化相对 key；MinIO 使用 `start_after`。所有 key 必须经过现有路径边界校验。列表 API 只向有权限的管理员/审计员返回对象 key。

## 7. 事务与流转设计

### 7.1 入库 saga

1. 用独立短事务创建 `file_transfers(status=prepared)`；幂等键冲突时返回已有结果或 `TRANSFER_IN_PROGRESS`。
2. 校验扩展名、DWG 头、大小和 ZIP 安全边界，并流式计算 SHA-256/MD5。
3. 把对象写入唯一 storage key，将流水更新为 `in_progress`。
4. 在一个 MySQL 事务中插入 `files`、将流水更新为 `succeeded`、写审计日志。
5. 第 4 步失败时回滚 MySQL，并删除刚写入的对象。
6. 补偿删除成功后，用独立短事务把流水写为 `failed`；补偿删除失败则写为 `compensation_required`。

ZIP 导入逐条使用 spooled file，不先把单个压缩条目完整读入内存。ZIP 头声明大小、实际解压总量、条目数和压缩比都受限。整个 ZIP 共享 `batch_ref`，每个成功或失败条目有独立流水。

### 7.2 生成文件

worker 的 `save_bytes_as_file` 与上传共用同一 saga 协调器。文件行、分析结果、任务终态和流水成功状态必须在相同 worker 事务中提交；失败补偿使用独立短事务记录。

### 7.3 单文件出库

1. 校验文件、权限、签名和对象存在。
2. 创建 `prepared` 出库流水并提交，然后返回 StreamingResponse。
3. iterator 每产出一块累加实际字节；正常耗尽时用独立短事务写 `succeeded`。
4. 读取异常或客户端取消时写 `failed/cancelled`，记录已传字节和脱敏错误码。
5. 生成下载 URL 只记审计，不冒充完成的出库流水。

### 7.4 ZIP 出库

- 先校验全部请求文件和权限，缺失 ID 不允许静默跳过。
- 使用临时文件增量构建 ZIP，不在内存保存完整对象集合或完整 ZIP。
- 任一必需对象缺失或读取失败时整个导出失败，返回 `STORAGE_INCONSISTENT`；不返回不完整包。
- ZIP 准备完成后创建/更新批量出库流水，响应结束后结算实际字节。
- 临时文件无论成功、异常或客户端取消都清理。

### 7.5 软删除、恢复与清理

- 软删除只改变 `files.status/deleted_at`，对象按保留策略保留，并记录流水与审计。
- 恢复前必须 stat 对象并校验大小；对象缺失时拒绝恢复。
- purge 的顺序为：锁定文件行、重检状态、删除对象、删除或归档数据库行。对象删除后数据库提交失败时必须留下 `compensation_required`，不声称成功。
- reaper 按单行或可独立提交的小批处理执行。单个失败不能回滚其他已经删除对象的数据库变更。

## 8. 一致性扫描与处置

report worker 分批读取文件登记和对象清单，以 `(bucket, storage_key)` 为连接键，分类为：

- `consistent`：available 行存在对象且大小相同，只计数。
- `retained_deleted`：deleted 行仍有对象，属于保留策略，只在 finding 中展示。
- `missing_object`：available 行缺少对象。
- `untracked_object`：对象没有任何文件行。
- `size_mismatch`：两边存在但大小不同，需要进一步校验 SHA-256。

扫描每批使用独立只读查询，不长期持有大事务或行锁。前端只轮询 scan run，不在总览刷新时全量枚举对象。

处置分为预检和执行：

1. `preview` 重新读取目标，返回目标数、总字节、动作、风险和带过期时间的签名 token。
2. `execute` 校验 token 和操作者，锁定相关文件行，重新 stat 对象。
3. 当前状态与 preview 摘要不一致时返回 `REMEDIATION_PREVIEW_STALE`。
4. 执行恢复、补登记、软删除或清理，并写流水与审计。
5. finding 标记 resolved，但历史扫描不改写。

补登记必须计算 SHA-256、MD5、大小和扩展名，并由管理员确认显示名。无法从 key 可靠还原的字段不能猜测。孤儿清理要求输入确认词；批量操作限制数量和总字节，超过限制必须拆批。

## 9. API 设计

新路由前缀为 `/api/v1/data-admin`：

- `GET /overview`
- `GET /files`
- `GET /files/{file_id}`
- `GET /objects`
- `GET /transfers`
- `GET /transfers/{transfer_uid}`
- `POST /scans`
- `GET /scans`
- `GET /scans/{scan_id}`
- `GET /scans/{scan_id}/findings`
- `POST /remediations/preview`
- `POST /remediations/execute`

列表端点统一使用服务端分页、白名单排序和明确的过滤参数。数据库列表使用 page/page_size；对象列表使用 cursor/page_size。管理员拥有全部能力，审计员只能调用 GET 和 preview，其他角色返回 403。

现有上传、下载、ZIP、转换和结果接口保持 URL 兼容，但内部改用流转服务。新增错误码包括：

- `STORAGE_OBJECT_MISSING`
- `STORAGE_INCONSISTENT`
- `STORAGE_COMPENSATION_REQUIRED`
- `TRANSFER_IN_PROGRESS`
- `CONSISTENCY_SCAN_ACTIVE`
- `REMEDIATION_PREVIEW_STALE`
- `REMEDIATION_LIMIT_EXCEEDED`

错误响应不包含 traceback、child stderr、secret、DSN 或宿主路径。

## 10. 前端信息架构

采用已确认的“B · 统一工作台”。保留 `/admin/infrastructure` 路径，页面包含五个可深链页签，筛选、分页和选中 ID写入 URL。

### 10.1 总览

- 显示环境、数据库引擎/逻辑库、存储后端、最后成功刷新时间。
- 显示登记文件、今日入库、今日出库、待处置异常、失败/需补偿流水。
- 展示最近流转、最近扫描和高风险异常。
- 部分请求失败时保留上次成功数据并标记 stale，不清空整页。

### 10.2 文件登记

- 服务端搜索文件名、ID、SHA-256。
- 按状态、bucket、扩展名和一致性分类过滤。
- 详情抽屉显示元数据、对象状态、关联任务、流转、审计和允许的操作。
- 支持下载、软删除、恢复和 Excel 预览；操作完成后精确失效相关 query。

### 10.3 存储对象

- 按 bucket 与 prefix 游标分页。
- 显示 key、大小、最后修改、登记文件 ID 和一致性状态。
- 未登记对象进入预检补登记或预检清理；不存在直接删除按钮。

### 10.4 流转流水

- 按方向、状态、操作、用户和时间筛选。
- 显示预期/实际字节、request ID、错误和补偿状态。
- 支持从流水跳转文件，也支持从文件跳转相关流水。

### 10.5 一致性

- 展示扫描历史、活动扫描进度和分类计数。
- 异常表支持按 finding 类型、bucket、resolution 状态筛选。
- 批量动作先展示影响预览，再二次确认；孤儿清理需要确认词。

整体风格为克制的工业运维控制台：深墨蓝、钢灰、琥珀风险色，保留 Ant Design 体系。状态不能只依赖颜色，所有按钮有 loading、disabled 原因、键盘焦点和错误恢复提示。桌面优先，窄屏使用可滚动页签、横向表格和全宽抽屉。

## 11. 现有前端优化

- 转换页文件和任务改为服务端分页，删除 `fetchAllPages` 对大列表的依赖。
- 上传、下载和批处理完成后联动刷新文件、流水与总览。
- 审计页改用服务端过滤和分页，不再只筛选最近 200 行。
- React Query 的总览刷新与活动扫描刷新分开：总览低频刷新，只有 active scan 才高频轮询。
- 页面隐藏时暂停非必要轮询；恢复可见时立即刷新。

## 12. 错误处理与可观测性

- 数据库不可用时，overview 返回结构化 degraded 响应；不会在后续目录查询中再次抛 500。
- 存储不可用时，MySQL 摘要仍可显示，存储区块单独降级。
- 下载中断、对象缺失、扫描冲突、预检过期和补偿失败都有独立前端说明。
- 每笔流转携带 request ID/correlation ID，日志、审计和流水可以互相定位。
- 不记录原始 secret、Authorization、签名 URL 或完整异常栈到数据库。

## 13. 测试与验收

### 13.1 自动化测试

1. 模型和迁移：SQLite 业务测试、真实 MySQL upgrade/downgrade/check、唯一约束与 deleted_at 回填。
2. 故障注入：对象写成功后数据库失败、补偿失败、重复幂等请求、下载中断、ZIP 中途缺对象、reaper 混合成功/失败。
3. 存储契约：本地和 MinIO 的 list/stat/exists/delete 与 cursor 行为。
4. 扫描：必须使用 `(bucket, key)`，并准确分类 retained deleted、missing、untracked 和 size mismatch。
5. API/RBAC：管理员读写、审计员只读、普通用户拒绝；服务端分页、过滤、并发扫描和 stale preview。
6. 前端：类型构建、五页签、URL 状态、详情联动、预检确认、部分失败和 stale 数据。
7. 回归：现有后端测试、Ruff、docs check、Compose config、前端构建与现有 Playwright 均保持通过。

### 13.2 Compose 真闭环

在当前空的 Compose MySQL/MinIO 中执行：

1. 上传有效样本，验证对象、`files`、`file_transfers` 和审计同时出现。
2. 完成单文件出库和 ZIP 出库，验证响应结束后流水为 succeeded 且字节数正确。
3. 在测试前缀制造一个临时未登记对象和一个临时缺失对象。
4. 启动扫描，验证两类异常被识别。
5. 执行预检和确认处置，验证幂等、审计和 finding resolution。
6. 清理全部测试数据，再次扫描确认测试异常归零。

真实本机数据只做只读扫描；没有用户针对具体 preview 的再次确认，不执行批量清理或批量补登记。

## 14. 迁移与上线顺序

1. 先增加新表、files 约束和 deleted_at，不改变现有 API 行为。
2. 实现 storage adapter 的 list/stat/exists 契约和一致性扫描。
3. 接入上传/生成的入库流水与补偿状态。
4. 接入下载/ZIP 的出库流水和严格错误处理。
5. 上线 data-admin 只读 API 与统一工作台的总览/文件/对象/流水页签。
6. 上线扫描页签。
7. 最后上线预检与处置写操作。
8. 运行 Compose 真闭环和完整回归后，才把旧基础设施汇总页替换为新控制台。

每一步保持仓库可构建、可测试；不会一次性重写所有 route 或 worker。

## 15. 完成标准

只有同时满足以下条件才算完成：

- 前端能明确显示当前环境，并完整预览业务文件登记、对象、入/出库流水和一致性异常。
- MySQL 与本地/MinIO 都通过统一 adapter 和扫描测试。
- 上传、生成、单文件下载和 ZIP 出库都有真实终态流水。
- 数据库失败、存储失败和补偿失败都能被自动测试并在控制台可见。
- 管理动作全部经过预检、确认、实时重检、事务和审计。
- 现有全量测试、静态检查、文档、前端构建和 Playwright 通过。
- Compose 中的 MySQL/MinIO 真闭环通过，且测试数据清理完成。
- 不以当前真实数据的自动删除作为完成条件，也不在无确认时改变现有 12,081 个孤儿对象。
