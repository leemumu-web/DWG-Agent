# API 草案

正式路径统一为：

```text
/api/v1
```

当前已落地的资源：

```text
GET    /health
GET    /api/v1/health
POST   /api/v1/auth/sessions
DELETE /api/v1/auth/sessions/current
GET    /api/v1/auth/me
GET    /api/v1/users
POST   /api/v1/users
GET    /api/v1/users/{user_id}
PATCH  /api/v1/users/{user_id}
DELETE /api/v1/users/{user_id}
GET    /api/v1/roles
POST   /api/v1/roles
GET    /api/v1/permissions
GET    /api/v1/projects
POST   /api/v1/projects
GET    /api/v1/projects/{project_id}
PATCH  /api/v1/projects/{project_id}
DELETE /api/v1/projects/{project_id}
GET    /api/v1/files
POST   /api/v1/files
GET    /api/v1/files/{file_id}
GET    /api/v1/files/{file_id}/download-url
GET    /api/v1/files/{file_id}/download
GET    /api/v1/drawings
POST   /api/v1/drawings
GET    /api/v1/jobs
POST   /api/v1/jobs
GET    /api/v1/jobs/{job_id}
GET    /api/v1/jobs/{job_id}/steps
GET    /api/v1/jobs/{job_id}/results
GET    /api/v1/results/{result_id}
POST   /api/v1/results/{result_id}/reviews
GET    /api/v1/reviews/pending
GET    /api/v1/audit-logs
POST   /api/v1/agent-runs      # 当前按 feature flag 返回 503
GET    /api/v1/agent-tools     # 当前按 feature flag 返回 503
```

统一响应格式：

```json
{
  "data": {},
  "meta": {
    "request_id": "...",
    "timestamp": "..."
  }
}
```
