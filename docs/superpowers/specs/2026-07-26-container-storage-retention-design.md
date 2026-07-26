# 容器存储联通与 Workflow 整批清理设计

## 目标

把 Docker Compose 作为优先生产路径，补齐 MySQL 与 MinIO 的真实联通验收、MinIO 容量可见性、长期日志边界，以及存储接近爆满时按完整生产 Workflow 备份后整批释放空间的安全闭环。数据控制台保持工人可理解的基础状态页，不扩展为复杂清理中心。

本设计确认以下业务口径：

- “整批”指一个完整的生产 Workflow，不是 Files 中的 `batch_name`，也不是整个 Bucket；
- 只允许清理 `succeeded`、`failed`、`cancelled` 的终止 Workflow；
- 永久删除前必须由服务端确认完整备份 ZIP 已传输；
- 容量达到高水位只告警并提示处理已结束的历史 Workflow，不自动删除；
- MySQL 保留文件墓碑、生产关系、审计和 transfer 流水，MinIO/Local 删除对象字节。

## 当前证据与缺口

PR #10 的四路分类图纸导出已经由 `ee4de78` 合入 `main`，当前接口、页面和回归测试仍在。现有 Workflow 分批导出支持四类生产文件的流式下载与确认后物理清理，但不覆盖完整输入、全部阶段产物、历史 attempt 结果和预览缓存，因此不能作为存储爆满时的完整批次释放手段。

Compose 已具备 internal 网络、MySQL/MinIO 命名卷、服务健康依赖、非 root 应用和 worker ready marker。当前不足为：

- MinIO live/readiness 只能证明服务和凭据可列举，不能证明 MySQL 登记与对象写读删形成闭环；
- `GET /api/v1/system/infrastructure` 对 MinIO 容量始终返回 `unknown`；
- 容器日志没有统一轮转上限；
- 缺少面向单个终止 Workflow 的完整范围预检、备份和异步永久清理状态机。

## 容器与容量设计

### Compose 边界

MySQL 与 MinIO 继续只连接 `internal` 网络，不发布宿主端口。业务对象继续写入 `minio_data`，MySQL 继续写入 `mysql_data`；任何日常清理不得调用 `docker compose down -v`。

所有长期服务使用统一的 `json-file` 日志轮转配置，默认单文件 20 MiB、保留 5 个文件。应用、worker、MySQL、MinIO 和 Nginx 均使用该边界，避免宿主 Docker 日志无限增长。CPU 和内存不写死为固定值，以免在不同现场机器上造成 OOM 或错误限流；部署环境仍可通过 Compose override 设置额度。

MinIO 容器在 internal 网络启用只读 Prometheus 指标。后台通过新配置 `MINIO_METRICS_URL` 读取容量指标；Compose 默认指向内部 MinIO 指标地址，本地 Local 后端继续使用 `shutil.disk_usage`。容量读取必须满足：

- 返回总量、已用、可用、使用率和采集时间；
- 80% 及以上为 warning，90% 及以上为 critical，阈值由配置项覆盖；
- 指标连接失败、格式变化或缺少所需指标时返回 `unknown`，不得用 0 代替；
- 容量采集失败不改变对象存储连接状态，两者在 API 和前端分别展示。

### MySQL–MinIO 深探针

`/health/ready` 保持无副作用，只检查 MySQL 和配置的对象后端是否可达。新增 `bash scripts/docker.sh verify-storage` 作为部署验收门，在已健康的 Compose `backend-api` 中执行现有 `scripts/storage/verify_transactions.py`：

1. 创建本次唯一探针对象；
2. 通过应用 Files 路径写入 MinIO 并在 MySQL 登记；
3. 校验对象 stat、读取字节和 SHA-256；
4. 验证鉴权下载、DXF 预览缓存和 file transfer 终态；
5. 软删除本次登记，并物理移除只属于本次探针的对象；
6. 输出 file ID、transfer UID 和清理结论，不打印 endpoint 凭据或 DSN 密码。

