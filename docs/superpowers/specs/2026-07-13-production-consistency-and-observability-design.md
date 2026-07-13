# 生产一致性与数据监视深化设计

## 背景与目标

当前分支已经完成 PR #1 的选择性重写吸收：DXF 预览对象进入 StoredFile 与传输流水，Excel Final 提供权限过滤的数据控制台，DXF→Excel 可以桥接到 Excel Final，MinIO/MySQL 与本地存储路径均通过了端到端验证。

第二轮审计确认，现有功能已经可用，但仍有四类生产边界需要收口：Excel Final 建任务缺少跨请求、跨进程的幂等契约；DXF 预览登记没有随源文件删除而失效；Excel Final 列表查询对任务类型的约束不完整；前端健康与页面状态仍包含固定 MinIO 文案和刷新后丢失的交互上下文。

本次工作的目标是在不引入通用操作编排大重构的前提下，补齐数据库级幂等、预览生命周期、查询域边界、真实基础设施健康和可恢复的前端监视状态，并保持现有界面语言与任务重试语义。

## 方案选择

### 未采用：查询已有任务的轻量去重

仅按 `file_id` 查询已有活动或成功任务不需要迁移，但查询与创建之间存在竞态；它也无法区分网络重放、用户主动再次处理和失败任务重试，因此不能作为生产级幂等边界。

### 采用：任务请求键与数据库唯一约束

为 Job 增加可空请求键，由 API 接受 `Idempotency-Key`，并通过“创建人、任务类型、请求键”的唯一约束处理多进程并发。相同请求键只复用原任务；正常重试仍使用已有 job retry 接口，新的主动提交使用新的请求键。

### 未采用：通用 Operation/Saga 表

把上传、建任务、预览、导出统一抽象为操作编排模型具有长期价值，但会改变多条已稳定业务链和迁移结构，超出本轮增量完善范围。

## Excel Final 请求幂等

### 数据模型

- `jobs.request_key` 为可空 `VARCHAR(128)`，兼容全部旧记录和不要求幂等的通用任务 API。
- 唯一约束覆盖 `created_by`、`task_type`、`request_key`。认证端点的 `created_by` 非空，因此相同用户和任务类型下的相同键只能创建一个任务。
- 请求键不写入 `params_json`，避免业务参数查询承担并发控制职责。

### API 语义

- `/excel-final/process` 和 `/excel-final/upload-and-process` 接受可选 `Idempotency-Key`，长度与字符集在入口验证。
- 首次请求返回新任务，`reused=false`；相同键重放返回同一 `job_id`，`reused=true`，不再次分发 worker。
- 唯一约束冲突时回滚当前创建，重新读取已提交的任务；只有真正创建任务的请求可以写入创建审计并调用 dispatch。
- `/process` 严格校验 StoredFile 未删除且扩展名为 `.xls` 或 `.xlsx`，再执行现有上传者/管理员权限检查。
- `/upload-and-process` 将同一请求键用于上传传输流水和建任务。上传已成功但响应中断时，重放会取回已登记 StoredFile，再复用或创建唯一任务。
- 不带请求键时保持现有行为，避免破坏通用调用方；本仓库前端始终发送请求键。

### 前端请求键

- 普通文件上传每次用户明确点击提交时生成新的 UUID；同一次提交的网络重放复用该 UUID。
- DXF→Excel 桥使用上游提取任务 ID 与结果文件 ID 派生稳定请求键，同一个提取结果不会因刷新、多标签或重复点击创建多个 Excel Final 任务。
- 失败任务继续调用既有 retry 端点并增加 attempt，不通过创建新任务模拟重试。

## DXF 预览生命周期与流水语义

- 源 DXF 软删除时，在同一数据库事务中查找其当前 SHA 对应的所有活动预览登记并标记删除。
- 每个失效预览写入 `direction=internal, operation=preview_invalidate` 的成功流水，保留源删除的 actor、request ID、对象位置和登记字节数。
- 预览对象不在软删除事务中物理删除，继续遵循现有保留期、对账和管理员 purge 流程。
- 批量删除复用同一事务助手，因此单文件和批次删除具有一致行为。
- 并发生成时若第二次缓存检查发现其他请求已写入对象，该次传输标记为缓存复用，`transferred_bytes=0`，不把零字节记录描述为实际生成写入。
- 内容端点继续以源文件权限为权威；源文件删除后，即使对象仍在保留期内也不能访问预览。

## Excel Final 查询域边界

- 概览、批次列表和跨批次零件搜索均显式增加 `Job.task_type == process_excel_final`。
- 单批次详情继续通过 `_get_accessible_batch()` 同时验证任务类型和权限。
- 任务类型过滤与 `job_read_filter()` 并列：前者定义业务域，后者定义用户可见域，不能互相替代。
- 聚合继续使用批次表的权威统计字段，分页接口继续由数据库返回精确总数。

## 真实健康监视

### 后端健康契约

