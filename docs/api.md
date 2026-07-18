# API 参考

本文件由 `cd backend && uv run python ../scripts/generate_api_docs.py` 从 FastAPI OpenAPI schema 生成。端点变更必须先修改代码和测试，再重新生成本文件。当前 OpenAPI 包含 **96 个 path、115 个 operation**。路由表只证明接口存在；功能开关、权限、外部依赖和真实样本仍可能阻止业务执行。

## 统一约定

- 本地直连基地址：`http://127.0.0.1:8010`；Nginx 入口：`http://127.0.0.1:8080`；容器内部 API 端口同为 `8010`。
- 除 `/health`、`/health/ready`、`POST /api/v1/auth/sessions` 和刷新端点外，业务端点均要求 Bearer access token。
- 成功响应使用 `{data, meta}`；分页响应额外包含 `pagination`，`total` 来自 SQL `COUNT(*)`。
- 错误响应使用 `{error: {code, message, details}, meta}`，不会向客户端暴露 traceback、DSN 或本机路径。
- 仓库前端优先展示 `error.message`；422 会展开 `details.errors` 的字段路径和原因，并附带 `error.code` 与 `meta.request_id`。客户端不得只显示“HTTP 4xx”而隐藏服务端原因。无法连接、超时以及无结构化响应时才使用状态码兜底文案。
- `GET /api/v1/jobs/{job_id}/events` 与聚合 `GET /api/v1/jobs/events/stream` 使用 SSE cookie 认证并轮询 MySQL 权威状态；URL 中不传 token。聚合流每次最多观察 200 个文件并在全部终态后关闭。
- 下载流程为：鉴权获取短期签名 URL，再携带 Bearer token 下载。403、408、429、5xx 或网络错误重试时必须重新获取签名。
- 任务重试递增 `attempt`；步骤查询可用 `?attempt=N`，旧 worker 不能覆盖新 attempt。
- 双向 CAD 批量创建一次接受最多 200 个文件并保留每文件 Job；批量取消只作用于请求内且有权写入的 Job，不等同于管理员全局取消。
- SSE snapshot 只包含当前 attempt 的 steps；无项目 Job 的结果仅管理员或创建者可访问。
- 数据控制台读取允许 `admin/auditor`，扫描与处置执行只允许 `admin`；处置必须先预检，再携带绑定操作人和目标摘要的短期 token 与幂等键执行。
- 文件/流水/finding 使用服务端页码分页；对象清单使用不透明 cursor。永久清理未登记对象还必须提交确认词 `PURGE`。
- `AGENT_ENABLED=false` 时 Agent 端点返回 503；仓库没有可执行 Agent task，本项目也不把 Agent 执行列为当前交付目标。

## 健康检查

| Method | Path |
|---|---|
| `GET` | `/health` |
| `GET` | `/health/ready` |

## 认证

| Method | Path |
|---|---|
| `POST` | `/api/v1/auth/sessions` |
| `DELETE` | `/api/v1/auth/sessions/current` |
| `POST` | `/api/v1/auth/tokens/refresh` |
| `GET` | `/api/v1/auth/me` |
| `PATCH` | `/api/v1/auth/password` |

## 数据控制台

| Method | Path |
|---|---|
| `GET` | `/api/v1/data-admin/overview` |
| `GET` | `/api/v1/data-admin/files` |
| `GET` | `/api/v1/data-admin/files/{file_id}` |
| `GET` | `/api/v1/data-admin/objects` |
| `GET` | `/api/v1/data-admin/transfers` |
| `GET` | `/api/v1/data-admin/transfers/{transfer_uid}` |
| `POST, GET` | `/api/v1/data-admin/scans` |
| `POST` | `/api/v1/data-admin/remediations/preview` |
| `POST` | `/api/v1/data-admin/remediations/execute` |
| `GET` | `/api/v1/data-admin/scans/{scan_id}` |
| `GET` | `/api/v1/data-admin/scans/{scan_id}/findings` |