探针失败返回非零退出码，并保留足以定位 MySQL、MinIO、API 或 transfer 层的安全证据。它不扫描、修改或清理既有业务对象。

## Workflow 完整备份模型

新增 `workflow_retention_exports` 持久表，避免把“完整批次清理”混入现有四类 `workflow_batch_exports` 合同。主要字段为：

- `export_uid`、`workflow_run_id`、`created_by`；
- `status`：`prepared`、`downloading`、`downloaded`、`download_failed`、`purge_queued`、`purging`、`purged`、`purge_failed`；
- `manifest_json`、`manifest_sha256`、`file_count`、`preview_cache_count`；
- `source_size_bytes`、`reclaimable_size_bytes`；
- 下载 capability 摘要与过期时间、下载/清理时间；
- maintenance task ID、实际释放量、错误码和安全错误信息。

完整清单从以下关系收集并按 `file_id` 去重：

- Workflow input batch 的 Excel、DWG、服务器派生 DXF；
- `workflow_artifacts.file_id`；
- 当前和历史 attempt 对应 Job 的 `analysis_results.result_file_id`；
- 分类与拆板账本中仍属于该 Workflow、但尚未被 artifact 覆盖的文件引用。

DXF 预览缓存不重复写入备份 ZIP，但计入预计释放量，并在永久清理时随源文件一起删除。ZIP 使用稳定目录：`输入/<role>/<file_id>/<original_name>`、`阶段产物/<stage_code>/<artifact_type>/<file_id>/<original_name>`、`其他结果/<file_id>/<original_name>`。file ID 隔离同名文件，不重命名叶子文件。

若同一文件被另一个 Workflow 的 input 或 artifact 引用，预检返回共享引用冲突并阻止清理。文件在同一 Workflow 中出现多次只备份和删除一次。

## 清理状态机

### 范围预检

Workflow 详情只对当前终止批次展示清理范围，包含状态、主文件数、预览缓存数、登记字节、预计释放量和阻断原因。系统不维护跨项目候选排序；容量高水位只提示操作员前往已结束的历史 Workflow 逐批处理。

创建完整备份时，服务端锁定 Workflow 并确认：

- Workflow 为终止状态；
- 不存在 queued/running stage 或 Job；
- 清单中的 StoredFile 状态为 available、大小和 SHA 与登记一致；
- 不存在其他 Workflow 引用。

随后保存不可变 manifest 摘要并签发只允许访问本次下载路径的短期 HttpOnly capability。ZIP 从 Local/MinIO 直接流向浏览器，不在 API 或 MinIO 生成第二份临时 ZIP。

### 永久清理

只有服务端出库流水确认 ZIP 完整传输并把 export 状态置为 `downloaded` 后，管理员才能输入 `DELETE WORKFLOW <workflow_id>` 请求清理。API 再次锁定 Workflow、export 和文件行，重算状态与外部引用；任何变化以 409 拒绝并要求重新预检。

清理任务投递到 maintenance queue，API 返回 202，前端轮询 export。worker 按 manifest 删除主对象和关联预览对象。全部对象删除成功后，单个 MySQL 事务执行：

- `files.status=deleted`、`deleted_at` 与 `purged_at`；
- 移除相应可下载 `workflow_artifacts`；
- 清空 export manifest 和 capability；
- 保存实际删除文件数、字节数和完成时间；
- 写入审计与成功 transfer 终态。

对象存储与 MySQL 不能共享 ACID。若删除部分对象后失败，transfer 标记 `compensation_required`，export 标记 `purge_failed`，MySQL 文件行不伪装成已全部删除。重试对已经不存在的目标执行幂等删除，并在最终提交前再次确认剩余范围。若对象已删而 MySQL 最终提交失败，同样保留 `compensation_required`，要求按 request ID、transfer UID 和一致性扫描处理。

## API 与权限

新增接口：

