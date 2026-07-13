# API 参考

本文件由 `cd backend && uv run python ../scripts/generate_api_docs.py` 从 FastAPI OpenAPI schema 生成。端点变更必须先修改代码和测试，再重新生成本文件。路由表只证明接口存在；功能开关、权限、外部依赖和真实样本仍可能阻止业务执行。

## 统一约定

- 本地直连基地址：`http://127.0.0.1:8010`；Nginx 入口：`http://127.0.0.1:8080`；容器内部 API 端口同为 `8010`。
- 除 `/health`、`/health/ready`、`POST /api/v1/auth/sessions` 和刷新端点外，业务端点均要求 Bearer access token。
- 成功响应使用 `{data, meta}`；分页响应额外包含 `pagination`，`total` 来自 SQL `COUNT(*)`。
- 错误响应使用 `{error: {code, message, details}, meta}`，不会向客户端暴露 traceback、DSN 或本机路径。
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
| `DELETE` | `/api/v1/files/batches/{batch_name}` |
| `GET` | `/api/v1/files/batches/{batch_name}/download-zip` |
| `GET` | `/api/v1/files/{file_id}/excel-preview` |
| `GET` | `/api/v1/files/{file_id}/dxf-preview` |
| `GET` | `/api/v1/files/{file_id}/dxf-preview/content` |
| `GET, DELETE` | `/api/v1/files/{file_id}` |
| `GET` | `/api/v1/files/{file_id}/download-url` |
| `GET` | `/api/v1/files/{file_id}/download` |
| `POST` | `/api/v1/files/bulk-delete` |
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


## Excel Final 幂等与监视契约

`POST /process` 和 `POST /upload-and-process` 接受 `Idempotency-Key`。键去除首尾空白后必须为 1-96 个 ASCII 字母、数字、点、下划线、冒号或连字符。服务端按端点作用域保存到 `jobs.request_key`，数据库唯一约束覆盖 `(created_by, task_type, request_key)`：首次提交返回 `reused=false` 并分发 Job；相同键和相同参数重放返回同一 `job_id`、`reused=true`，不再次分发；同一 process 键改用另一个 `file_id` 返回 `409 IDEMPOTENCY_KEY_REUSED`。不带键保留旧的每次创建行为，仓库前端始终发送键。

`upload-and-process` 同时把键用于 inbound 上传流水。已完成请求的 HTTP 响应丢失后，重放复用同一 StoredFile、对象和 Job；第一个上传仍在进行时返回 `409 TRANSFER_IN_PROGRESS`，失败终态不能被假装为成功重放。DXF→Excel 桥使用 `dxf2excel-{extraction_job_id}-{result_file_id}`，同一提取结果跨刷新/多标签不会重复登记；正常失败重试仍调用既有 retry endpoint 并递增 attempt。

`GET /health` 除 Stage/依赖/五金手册字段外，还返回 `database_backend`、`database_available`、`storage_backend`、`storage_available`、`storage_bucket` 与稳定的 `degraded_components`。`ready` 要求处理开关、Stage/依赖、手册库、业务数据库和对象存储同时可用；响应不包含底层连接异常或凭据。

前端 `/files/excel-final` 支持 `job_id`、`batch_page`、`batch_size`、`batch_id`、`part_no`、`spec`、`material`、`search_page`、`search_size` 和内部搜索激活标记。默认值不强制写入 URL；关闭抽屉、清空搜索及分页更新只修改自身参数，不覆盖同页任务状态。

## 运行时文档

development/debug 模式启动后，访问 `/docs`、`/redoc` 或 `/openapi.json` 获取请求/响应 schema。
当 `APP_ENV=production` 且 `DEBUG=false` 时，这三个运行时文档入口有意关闭；生产应使用本生成文件和版本化 OpenAPI artifact。