## 用户

| Method | Path |
|---|---|
| `GET, POST` | `/api/v1/users` |
| `GET, PATCH, DELETE` | `/api/v1/users/{user_id}` |
| `PATCH` | `/api/v1/users/me` |
| `POST` | `/api/v1/users/{user_id}/roles` |
| `DELETE` | `/api/v1/users/{user_id}/roles/{role_id}` |
| `POST` | `/api/v1/users/{user_id}/password-reset-requests` |
| `POST` | `/api/v1/users/{user_id}/disable-requests` |
| `POST` | `/api/v1/users/{user_id}/enable-requests` |

## 角色与权限

| Method | Path |
|---|---|
| `GET, POST` | `/api/v1/roles` |
| `PUT` | `/api/v1/roles/{role_id}/permissions` |
| `GET` | `/api/v1/permissions` |

## 项目

| Method | Path |
|---|---|
| `GET, POST` | `/api/v1/projects` |
| `GET, PATCH, DELETE` | `/api/v1/projects/{project_id}` |
| `GET, POST` | `/api/v1/projects/{project_id}/members` |
| `PATCH, DELETE` | `/api/v1/projects/{project_id}/members/{member_id}` |

## 文件与下载

| Method | Path |
|---|---|
| `POST, GET` | `/api/v1/files` |
| `POST` | `/api/v1/files/upload-zip` |
| `GET` | `/api/v1/files/batches` |
| `POST` | `/api/v1/files/batches/bulk-delete` |
| `DELETE` | `/api/v1/files/batches/{batch_name}` |
| `GET` | `/api/v1/files/batches/{batch_name}/download-zip` |
| `GET` | `/api/v1/files/{file_id}/excel-preview` |
| `GET` | `/api/v1/files/{file_id}/dxf-preview` |
| `GET` | `/api/v1/files/{file_id}/dxf-preview/content` |
| `GET, DELETE` | `/api/v1/files/{file_id}` |
| `GET` | `/api/v1/files/{file_id}/download-url` |
| `GET` | `/api/v1/files/{file_id}/download` |
| `POST` | `/api/v1/files/bulk-delete` |
| `POST` | `/api/v1/files/download-zip/preview` |
| `POST` | `/api/v1/files/download-zip` |

## 图纸

| Method | Path |
|---|---|
| `GET, POST` | `/api/v1/drawings` |
| `GET, PATCH, DELETE` | `/api/v1/drawings/{drawing_id}` |
| `GET, POST` | `/api/v1/drawings/{drawing_id}/versions` |
| `GET` | `/api/v1/drawings/{drawing_id}/preview` |

## 任务

| Method | Path |
|---|---|
| `GET, POST` | `/api/v1/jobs` |
| `POST` | `/api/v1/jobs/batches` |
| `POST` | `/api/v1/jobs/cancellation-requests` |
| `GET` | `/api/v1/jobs/events/stream` |
| `GET` | `/api/v1/jobs/{job_id}` |
| `POST` | `/api/v1/jobs/{job_id}/cancellation-requests` |
| `POST` | `/api/v1/jobs/{job_id}/retry-requests` |
| `GET` | `/api/v1/jobs/{job_id}/steps` |
| `GET` | `/api/v1/jobs/{job_id}/logs` |
| `GET` | `/api/v1/jobs/{job_id}/events` |
| `GET` | `/api/v1/jobs/{job_id}/results` |
| `POST` | `/api/v1/jobs/cancel-all-active` |

## 结果与复核

| Method | Path |
|---|---|
| `GET` | `/api/v1/results/{result_id}` |
| `GET` | `/api/v1/results/{result_id}/download-url` |
| `POST, GET` | `/api/v1/results/{result_id}/reviews` |
| `GET` | `/api/v1/reviews/pending` |

## 审计

| Method | Path |
|---|---|
| `GET` | `/api/v1/audit-logs` |
| `GET` | `/api/v1/audit-logs/{audit_log_id}` |

## Agent（禁用边界）