- `GET /api/v1/workflows/{workflow_id}/retention-preview`：项目成员读取当前批次的范围和阻断原因；
- `POST /api/v1/workflows/{workflow_id}/retention-exports`：项目写角色或管理员创建完整备份；
- `GET /api/v1/workflows/{workflow_id}/retention-exports/{export_uid}`：创建者或管理员读取状态；
- `GET /api/v1/workflows/{workflow_id}/retention-exports/{export_uid}/download`：路径级 capability 流式下载；
- `POST /api/v1/workflows/{workflow_id}/retention-exports/{export_uid}/purge`：仅管理员提交确认词后异步清理。

永久清理严格要求管理员。普通项目写角色可以创建并下载完整备份，但不能物理清理整个 Workflow。

## 前端设计

数据控制台总览只增加一张精炼容量卡，分别显示 MySQL、对象存储连接和容量采集状态。Local/MinIO 均显示总量、已用、可用、使用率和更新时间；`unknown` 明确说明“容量指标不可用，不能判断是否接近爆满”。warning 使用橙色，critical 使用红色。这里不增加清理表格、复杂筛选或批量操作。

完整清理入口位于对应的终止 Workflow 详情页，与现有分类导出和四类分批清理区分。操作固定为三步：

1. `检查清理范围`；
2. `生成完整备份并下载`；
3. `确认备份可打开，永久删除`。

弹窗关闭后 export 状态仍从服务端恢复。下载中断不开放删除；清理执行中禁用重复提交；成功显示实际释放量。登记量与实际对象量不一致时显示红色提示，说明需要在数据控制台执行基础一致性检查。

所有失败使用共享 `ApiErrorAlert`，显示安全中文事实、影响、下一步、错误码和请求编号。容量未知、MinIO 不可达、MySQL 不可达、共享引用、状态漂移、下载中断、maintenance worker 不可用和部分删除分别给出具体建议。前端不得显示 bucket key、主机路径、DSN、密钥或 traceback。

## 长期稳定性

- 不设置自动删除定时任务；高水位只告警并提示逐个处理历史 Workflow；
- 当前 Workflow 的范围预检使用 MySQL 关系查询，不在浏览器或单次请求中枚举全部 MinIO 对象；
- 清理范围以持久 manifest 和 SHA-256 固定，执行前重检；
- 大批次清理在 maintenance worker 执行，不占用长 HTTP 请求；
- capability、预检和确认均绑定操作人、Workflow、manifest 与有效期；
- 日志、审计和 transfer 记录保留 request ID、export UID、task ID 和 transfer UID；
- 不删除 MySQL Workflow、Job、stage、input 或审计行，只释放对象字节并保留墓碑。

## 验证

1. Compose 静态合同：internal 网络、命名卷、日志轮转、MinIO 指标配置、健康依赖和新命令。
2. 容量单元测试：Local 正常、MinIO 指标正常、指标缺失/格式变化/连接失败返回 unknown、阈值边界。
3. Workflow 服务测试：终止状态允许、活动状态拒绝、共享引用拒绝、同文件去重、清单稳定、下载前拒绝删除、确认词错误、状态漂移、并发重复提交和幂等重试。
4. 清理故障测试：对象删除失败、部分删除、MySQL commit 失败、maintenance 投递失败均保留正确 transfer/export 状态。
5. 前端 Playwright：数据控制台精炼容量状态、Workflow 三步操作、下载中断、异步轮询、结构化错误和窄屏布局。
6. 真实容器验收：隔离 Compose MySQL/MinIO 冷启动、`verify-storage`、重启持久化、MinIO 中断导致 readiness 503、恢复后无需重启 API、探针对象 SHA 不变。
7. 仓库发布门：后端全量、前端全量、迁移、Compose、基础设施、文档和 `scripts/verify.sh full`。

## 非目标

- 不自动删除最旧批次；
- 不按 Bucket、路径前缀或 `batch_name` 旁路删除业务对象；
- 不用固定 CPU/内存额度替代现场容量规划；
- 不声称 Compose 已提供 TLS、离机备份、PITR、集中监控或多节点高可用；
- 不改变 PR #10 的四路分类图纸下载，也不把完整清理入口放进普通生产统计。
