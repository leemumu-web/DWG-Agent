# API 参考

本文件由 `cd backend && uv run python ../scripts/generate_api_docs.py` 从 FastAPI OpenAPI schema 生成。端点变更必须先修改代码和测试，再重新生成中英文参考。

## 统一约定

- 本地直连基地址：`http://127.0.0.1:8010`；Nginx 入口：`http://127.0.0.1:8080`；容器内部 API 端口：`8000`。
- 除 `/health`、`/health/ready`、`POST /api/v1/auth/sessions` 和刷新端点外，业务端点均要求 Bearer access token。
- 成功响应使用 `{data, meta}`；分页响应额外包含 `pagination`，`total` 来自 SQL `COUNT(*)`。
- 错误响应使用 `{error: {code, message, details}, meta}`，不会向客户端暴露 traceback、DSN 或本机路径。
- `GET /api/v1/jobs/{job_id}/events` 使用 SSE cookie 认证并轮询 MySQL 权威状态；URL 中不传 token。
- 下载流程为：鉴权获取短期签名 URL，再携带 Bearer token 下载。403、408、429、5xx 或网络错误重试时必须重新获取签名。
- 任务重试递增 `attempt`；步骤查询可用 `?attempt=N`，旧 worker 不能覆盖新 attempt。
- SSE snapshot 只包含当前 attempt 的 steps；无项目 Job 的结果仅管理员或创建者可访问。
- `AGENT_ENABLED=false` 时 Agent 端点返回 503；启用后详情和步骤受创建者、管理员或项目成员边界约束。

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

## Agent

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
| `GET` | `/api/v1/system/health/oda` |

## Excel Final

| Method | Path |
|---|---|
| `POST` | `/api/v1/excel-final/upload` |
| `POST` | `/api/v1/excel-final/process` |
| `POST` | `/api/v1/excel-final/upload-and-process` |
| `GET` | `/api/v1/excel-final/process/{job_id}` |
| `GET` | `/api/v1/excel-final/process/{job_id}/download` |
| `GET` | `/api/v1/excel-final/batches` |
| `GET` | `/api/v1/excel-final/batches/{batch_id}` |
| `GET` | `/api/v1/excel-final/batches/{batch_id}/parts` |
| `GET` | `/api/v1/excel-final/batches/{batch_id}/parts/{part_id}` |
| `GET` | `/api/v1/excel-final/batches/{batch_id}/components` |
| `GET` | `/api/v1/excel-final/parts/search` |
| `GET` | `/api/v1/excel-final/weights/lookup` |
| `GET` | `/api/v1/excel-final/health` |

## 运行时文档

启动后访问 `/docs`、`/redoc` 或 `/openapi.json` 获取请求/响应 schema。