- Excel Final 健康接口使用当前请求数据库会话确认数据库可用，并返回实际 dialect 名称。
- 调用统一存储后端的 `check_health()`，返回 `storage_backend`、`storage_available` 和 Excel Final 结果 bucket。
- 保留 Stage、依赖包、handbook 文件与 handbook 数据库检查；`ready` 同时要求处理管道、数据库和对象存储可用。
- 健康失败只返回稳定错误分类和布尔状态，不向前端泄露凭据、主机地址或底层异常文本。

### 前端呈现

- 概览栏按真实后端显示“MinIO 对象存储”或“本地对象存储”，数据库按实际 dialect 显示；不再在本地开发模式固定宣称 MinIO/MySQL。
- 降级提示指出处理依赖、业务数据库、五金手册数据库或对象存储中的具体异常环节。
- 页面显示最近一次成功刷新时间，并保留手动刷新入口；活动任务仍按现有频率轮询，静态数据不增加无界轮询。

## 可恢复的前端页面状态

- URL 查询参数承载 `job_id`、批次页码与页大小、打开的 `batch_id`、跨批次搜索条件及搜索分页。
- 参数解析执行正整数、允许页大小和非空字符串校验；无效参数回退默认值并在下一次状态写入时清理。
- 默认值不强制写入 URL，避免冗长链接；改变页大小时页码重置为 1。
- 关闭批次抽屉会删除 `batch_id`，浏览器前进/后退会重新打开或关闭对应详情。
- 搜索继续区分草稿与已应用条件。只有执行搜索后才把条件写入 URL；清空搜索同时删除结果和相关参数。
- URL 更新使用合并方式，不覆盖 `job_id` 或其他同页状态。

## 事务与失败恢复

```text
client request key
  -> upload transfer intent (upload-and-process only)
  -> registered StoredFile or reused upload result
  -> INSERT Job(request_key)
  -> unique constraint decides creator vs reuser
  -> creator commits audit + queued event
  -> creator dispatches committed job
  -> reuser returns existing job without dispatch
```

- 上传对象成功但 Job 创建失败时，重放复用已登记文件，不制造第二个对象。
- Job 已提交但 HTTP 响应丢失时，重放由唯一键返回原任务。
- dispatch 失败仍遵循现有 `dispatch_committed_job()` 恢复语义；幂等复用请求不重复 dispatch。
- 所有新增事务路径必须在 SQLite 单元测试和 MySQL 集成测试中验证，不能依赖数据库特有 JSON 查询实现幂等。

## 测试策略

### 后端

1. 相同用户、任务类型和请求键串行及并发提交只创建一个 Job。
2. 不同用户或不同请求键可独立创建任务。
3. upload-and-process 重放复用同一 StoredFile 和 Job，且不重复 dispatch。
4. 非 Excel StoredFile 被 `/process` 拒绝。
5. 概览、批次和搜索忽略挂在错误任务类型下的异常数据。
6. 单文件和批次软删除同步失效预览登记并产生流水。
7. 本地存储与 MinIO 健康状态、降级分类和 `ready` 组合正确。

### 前端

1. Excel Final API 发送请求键并解析 `reused`。
2. DXF→Excel 桥对同一结果使用稳定请求键。
3. 页面从 URL 恢复任务、批次、详情和搜索状态。
4. 分页、搜索、清空、抽屉关闭及浏览器历史不会覆盖其他参数。
5. 健康栏按后端类型显示正确名称和降级原因。
6. 桌面与窄屏下操作可见，键盘按钮和状态区域保持可访问名称。

## 迁移与文档

- 新迁移接在当前唯一 Alembic head 之后，只增加 `jobs.request_key` 和唯一约束/索引，不修改历史迁移。
- 在空 MySQL、从当前 head 升级的 MySQL、SQLite 测试模型三条路径验证。
- 更新 `docs/api.md`、`docs/database.md`、`docs/architecture.md`、`docs/operations.md` 和 `docs/processing-pipelines.md`，说明幂等请求、预览保留期、健康字段和 URL 深链接。
- 实施计划与验证证据写入 `docs/superpowers/plans/`，不创建根目录状态快照。

## 验收标准

1. 相同 Excel Final 请求键在并发和重放场景下只对应一个 StoredFile（如适用）和一个 Job。
2. 源 DXF 删除后，其预览登记立即不可用且流水可审计，物理对象仍由既有保留期流程管理。
3. Excel Final 所有全局查询同时满足任务类型域和用户权限域。
4. 健康接口与界面准确反映数据库、对象存储、Stage 和五金手册状态，不固定伪装为 MinIO/MySQL。
5. 刷新、分享链接及浏览器前进/后退可恢复监视任务、批次详情和搜索分页。
6. Alembic 保持单一 head，空库与增量 MySQL 迁移均成功。
7. 后端全集、Stage 套件、前端单测与构建、Playwright、基础设施门禁及真实 MinIO+MySQL 探针全部通过。
