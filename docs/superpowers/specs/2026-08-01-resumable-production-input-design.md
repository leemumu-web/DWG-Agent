# 可恢复生产输入上传设计

## 目标

把 Linux 生产工作流的 DWG 文件夹上传从一个长时间、全量 multipart 请求升级为可恢复的文件级会话。在网络抖动、单文件失败、页面重连和多个账号竞争时，只重传缺失文件，最终仍以“一个完整文件夹 + 一个 Excel + 一次整批登记”进入现有生产输入批次。

传输方式可以变化，但下列效果不得减少：最多 5000 张 DWG、完整相对路径校验、同一根目录、非 DWG 明确忽略确认、同名/规范化冲突检测、文件真实性检查、SQL/对象存储一致性、单 Excel 限制、冻结前全量配对和审计血缘。

## 现有问题

- 当前 `/input-dwg-folder` 把全部文件放在一个最长 30 分钟的 multipart 请求中。任意中途断线都无法知道服务端已经稳定接收了哪些文件。
- 浏览器不能安全地盲目重放大型 multipart；服务端当前 transfer idempotency 又基于每次新的 `request_id`，无法把人工重试识别为同一上传意图。
- 上传路由在一个请求中串行保存所有对象。文件多时，HTTP 存活时间、反向代理连接和应用 worker 占用都会放大。
- 多账号可以同时开始不同文件夹上传；当前只有最终批次锁和“已有 DWG”冲突，没有持久、可查询的上传会话。

## 核心不变量

1. 一个会话绑定一个 workflow、一个输入批次版本和一个经过规范化验证的文件夹清单。
2. 每个清单项独立上传、校验和记录；同一项的成功响应可以安全重放。
3. 只有所有项目成功并再次通过服务端完整性校验后，才一次性登记到生产输入批次。
4. 未完成会话中的文件不得出现在生产输入批次、冻结清单或下游任务中。
5. 两个会话竞争同一批次时最多一个完成；失败方收到当前权威版本，不能覆盖已登记输入。
6. 断点恢复不依赖浏览器保留秘密文件句柄。页面刷新后可以通过重新选择同一文件夹和相同清单指纹续传缺失项。
7. 上传并发保持保守默认值 3；不能以提高并发换取表面速度并挤压 MinIO、API 或数据库连接。

## 数据模型

### 上传会话

新增 `workflow_input_upload_sessions`：

- `session_uid`、`workflow_run_id`、`input_batch_id`、`created_by`。
- `kind`，首版为 `dwg_folder`。
- `idempotency_key`、`manifest_sha256`、`root_name`。
- `expected_batch_version`、`expected_file_count`、`expected_total_bytes`。
- `status`：`open`、`uploading`、`ready`、`finalizing`、`completed`、`cancelled`、`expired`、`failed`。
- 已完成数量/字节、到期时间、完成时间和有界错误字段。

同一账号、workflow、kind、幂等键唯一；同一 workflow、manifest 指纹的未终止会话可被显式复用。服务端仍会重新校验授权和批次版本。

### 会话项目

新增 `workflow_input_upload_items`：

- `session_id`、`ordinal`、`relative_path`、`original_name`。
- `expected_size_bytes` 和浏览器提供的 `last_modified_ms`（仅作重选匹配，不作为安全事实）。
- `status`：`pending`、`uploading`、`uploaded`、`failed`。
- `attempt`、`file_id`、`transfer_uid`、服务端计算的 `sha256`、实际字节数和有界错误。

唯一约束为 `(session_id, ordinal)` 和 `(session_id, relative_path)`。相对路径、文件名、扩展名和大小在创建会话时先做有界验证。

## API

### 创建或恢复会话

`POST /api/v1/workflows/{workflow_id}/input-upload-sessions`

请求包含根目录名和至多 5000 个 `{ordinal, relative_path, original_name, size_bytes, last_modified_ms}`。请求总体继续受 4 MiB 清单边界约束，并带稳定 `Idempotency-Key`。

服务端：

1. 验证权限、当前 `source_intake` 阶段和输入批次可编辑状态。
2. 使用现有文件夹清单规则校验单根、DWG 类型、重复路径、同名和数量边界。
3. 计算规范化清单 SHA-256，不信任客户端摘要。
4. 创建会话与项目，或返回相同键/相同摘要的现有会话。
5. 返回每个项目的状态和聚合进度，不返回内部对象键。

### 上传单个项目

`POST /api/v1/workflows/{workflow_id}/input-upload-sessions/{session_uid}/items/{ordinal}`

- multipart 只含一个文件；服务端核对会话、ordinal、名称和预期字节数。
- 项目行加锁。若已经 `uploaded`，重新验证 StoredFile 的大小/SHA/状态后直接返回原结果。
- 失败或中断的项目增加 attempt，创建新的 transfer 意图；旧失败意图保留审计，不把失败 transfer 伪装成可复用成功。
- 对象写入、StoredFile 登记、transfer 完成与 upload item 绑定沿用现有存储补偿机制。
- 响应提供权威项目状态、已完成总字节和整体数量。

单个 multipart 不自动重放。前端只在确认没有成功回执后，使用同一会话项目再次提交；服务端项目状态承担幂等判断。