| Method | Path |
|---|---|
| `POST` | `/api/v1/agent-runs` |
| `GET` | `/api/v1/agent-runs/{agent_run_id}` |
| `GET` | `/api/v1/agent-runs/{agent_run_id}/steps` |
| `GET` | `/api/v1/agent-tools` |

## 系统

| Method | Path |
|---|---|
| `GET` | `/api/v1/system/health` |
| `GET` | `/api/v1/system/infrastructure` |
| `GET` | `/api/v1/system/health/oda` |

## Excel Final

| Method | Path |
|---|---|
| `POST` | `/api/v1/excel-final/upload` |
| `POST` | `/api/v1/excel-final/process` |
| `POST` | `/api/v1/excel-final/upload-and-process` |
| `GET` | `/api/v1/excel-final/process/{job_id}` |
| `GET` | `/api/v1/excel-final/process/{job_id}/download` |
| `GET` | `/api/v1/excel-final/overview` |
| `GET` | `/api/v1/excel-final/batches` |
| `GET` | `/api/v1/excel-final/batches/{batch_id}` |
| `GET` | `/api/v1/excel-final/batches/{batch_id}/parts` |
| `GET` | `/api/v1/excel-final/batches/{batch_id}/parts/{part_id}` |
| `GET` | `/api/v1/excel-final/batches/{batch_id}/components` |
| `GET` | `/api/v1/excel-final/parts/search` |
| `GET` | `/api/v1/excel-final/weights/lookup` |
| `GET` | `/api/v1/excel-final/health` |

## 生产流程

| Method | Path |
|---|---|
| `GET, POST` | `/api/v1/workflows` |
| `GET` | `/api/v1/workflows/{workflow_id}` |
| `POST` | `/api/v1/workflows/{workflow_id}/start` |
| `POST` | `/api/v1/workflows/{workflow_id}/stages/{stage_code}/completion` |
| `POST` | `/api/v1/workflows/{workflow_id}/cancellation-requests` |


## CAD 转换生产契约

### 批量创建转换任务

`POST /api/v1/jobs/batches` 仅接受 `convert_dwg_to_dxf` 或 `convert_dxf_to_dwg`，每次请求包含 1-200 个 `file_id`。服务端对重复 ID 去重，先验证全部文件存在性、读权限以及源扩展名（DWG→DXF 只接受 `.dwg`，DXF→DWG 只接受 `.dxf`），再为每个文件创建独立 Job。任一文件不存在、不可读或类型错误时，该 HTTP 请求不留下部分 Job。

```json
{
  "task_type": "convert_dwg_to_dxf",
  "file_ids": [101, 102, 103],
  "precision_level": "normal"
}
```

HTTP 202 返回标准 envelope：

```json
{
  "data": {
    "jobs": [
      {
        "id": 9001,
        "task_type": "convert_dwg_to_dxf",
        "status": "queued",
        "attempt": 1,
        "progress": 0,
        "params_json": {"file_id": 101}
      }
    ]
  },
  "meta": {"request_id": "req_...", "timestamp": "..."}
}
```

仓库前端会对超过 200 个的提交去重、分块，每轮最多并发 3 个请求，并分别保留 `submittedFileIds`、`unsubmittedFileIds` 和 `errors`。这是客户端的“部分块成功”处理，不表示单个 `/jobs/batches` 请求会部分提交。失败块不自动重试，操作员通过“提交/重试”补交，避免响应丢失时无边界重复创建。

| HTTP | `error.code` | 含义 |
|---|---|---|
| 401 | `INVALID_TOKEN` / `TOKEN_REVOKED` / `USER_NOT_ACTIVE` | access 会话无效。 |
| 403 | `FORBIDDEN` | 至少一个文件不可读；该请求未创建 Job。 |
| 404 | `NOT_FOUND` | 至少一个文件不存在或已删除。 |
| 422 | `VALIDATION_ERROR` | task type、ID 数量或 `precision_level` 不符合 schema。 |
| 422 | `INVALID_CONVERSION_SOURCE` | 至少一个文件扩展名与转换方向不匹配。 |
| 503 | `DXF_PIPELINE_DISABLED` / `DXF2DWG_PIPELINE_DISABLED` | 对应管线开关未启用。 |