### 查询与完成

- `GET .../input-upload-sessions/{session_uid}` 返回会话和所有项目的精简状态，支持页面重连和失败项筛选。
- `POST .../input-upload-sessions/{session_uid}/completion` 锁定会话与输入批次，逐项核验 StoredFile、对象大小、SHA-256、名称和可用状态。
- 完成事务把全部文件登记为 WorkflowInputItem、写审计、更新批次版本并将会话置为 `completed`。
- 相同完成请求重放返回已完成批次；若批次已被另一个不同会话改变，返回 `409 INPUT_BATCH_VERSION_CONFLICT` 和当前版本/计数。
- `DELETE .../input-upload-sessions/{session_uid}` 取消未完成会话，不删除已经属于其他业务事实的文件。

### Excel

Excel 仍是单文件上传，但增加稳定 `Idempotency-Key` 和同文件重放结果。它不进入 DWG 文件夹会话，也不降低现有 Excel 内容检查和单文件限制。

现有 `/input-dwg-folder` 在一个兼容周期内保留并继续受原测试保护；新前端只使用会话 API。确认服务器和客户端全部升级后，再单独决定是否移除旧入口。

## 前端行为

- 选择文件夹后先执行现有非 DWG 忽略确认，再建立规范化清单。只有用户确认的 DWG 进入会话。
- 默认并发 3 个单文件上传；每个槽位独立进度和取消信号。总进度由已完成字节与在途字节合并计算。
- 网络离线时停止领取新项目，不主动取消服务端可能仍在完成的请求；恢复后先 GET 会话状态，再只提交缺失/失败项目。
- 当前页面保留 File 对象，可自动续传。页面刷新后提示用户重新选择同一文件夹；清单指纹一致时复用原会话和已完成项目，不一致时要求明确创建新会话。
- 单文件失败不清空已完成进度；列表显示失败文件名、原因和“仅重试失败项”。
- 全部项目成功后自动进入“服务端整批校验”，completion 成功才显示“DWG 文件夹已上传并登记”。
- 离开页面、切换阶段或其他账号完成批次时，当前会话停止上传并刷新权威批次状态。

## 一致性、清理与安全

- finalization 使用输入批次行锁和 `expected_batch_version`。批次 `version` 在每次成功内容变化后递增。
- 会话项目先形成普通 StoredFile 事实，但在完成前不绑定 WorkflowInputItem，不可被工作流冻结或下游选择。
- 取消/过期会话中的孤立文件先软删除，沿用存储保留期和 reconciliation 执行最终清理；不能直接在请求中硬删对象。
- 会话到期由 maintenance 路径处理，活跃上传和最近失败保留足够恢复窗口。
- 每次请求重新执行项目授权；session UID 是定位符，不是授权凭据。
- 路径必须经过现有规范化与安全检查，禁止绝对路径、父目录逃逸、空路径和跨根目录混入。
- 限制清单字节、项目数、单文件大小、总字节和并发；所有计数以服务端实际读取得到的字节为准。

## 测试门槛

### 后端

- 相同会话键和相同清单复用；相同键不同清单冲突。
- 成功单项重复上传不创建第二个 StoredFile；失败 attempt 可安全续传且保留 transfer 审计。
- completion 任一对象缺失、大小/SHA 不符或批次版本变化时整批不登记。
- 两个账号、两个会话同时完成时只有一个成功，另一个得到权威冲突。
- 取消和过期只清理会话拥有且未被其他业务引用的文件。
- 5000 项和 4 MiB 清单边界、路径攻击、非 DWG、重复名称和单根规则全部锁定。

### 浏览器弱网

- 第 N 个文件连接中断后只重传该文件，之前项目保持完成。
- 离线/在线切换后先同步状态；响应在服务端成功后被截断时不重复创建文件。
- 页面刷新并重新选择相同文件夹后继续缺失项；不同清单不会静默复用。
- 多账号竞争时失败页面刷新为服务器已完成的批次，而不是覆盖或继续上传。
- 聚合进度单调、可访问提示稳定、取消不会显示假成功。

### 性能与服务器

- 在并发 1、3、4 下测量 API、MinIO、MySQL 和总耗时；默认 3 只有在资源和吞吐证据支持时才能调整。
- 验证 Nginx 单文件上传超时、请求缓冲和 body 限制；不再依赖一个 30 分钟连接完成整批。
- 对真实大批清单验证内存有界，后端不把全部文件内容同时保存在内存。

## 回滚

- 新会话 API 是新增表和路径，旧大请求入口保留，因此前端可回退到旧版本。
- 回退前停止新会话创建；已完成会话已转成正常 WorkflowInputItem，不受影响。
- 未完成会话保留并停止清理，待恢复新版本后继续，或经管理员清单确认后软删除；不得因回退直接删除 MinIO 对象。

## 非目标

- 首版不实现单个 DWG 文件内部的分块续传；恢复粒度为单文件。
- 不把非 DWG 文件上传后再过滤。
- 不改变 DWG 到 DXF 的业务验证、转换、冻结和下游血缘。
- 不以提高浏览器并发代替协议级恢复。