该端点同样没有 `Idempotency-Key`；只保证一个请求体内重复 `file_id` 去重。完整重放已成功的 HTTP 请求会为同一文件创建新 Job；响应丢失后应先查询最新 Job，不应无条件重放。

### 多文件夹原子软删除

`POST /api/v1/files/batches/bulk-delete` 完整删除 1-100 个批次。`batch_names` 中每个名称会去除首尾空白，不能为空，最长 128 个字符；重复名称只处理一次。

```json
{
  "batch_names": ["BH_拆板前_dwg", "BH_拆板后_dwg"]
}
```

HTTP 200 响应：

```json
{
  "data": {
    "deleted_batch_count": 2,
    "deleted_file_count": 84,
    "cancelled_job_count": 3
  },
  "meta": {"request_id": "req_...", "timestamp": "..."}
}
```

`deleted_file_count` 统计被软删除的 `files` 记录，包含具有相同 `batch_name` 的源文件和已登记生成结果，因此可大于界面上的源文件数。软删除会写入文件状态、删除时间、流转账本和审计记录；它不在请求内立即物理回收对象字节。

服务端在任何写入前确认所有文件夹存在，并对其中每个文件执行“上传者或管理员”删除授权。同一事务内会取消关联的 `pending`、`queued`、`running`、`validating` 和 `waiting_cad_worker` 双向 CAD Job。任一文件夹缺失、任一文件无权或写入失败时，所有文件夹和 Job 一起回滚。

| HTTP | `error.code` | 含义与客户端处理 |
|---|---|---|
| 401 | `INVALID_TOKEN` / `TOKEN_REVOKED` / `USER_NOT_ACTIVE` | 未登录或 access 会话无效；刷新会话后重新确认操作。 |
| 403 | `FORBIDDEN` | 至少一个文件无删除权限；整批未变更。 |
| 404 | `NOT_FOUND` | 至少一个文件夹不存在或已全部删除；整批未变更。 |
| 422 | `VALIDATION_ERROR` | 数组为空、超过 100 项、包含空名或超长名称。 |
| 500 | `INTERNAL_ERROR` | 事务回滚；保留当前选择并刷新权威状态后再决定是否重试。 |

该端点没有 `Idempotency-Key` 契约。如果首次请求已提交但响应丢失，直接重放可因批次已删除而返回 404；客户端应先刷新文件夹列表，不能把 404 当作“从未执行”的证据。

### ZIP 打包可用性预检与严格下载

`POST /api/v1/files/download-zip/preview` 与正式 `POST /api/v1/files/download-zip` 接受相同请求体。预检按 `file_id` 去重，检查源文件和已登记转换结果是否能覆盖每个请求格式，但不读取全部对象字节。

```json
{
  "file_ids": [101, 102],
  "formats": ["dwg", "dxf"],
  "folder_name": "图纸导出"
}
```

HTTP 200 预检响应：

```json
{
  "data": {
    "file_count": 2,
    "formats": [
      {
        "format": "dwg",
        "available_count": 2,
        "missing_count": 0,
        "missing_file_ids": [],
        "complete": true
      },
      {
        "format": "dxf",
        "available_count": 1,
        "missing_count": 1,
        "missing_file_ids": [102],
        "complete": false
      }
    ],
    "can_download": false
  },
  "meta": {"request_id": "req_...", "timestamp": "..."}
}
```

`missing_file_ids` 最多返回 20 个样本，`missing_count` 始终是准确总数。前端只允许选择 `complete=true` 的格式，并默认选择当前转换页的源格式。预检不是锁：预检和正式下载之间若结果被删除或对象丢失，正式下载仍以严格校验为准。

| HTTP | `error.code` | 含义与客户端处理 |
|---|---|---|
| 401 | `INVALID_TOKEN` / `TOKEN_REVOKED` / `USER_NOT_ACTIVE` | 会话无效；刷新登录状态后重新预检。 |
| 403 | `FORBIDDEN` | 至少一个源文件不可读。 |
| 404 | `NOT_FOUND` / `FILE_EXPORT_SOURCE_MISSING` | 至少一个源文件不存在或已删除。 |
| 409 | `FILE_EXPORT_FORMAT_UNAVAILABLE` | 正式下载时至少一个格式不完整；保留弹窗并重新预检。 |
| 409 | `STORAGE_INCONSISTENT` | 元数据存在但所需对象字节不可读；停止重放并排查存储一致性。 |
| 422 | `INVALID_PARAMS` / `VALIDATION_ERROR` | ID 或格式为空，或格式不是 `dwg`/`dxf`。 |

正式 ZIP 不静默跳过缺失文件或格式。只有全部请求项可读取时才返回下载流，因此成功 ZIP 可以作为“请求集合完整”的证据，但不能证明未请求格式存在。

### 进度和可补交口径

Job 的 `progress` 是单任务快照。转换页的“成功进度”按当前范围文件数作分母：`succeeded` 计 100，非停滞 active Job 计经 0-100 截断的当前进度，`failed`、`cancelled`、无 Job 和超过 60 秒仍为 0% 的停滞 `queued` 计 0。因此失败 Job 保留的历史进度不会抬高汇总。

只有最新 Job 状态加载完成后才显示“未提交”。无 Job、`failed`、`cancelled` 或停滞 `queued` 文件进入“提交/重试”集合；活动任务与可补交文件可同时存在，暂停和补交入口因而可同时显示。


## Excel Final 幂等与监视契约

`POST /process` 和 `POST /upload-and-process` 接受 `Idempotency-Key`。键去除首尾空白后必须为 1-96 个 ASCII 字母、数字、点、下划线、冒号或连字符。服务端按端点作用域保存到 `jobs.request_key`，数据库唯一约束覆盖 `(created_by, task_type, request_key)`：首次提交返回 `reused=false` 并分发 Job；相同键和相同参数重放返回同一 `job_id`、`reused=true`，不再次分发；同一 process 键改用另一个 `file_id` 返回 `409 IDEMPOTENCY_KEY_REUSED`。不带键保留旧的每次创建行为，仓库前端始终发送键。

`upload-and-process` 同时把键用于 inbound 上传流水。已完成请求的 HTTP 响应丢失后，重放复用同一 StoredFile、对象和 Job；第一个上传仍在进行时返回 `409 TRANSFER_IN_PROGRESS`，失败终态不能被假装为成功重放。DXF→Excel 桥使用 `dxf2excel-{extraction_job_id}-{result_file_id}`，同一提取结果跨刷新/多标签不会重复登记；正常失败重试仍调用既有 retry endpoint 并递增 attempt。

`GET /health` 除 Stage/依赖/五金手册字段外，还返回 `database_backend`、`database_available`、`storage_backend`、`storage_available`、`storage_bucket` 与稳定的 `degraded_components`。`ready` 要求处理开关、Stage/依赖、手册库、业务数据库和对象存储同时可用；响应不包含底层连接异常或凭据。

前端 `/files/excel-final` 支持 `job_id`、`batch_page`、`batch_size`、`batch_id`、`part_no`、`spec`、`material`、`search_page`、`search_size` 和内部搜索激活标记。默认值不强制写入 URL；关闭抽屉、清空搜索及分页更新只修改自身参数，不覆盖同页任务状态。

## 运行时文档

development/debug 模式启动后，访问 `/docs`、`/redoc` 或 `/openapi.json` 获取请求/响应 schema。
当 `APP_ENV=production` 且 `DEBUG=false` 时，这三个运行时文档入口有意关闭；生产应使用本生成文件和版本化 OpenAPI artifact。
