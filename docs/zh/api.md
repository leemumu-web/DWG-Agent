# API 参考 -- DWG-Agent 平台

> 版本: v1.0 | 基础路径: `/api/v1` | Stage 1 (生产就绪骨架)
> 63 个端点，位于 `/api/v1` 下，分布在 11 个路由模块中，外加 1 个健康检查端点位于 `/` (共 64 个)。
> 规范依据: `DWG-Agent企业平台技术规范.md` 第 7 节。

---

## 1. 快速参考 -- 所有端点

### 1.1 认证 -- `/api/v1/auth`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 1 | POST | `/api/v1/auth/sessions` | 登录，返回 access token 并设置 refresh cookie | 公开 |
| 2 | DELETE | `/api/v1/auth/sessions/current` | 登出 (将 token 加入黑名单) | 已认证 |
| 3 | POST | `/api/v1/auth/tokens/refresh` | 刷新 access token (读取 HttpOnly cookie) | 公开 (读取 cookie) |
| 4 | GET | `/api/v1/auth/me` | 当前用户信息 | 已认证 |
| 5 | PATCH | `/api/v1/auth/password` | 修改自身密码 (需要当前密码) | 已认证 |

### 1.2 用户 -- `/api/v1/users`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 7 | GET | `/api/v1/users` | 列出用户 (分页) | admin / super_admin |
| 8 | POST | `/api/v1/users` | 创建用户 | admin / super_admin |
| 9 | GET | `/api/v1/users/{user_id}` | 用户详情 | admin / super_admin |
| 10 | PATCH | `/api/v1/users/me` | 更新自身资料 | 已认证 |
| 11 | PATCH | `/api/v1/users/{user_id}` | 更新用户 (管理员操作) | admin (不能修改 super_admin) |
| 12 | DELETE | `/api/v1/users/{user_id}` | 软删除用户 | admin (不能删除 super_admin / 自身) |
| 13 | POST | `/api/v1/users/{user_id}/roles` | 为用户分配角色 | admin (仅 super_admin 可分配 super_admin 角色) |
| 14 | DELETE | `/api/v1/users/{user_id}/roles/{role_id}` | 移除用户角色 | admin (不能移除自身角色) |
| 15 | POST | `/api/v1/users/{user_id}/password-reset-requests` | 管理员发起密码重置 | admin |
| 16 | POST | `/api/v1/users/{user_id}/disable-requests` | 禁用用户账号 | admin (不能禁用 super_admin / 自身) |
| 17 | POST | `/api/v1/users/{user_id}/enable-requests` | 重新启用用户账号 | admin (不能启用 super_admin) |

### 1.3 角色与权限 -- `/api/v1`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 18 | GET | `/api/v1/roles` | 列出角色 | super_admin / admin |
| 19 | POST | `/api/v1/roles` | 创建角色 | super_admin |
| 20 | GET | `/api/v1/permissions` | 列出可用权限 | super_admin / admin |
| 21 | PUT | `/api/v1/roles/{role_id}/permissions` | 替换角色的权限集合 | super_admin |

### 1.4 项目 -- `/api/v1/projects`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 22 | GET | `/api/v1/projects` | 列出项目 (管理员查看全部，其他用户查看自己的) | 已认证 |
| 23 | POST | `/api/v1/projects` | 创建项目 (创建者成为 project_owner) | 已认证 |
| 24 | GET | `/api/v1/projects/{project_id}` | 项目详情 | 项目成员 |
| 25 | PATCH | `/api/v1/projects/{project_id}` | 更新项目 | project_owner / project_engineer |
| 26 | DELETE | `/api/v1/projects/{project_id}` | 软删除项目 | project_owner |
| 27 | GET | `/api/v1/projects/{project_id}/members` | 列出项目成员 | 项目成员 |
| 28 | POST | `/api/v1/projects/{project_id}/members` | 添加项目成员 | project_owner |
| 29 | PATCH | `/api/v1/projects/{project_id}/members/{member_id}` | 修改成员的项目角色 | project_owner |
| 30 | DELETE | `/api/v1/projects/{project_id}/members/{member_id}` | 移除成员 (硬删除) | project_owner |

### 1.5 文件 -- `/api/v1/files`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 31 | POST | `/api/v1/files` | 上传文件 (multipart, DWG 校验) | 已认证 |
| 32 | GET | `/api/v1/files` | 列出文件 (管理员查看全部，其他用户查看自己的) | 已认证 |
| 33 | GET | `/api/v1/files/{file_id}` | 文件元数据 | 上传者 / 管理员 / 项目成员 |
| 34 | DELETE | `/api/v1/files/{file_id}` | 软删除文件 | 上传者 / 管理员 |
| 35 | GET | `/api/v1/files/{file_id}/download-url` | 获取短期签名下载 URL (HMAC, TTL=300s) | 上传者 / 管理员 / 项目成员 |
| 36 | GET | `/api/v1/files/{file_id}/download` | 直接下载 (需要签名参数) | 上传者 / 管理员 / 项目成员 |

### 1.6 图纸 -- `/api/v1/drawings`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 37 | GET | `/api/v1/drawings` | 列出图纸 (管理员查看全部，其他用户查看自己项目的) | 已认证 |
| 38 | POST | `/api/v1/drawings` | 创建图纸 (可选初始 file_id) | project_owner / project_engineer |
| 39 | GET | `/api/v1/drawings/{drawing_id}` | 图纸详情 (级联项目活跃状态检查) | 项目成员 |
| 40 | PATCH | `/api/v1/drawings/{drawing_id}` | 更新图纸元数据 | project_owner / project_engineer |
| 41 | DELETE | `/api/v1/drawings/{drawing_id}` | 归档图纸 (软删除) | project_owner / project_engineer |
| 42 | GET | `/api/v1/drawings/{drawing_id}/versions` | 列出图纸版本 | 项目成员 |
| 43 | POST | `/api/v1/drawings/{drawing_id}/versions` | 上传新版本 (自动递增 version_no) | project_owner / project_engineer |
| 44 | GET | `/api/v1/drawings/{drawing_id}/preview` | 获取图纸预览 (Stage 1 占位) | 项目成员 |

### 1.7 作业 -- `/api/v1/jobs`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 45 | GET | `/api/v1/jobs` | 列出作业 (管理员查看全部，其他用户查看自己项目的) | 已认证 |
| 46 | POST | `/api/v1/jobs` | 创建处理作业 | project_owner / project_engineer |
| 47 | GET | `/api/v1/jobs/{job_id}` | 作业详情 (状态、进度、错误信息) | 项目成员 |
| 48 | POST | `/api/v1/jobs/{job_id}/cancellation-requests` | 请求取消 (仅 queued/running 状态) | project_owner / project_engineer |
| 49 | POST | `/api/v1/jobs/{job_id}/retry-requests` | 请求重试 (仅 failed/cancelled 状态) | project_owner / project_engineer |
| 50 | GET | `/api/v1/jobs/{job_id}/steps` | 列出作业执行步骤 | 项目成员 |
| 51 | GET | `/api/v1/jobs/{job_id}/logs` | 获取作业日志 (Stage 1 占位) | 项目成员 |
| 52 | GET | `/api/v1/jobs/{job_id}/events` | SSE 事件流 (Stage 1 占位) | 项目成员 |
| 53 | GET | `/api/v1/jobs/{job_id}/results` | 列出此作业的分析结果 | 项目成员 |

### 1.8 结果与审核 -- `/api/v1`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 54 | GET | `/api/v1/results/{result_id}` | 结果详情 | 项目成员 (通过 job → drawing → project 链路) |
| 55 | GET | `/api/v1/results/{result_id}/download-url` | 结果文件下载 URL | 项目成员 |
| 56 | POST | `/api/v1/results/{result_id}/reviews` | 提交审核 (approved / rejected / needs_revision) | project_owner / project_reviewer |
| 57 | GET | `/api/v1/results/{result_id}/reviews` | 审核历史 | 项目成员 |

### 1.9 审核列表 -- `/api/v1`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 58 | GET | `/api/v1/reviews/pending` | 列出待审核结果 (need_review 状态) | 已认证 (管理员查看全部；其他用户按项目过滤) |

### 1.10 审计日志 -- `/api/v1/audit-logs`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 59 | GET | `/api/v1/audit-logs` | 列出审计日志 (最近 200 条，分页) | super_admin / auditor |
| 60 | GET | `/api/v1/audit-logs/{audit_log_id}` | 审计日志详情 | super_admin / auditor |

### 1.11 Agent -- `/api/v1`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 61 | POST | `/api/v1/agent-runs` | 创建 Agent 执行 (Stage 1: 返回 503) | 已认证 |
| 62 | GET | `/api/v1/agent-runs/{agent_run_id}` | Agent 执行详情 (Stage 1: 返回 503) | 已认证 |
| 63 | GET | `/api/v1/agent-runs/{agent_run_id}/steps` | Agent 执行步骤 (Stage 1: 返回 503) | 已认证 |
| 64 | GET | `/api/v1/agent-tools` | 可用 Agent 工具 (Stage 1: 返回 503) | 已认证 |

### 1.12 健康检查

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| -- | GET | `/health` | 服务健康检查 | 公开 |

---

## 2. 认证

### 2.1 如何获取 Token

向登录端点发送凭据：

```
POST /api/v1/auth/sessions
Content-Type: application/json

{
  "username": "10001",
  "password": "your-password-here"
}
```

成功时，你将在响应体中收到 `access_token`，同时 `dwg_refresh_token` 将以 HttpOnly cookie 的形式设置。

```
HTTP/1.1 201 Created
Set-Cookie: dwg_refresh_token=<jwt>; HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth

{
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIs...",
    "token_type": "Bearer",
    "expires_in": 1800,
    "user": {
      "id": 1,
      "username": "10001",
      "real_name": "Zhang San",
      "employee_no": "10001",
      "email": "zhangsan@company.local",
      "status": "active",
      "roles": [
        {"id": 3, "code": "engineer", "name": "Engineer"}
      ]
    }
  },
  "meta": {
    "request_id": "req_20260703_000001",
    "timestamp": "2026-07-03T10:00:00+00:00"
  }
}
```

### 2.2 如何传递 Token

在每个需要认证的请求中，将 access token 放入 `Authorization` 请求头：

```
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```

所有业务端点都需要认证。公开端点有：
- `POST /api/v1/auth/sessions` (登录)
- `POST /api/v1/auth/tokens/refresh` (token 刷新)
- `GET /health` (健康检查)

### 2.3 Token 生命周期

| Token | 有效期 | 存储方式 | 撤销机制 |
|-------|--------|----------|----------|
| `access_token` | 30 分钟 (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`) | 客户端内存 (前端使用 `sessionStorage`) | 登出时通过 Redis 加入黑名单 |
| `refresh_token` | 14 天 (`JWT_REFRESH_TOKEN_EXPIRE_DAYS`) | HttpOnly Secure SameSite Cookie | 登出时加入黑名单 |

### 2.4 刷新 Access Token

当 access token 过期时，使用 refresh cookie 获取新的 token：

```
POST /api/v1/auth/tokens/refresh
Cookie: dwg_refresh_token=<jwt>
```

该端点自动读取 HttpOnly cookie，无需请求体。

```
HTTP/1.1 200 OK
Set-Cookie: dwg_refresh_token=<new-jwt>; HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth

{
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIs...",
    "token_type": "Bearer",
    "expires_in": 1800,
    "user": {
      "id": 1,
      "username": "10001",
      "real_name": "Zhang San",
      "employee_no": "10001",
      "email": "zhangsan@company.local",
      "status": "active",
      "roles": [
        {"id": 3, "code": "engineer", "name": "Engineer"}
      ]
    }
  },
  "meta": {
    "request_id": "req_20260703_000002",
    "timestamp": "2026-07-03T10:25:00+00:00"
  }
}
```

### 2.5 登出

```
DELETE /api/v1/auth/sessions/current
Authorization: Bearer <access_token>
Cookie: dwg_refresh_token=<jwt>
```

两个 token 都会被加入 Redis 黑名单。之后再使用任一 token 都将返回 401。

```
HTTP/1.1 204 No Content
```

### 2.6 密码要求

- 最少 12 个字符
- 必须包含至少一个大写字母、一个小写字母和一个数字
- 常见密码 (如 `password123`、`admin123456`) 会被拒绝
- 密码在存储前使用 Argon2id 进行哈希

---

## 3. 端点详细参考

### 3.1 认证端点

#### POST /api/v1/auth/sessions -- 登录

创建新会话。返回 access token 并设置 refresh token cookie。

**请求体:**
```json
{
  "username": "10001",
  "password": "********"
}
```

| 字段 | 类型 | 必填 | 约束 |
|-------|------|------|------|
| `username` | string | 是 | 1-64 个字符 |
| `password` | string | 是 | 1 个字符以上 |

**成功响应 (201 Created):**
```json
{
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIs...",
    "token_type": "Bearer",
    "expires_in": 1800,
    "user": {
      "id": 1,
      "username": "10001",
      "real_name": "Zhang San",
      "employee_no": "10001",
      "email": "zhangsan@company.local",
      "status": "active",
      "roles": [
        {"id": 3, "code": "engineer", "name": "Engineer"}
      ]
    }
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:00:00+00:00"
  }
}
```

**错误响应:**
| 状态码 | 代码 | 条件 |
|--------|------|------|
| 401 | `INVALID_CREDENTIALS` | 用户名或密码错误 |
| 401 | `USER_NOT_ACTIVE` | 账号已被禁用或删除 |
| 422 | (Pydantic) | 用户名/密码格式校验失败 |

---

#### PATCH /api/v1/auth/password -- 修改自身密码

**请求体:**
```json
{
  "current_password": "old-password",
  "new_password": "NewSecurePass123"
}
```

**成功响应 (200 OK):**
```json
{
  "data": {
    "message": "Password changed successfully."
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:00:00+00:00"
  }
}
```

**错误响应:**
| 状态码 | 代码 | 条件 |
|--------|------|------|
| 400 | `INVALID_CURRENT_PASSWORD` | 当前密码错误 |
| 422 | (Pydantic) | 新密码未通过复杂度检查 |

---

### 3.2 文件上传

#### POST /api/v1/files -- 上传 DWG 文件

**请求:** `multipart/form-data`

| 字段 | 类型 | 必填 | 描述 |
|-------|------|------|------|
| `file` | file (二进制) | 是 | 要上传的 DWG 文件 |

**执行的校验:**
1. 扩展名白名单：仅 `.dwg`
2. 最小大小：1024 字节
3. DWG 文件头：必须匹配 AC1012 至 AC1032 其中之一
4. SHA-256 哈希计算并存储
5. MD5 哈希计算并存储

**cURL 示例:**
```bash
curl -X POST http://localhost:8000/api/v1/files \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@building-A.dwg"
```

**成功响应 (201 Created):**
```json
{
  "data": {
    "id": 1001,
    "bucket": "dwg-original",
    "storage_key": "dwg-original/project/1/drawing/123/v1/source.dwg",
    "original_name": "building-A.dwg",
    "file_ext": ".dwg",
    "content_type": "application/x-dwg",
    "size_bytes": 12345678,
    "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "md5": "d41d8cd98f00b204e9800998ecf8427e",
    "uploaded_by": 1,
    "status": "available",
    "created_at": "2026-07-03T10:05:00+00:00",
    "updated_at": "2026-07-03T10:05:00+00:00"
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:05:00+00:00"
  }
}
```

**错误响应:**
| 状态码 | 代码 | 条件 |
|--------|------|------|
| 415 | `FILE_TYPE_NOT_ALLOWED` | 扩展名不是 `.dwg` |
| 415 | `FILE_NOT_DWG` | 文件不包含有效的 DWG 头，或文件太小 (< 1024 字节) |
| 415 | `FILE_MIME_NOT_ALLOWED` | MIME 类型不在 DWG 白名单中 |
| 413 | `FILE_TOO_LARGE` | 文件超过 `MAX_UPLOAD_SIZE_MB` (默认 512 MB) |

---

#### GET /api/v1/files/{file_id}/download-url -- 获取签名下载 URL

返回一个 HMAC 签名的临时 URL，用于下载文件。该 URL 有效期为 300 秒 (5 分钟)。

**成功响应 (200 OK):**
```json
{
  "data": {
    "url": "/api/v1/files/1001/download?expires=1751523900&signature=abc123...",
    "expires_in": 300
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:05:00+00:00"
  }
}
```

**授权:** 上传者、管理员，或使用了该文件的项目的成员。

---

### 3.3 作业生命周期

#### POST /api/v1/jobs -- 创建处理作业

**请求体:**
```json
{
  "drawing_id": 123,
  "project_id": 1,
  "task_type": "extract_layers",
  "precision_level": "normal",
  "params": {
    "include_hidden_layers": false,
    "export_preview": true
  }
}
```

| 字段 | 类型 | 必填 | 默认值 | 约束 |
|-------|------|------|---------|------|
| `drawing_id` | int | 否 | null | 必须属于活跃项目 |
| `project_id` | int | 否 | null | 必须为活跃状态 |
| `task_type` | string | 否 | `"framework_smoke_test"` | 1-64 字符；正则 `^[a-z][a-z0-9_]+$` |
| `precision_level` | string | 否 | `"normal"` | 1-32 字符 |
| `params` | object | 否 | `{}` | 传递给 worker 的任意键值对 |

**成功响应 (202 Accepted):**
```json
{
  "data": {
    "id": 456,
    "project_id": 1,
    "drawing_id": 123,
    "created_by": 1,
    "task_type": "extract_layers",
    "precision_level": "normal",
    "pipeline": "local_stub",
    "status": "queued",
    "priority": 0,
    "progress": 0,
    "params_json": {
      "include_hidden_layers": false,
      "export_preview": true
    },
    "error_code": null,
    "error_message": null,
    "created_at": "2026-07-03T10:10:00+00:00",
    "started_at": null,
    "finished_at": null
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:10:00+00:00"
  }
}
```

**Stage 1 行为:** Celery `worker-report` 桩任务将作业从 `queued` 转换为 `running` 再转换为 `succeeded`，写入作业步骤，并存储 JSON 结果文件。测试在 Celery eager 模式下运行此任务；Docker/本地脚本运行真实的 worker 进程。

---

#### GET /api/v1/jobs/{job_id} -- 作业状态

轮询此端点以跟踪作业进度。

**成功响应 (200 OK -- 作业进行中):**
```json
{
  "data": {
    "id": 456,
    "project_id": 1,
    "drawing_id": 123,
    "created_by": 1,
    "task_type": "extract_layers",
    "precision_level": "normal",
    "pipeline": "dxf_open_source",
    "status": "running",
    "priority": 0,
    "progress": 45,
    "params_json": {
      "include_hidden_layers": false
    },
    "error_code": null,
    "error_message": null,
    "created_at": "2026-07-03T10:10:00+00:00",
    "started_at": "2026-07-03T10:10:01+00:00",
    "finished_at": null
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:12:00+00:00"
  }
}
```

**成功响应 (200 OK -- 作业已完成):**
```json
{
  "data": {
    "id": 456,
    "project_id": 1,
    "drawing_id": 123,
    "created_by": 1,
    "task_type": "extract_layers",
    "precision_level": "normal",
    "pipeline": "dxf_open_source",
    "status": "succeeded",
    "priority": 0,
    "progress": 100,
    "params_json": {
      "include_hidden_layers": false
    },
    "error_code": null,
    "error_message": null,
    "created_at": "2026-07-03T10:10:00+00:00",
    "started_at": "2026-07-03T10:10:01+00:00",
    "finished_at": "2026-07-03T10:13:30+00:00"
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:14:00+00:00"
  }
}
```

**成功响应 (200 OK -- 作业失败):**
```json
{
  "data": {
    "id": 456,
    "status": "failed",
    "progress": 72,
    "error_code": "DXF_PARSE_ERROR",
    "error_message": "ezdxf failed to parse entity at line 2541: invalid group code.",
    "created_at": "2026-07-03T10:10:00+00:00",
    "started_at": "2026-07-03T10:10:01+00:00",
    "finished_at": "2026-07-03T10:11:45+00:00"
  },
  "meta": {
    "request_id": "req_..."
  }
}
```

---

#### POST /api/v1/jobs/{job_id}/cancellation-requests -- 取消作业

仅 `queued` 或 `running` 状态的作业可以被取消。已处于终态 (`succeeded`、`failed`、`cancelled`) 的作业将被拒绝。

**请求体:** 无需 (空请求体)。

**成功响应 (202 Accepted):**
```json
{
  "data": {
    "id": 456,
    "status": "cancelled",
    "finished_at": "2026-07-03T10:15:00+00:00"
  },
  "meta": {
    "request_id": "req_..."
  }
}
```

**错误响应:**
| 状态码 | 代码 | 条件 |
|--------|------|------|
| 409 | `JOB_NOT_CANCELLABLE` | 作业已处于 succeeded / failed / cancelled 状态 |

---

#### POST /api/v1/jobs/{job_id}/retry-requests -- 重试作业

仅 `failed` 或 `cancelled` 状态的作业可以被重试。

**请求体:** 无需 (空请求体)。

**成功响应 (202 Accepted):**
```json
{
  "data": {
    "id": 456,
    "status": "queued",
    "progress": 0,
    "created_at": "2026-07-03T10:16:00+00:00"
  },
  "meta": {
    "request_id": "req_..."
  }
}
```

作业会原地重试：状态重置为 `queued`，进度重置为 0，然后重新入队。

**错误响应:**
| 状态码 | 代码 | 条件 |
|--------|------|------|
| 409 | `JOB_NOT_RETRYABLE` | 作业不处于 failed 或 cancelled 状态 |

---

### 3.4 项目成员

#### POST /api/v1/projects/{project_id}/members -- 添加成员

**请求体:**
```json
{
  "user_id": 5,
  "project_role": "project_engineer"
}
```

**成功响应 (201 Created):**
```json
{
  "data": {
    "id": 42,
    "project_id": 1,
    "user_id": 5,
    "project_role": "project_engineer",
    "created_at": "2026-07-03T10:20:00+00:00"
  },
  "meta": {
    "request_id": "req_..."
  }
}
```

**有效的 `project_role` 值:** `project_owner`、`project_engineer`、`project_reviewer`、`project_viewer`

---

### 3.5 结果审核

#### POST /api/v1/results/{result_id}/reviews -- 提交审核

**请求体:**
```json
{
  "decision": "approved",
  "comment": "All extracted layers match the drawing. Dimensions verified."
}
```

| 字段 | 类型 | 必填 | 约束 |
|-------|------|------|------|
| `decision` | string | 是 | `"approved"`、`"rejected"` 或 `"needs_revision"` |
| `comment` | string | 否 | 审核备注 |

**成功响应 (201 Created):**
```json
{
  "data": {
    "id": 12,
    "result_id": 2001,
    "reviewer_id": 1,
    "decision": "approved",
    "comment": "All extracted layers match the drawing.",
    "created_at": "2026-07-03T10:30:00+00:00"
  },
  "meta": {
    "request_id": "req_..."
  }
}
```

---

## 4. 统一响应格式

### 4.1 成功 (单个资源)

```json
{
  "data": { ... },
  "meta": {
    "request_id": "req_20260703_000001",
    "timestamp": "2026-07-03T10:00:00+00:00"
  }
}
```

### 4.2 成功 (分页列表)

```json
{
  "data": [ ... ],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 120,
    "total_pages": 6
  },
  "meta": {
    "request_id": "req_20260703_000001",
    "timestamp": "2026-07-03T10:00:00+00:00"
  }
}
```

### 4.3 错误

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable description of what went wrong.",
    "details": {
      "field": "optional per-field detail"
    }
  },
  "meta": {
    "request_id": "req_20260703_000003",
    "timestamp": "2026-07-03T10:00:00+00:00"
  }
}
```

### 4.4 空成功 (204 No Content)

对于 DELETE 操作和登出，响应没有响应体：

```
HTTP/1.1 204 No Content
```

---

## 5. 分页与过滤

### 5.1 分页

所有列表端点 (`GET /api/v1/users`、`GET /api/v1/projects`、`GET /api/v1/files` 等) 均通过查询参数支持分页：

| 参数 | 类型 | 默认值 | 描述 |
|-----------|------|---------|------|
| `page` | int | 1 | 页码 (从 1 开始) |
| `page_size` | int | 20 | 每页条目数 (最大 200) |
| `sort_by` | string | `"created_at"` | 排序字段 (因端点而异) |
| `sort_dir` | string | `"desc"` | 排序方向: `asc` 或 `desc` |

**示例:**
```
GET /api/v1/projects?page=2&page_size=50&sort_by=name&sort_dir=asc
```

响应包含一个 `pagination` 对象，内含 `page`、`page_size`、`total` 和 `total_pages`。

### 5.2 过滤

列表端点接受可选的查询过滤参数。常见过滤参数包括：

| 参数 | 类型 | 示例 | 描述 |
|-----------|------|---------|------|
| `status` | string | `?status=active` | 按资源状态过滤 |
| `project_id` | int | `?project_id=1` | 按父项目过滤 |

具体的过滤参数因端点而异。完整列表请查看 `/docs` 上的 OpenAPI schema。

### 5.3 按角色过滤行为

- **super_admin / admin:** 默认查看所有资源。过滤参数缩小结果集。
- **普通用户:** 结果自动限定于用户可访问的资源 (拥有的文件、所属项目等)。过滤参数在此基础上进一步缩小范围。
- **跨项目隔离:** 普通用户无法查看其非成员项目的资源 (服务端在分页前进行过滤，因此不可能泄露计数)。

---

## 6. HTTP 状态码

| 状态码 | 含义 | 典型用法 |
|------|---------|------|
| 200 | OK | 返回数据的成功 GET、PATCH、PUT 操作 |
| 201 | Created | 资源创建 (POST 用户、项目、文件、成员) |
| 202 | Accepted | 异步操作已接受 (POST 作业、agent-run、取消、重试) |
| 204 | No Content | 成功的 DELETE、登出 (无响应体) |
| 400 | Bad Request | 无效的请求参数或参数组合 |
| 401 | Unauthorized | Token 缺失、过期或已被加入黑名单 |
| 403 | Forbidden | 已认证但权限不足 |
| 404 | Not Found | 资源不存在 (或用户无权访问 -- 二者不可区分) |
| 409 | Conflict | 状态冲突 (如重试不可重试的作业、用户名重复) |
| 413 | Payload Too Large | 上传文件超过 `MAX_UPLOAD_SIZE_MB` |
| 415 | Unsupported Media Type | 不允许的文件类型 |
| 422 | Unprocessable Entity | 请求体的 Pydantic 校验失败 |
| 429 | Too Many Requests | 速率限制超限 (登录尝试) |
| 500 | Internal Server Error | 未处理的服务端异常 |
| 503 | Service Unavailable | Agent 已禁用 (`AGENT_ENABLED=false`)、MCP 不可用、CAD Worker 不可达 |

---

## 7. 错误代码参考

### 7.1 认证错误

| 代码 | HTTP | 消息 |
|------|------|------|
| `INVALID_CREDENTIALS` | 401 | 用户名或密码错误。 |
| `INVALID_TOKEN` | 401 | Access token 或 refresh token 无效或已过期。 |
| `TOKEN_REVOKED` | 401 | Token 已被撤销 (已登出或密码已更改)。 |
| `USER_NOT_ACTIVE` | 401 | 账号已被禁用或删除。 |
| `INVALID_CURRENT_PASSWORD` | 400 | 当前密码错误。 |

### 7.2 授权错误

| 代码 | HTTP | 消息 |
|------|------|------|
| `FORBIDDEN` | 403 | 权限不足以执行此操作 (通用)。 |
| `CANNOT_MANAGE_SUPER_ADMIN` | 400 | 只有 super_admin 可以管理 super_admin 账号。 |
| `CANNOT_DISABLE_SELF` | 400 | 管理员不能禁用自己的账号。 |
| `CANNOT_DELETE_SELF` | 400 | 管理员不能删除自己的账号。 |
| `CANNOT_REMOVE_OWN_ROLE` | 400 | 管理员不能移除自己的角色。 |

### 7.3 资源错误

| 代码 | HTTP | 消息 |
|------|------|------|
| `NOT_FOUND` | 404 | 请求的资源不存在。 |
| `USERNAME_EXISTS` | 409 | 该用户名已存在。 |
| `ROLE_EXISTS` | 409 | 该角色代码已存在。 |
| `PROJECT_CODE_EXISTS` | 409 | 该项目代码已存在。 |
| `USER_DELETED` | 400 | 用户已被删除。 |
| `SELF_RESET_NOT_IMPLEMENTED` | 400 | 自助密码重置尚未实现。 |

### 7.4 文件错误

| 代码 | HTTP | 消息 |
|------|------|------|
| `FILE_TYPE_NOT_ALLOWED` | 415 | 仅接受 `.dwg` 文件。 |
| `FILE_MIME_NOT_ALLOWED` | 415 | MIME 类型不在 DWG 白名单中。 |
| `FILE_NOT_DWG` | 415 | 文件不包含有效的 DWG 头，或文件太小 (< 1024 字节)。 |
| `FILE_TOO_LARGE` | 413 | 文件超过最大上传大小。 |

### 7.5 下载错误

| 代码 | HTTP | 消息 |
|------|------|------|
| `INVALID_DOWNLOAD_SIGNATURE` | 403 | 下载 URL 签名缺失或无效。 |
| `DOWNLOAD_URL_EXPIRED` | 403 | 下载 URL 已过期 (TTL=300s)。 |

### 7.6 存储错误

| 代码 | HTTP | 消息 |
|------|------|------|
| `STORAGE_WRITE_FAILED` | 503 | 无法将文件写入存储后端。 |
| `STORAGE_READ_FAILED` | 503 | 无法读取存储的文件对象。 |
| `STORAGE_BACKEND_MISCONFIGURED` | 500 | 配置的存储后端未就绪。 |
| `STORAGE_BACKEND_UNSUPPORTED` | 500 | 不支持的存储后端。 |

### 7.7 作业错误

| 代码 | HTTP | 消息 |
|------|------|------|
| `JOB_NOT_CANCELLABLE` | 409 | 作业无法取消 (已处于终态)。 |
| `JOB_NOT_RETRYABLE` | 409 | 作业当前状态不能重试 (仅 failed/cancelled 可重试)。 |
| `JOB_ENQUEUE_FAILED` | 503 | 作业已创建但无法派发至 Celery。 |

### 7.8 服务错误

| 代码 | HTTP | 消息 |
|------|------|------|
| `AGENT_DISABLED` | 503 | Agent 子系统在 Stage 1 中有意禁用。 |
| `INTERNAL_ERROR` | 500 | 发生意外的服务器错误。 |
| `VALIDATION_ERROR` | 422 | 请求校验失败 (Pydantic)。 |
| `INVALID_SORT_COLUMN` | 422 | 提供的排序列不在该资源允许的列列表中。 |
| `INVALID_SORT_RESOURCE` | 422 | 该资源类型不支持排序。 |

---

## 8. 项目角色

### 8.1 全局角色 (系统级)

通过 `sys_roles` 和 `sys_user_roles` 表进行控制。由 super_admin/admin 通过 `/api/v1/users/{user_id}/roles` 进行管理。

| 角色 | 常量 | 代码 | 范围 |
|------|----------|------|------|
| 超级管理员 | `ROLE_SUPER_ADMIN` | `super_admin` | 完全系统访问权限；绕过所有权限检查 |
| 管理员 | `ROLE_ADMIN` | `admin` | 用户管理、项目监督、作业监控 |
| 工程师 | `ROLE_ENGINEER` | `engineer` | 上传文件、创建任务、查看自己的项目 |
| 审核员 | `ROLE_REVIEWER` | `reviewer` | 审核分析结果 |
| 操作员 | `ROLE_OPERATOR` | `operator` | 执行分配的任务 |
| 观察员 | `ROLE_VIEWER` | `viewer` | 对分配的项目具有只读权限 |
| 审计员 | `ROLE_AUDITOR` | `auditor` | 查看审计日志 |

### 8.2 项目角色 (项目级)

通过 `project_members` 表进行控制。由 `project_owner` 通过 `/api/v1/projects/{project_id}/members` 进行管理。

| 角色 | 常量集 | 可读 | 可写图纸 | 可创建作业 | 可审核 | 可管理成员 |
|------|-------------|------|----------|------------|--------|----------|
| `project_owner` | `PROJECT_OWNER_ROLES` | 是 | 是 | 是 | 是 | 是 |
| `project_engineer` | `PROJECT_WRITE_ROLES` | 是 | 是 | 是 | 否 | 否 |
| `project_reviewer` | -- | 是 | 否 | 否 | 是 | 否 |
| `project_viewer` | -- | 是 | 否 | 否 | 否 | 否 |

### 8.3 角色组合规则

- 一个用户可以拥有多个全局角色。
- 一个用户在每个项目中只能拥有一个项目角色。
- `super_admin` 绕过所有项目级权限检查 -- 实际上他们是每个项目的成员，拥有完全访问权限。
- `admin` 在读取和监督操作中也会绕过项目成员资格检查。
- 项目角色检查会级联活跃项目状态：如果项目被软删除，所有成员均返回 404，无论其角色如何。

---

## 9. 作业状态状态机

```
pending ──→ queued ──→ running ──→ validating ──→ need_review ──→ succeeded
  │                     │   │                        (审核后)
  │                     │   └──→ waiting_cad_worker ──→ validating ──→ ...
  │                     │
  └── (自动)            ├──→ cancelled (仅从 queued/running)
                        └──→ failed    (从 running/validating)
                                         │
                                         └──→ retry ──→ queued (仅从 failed/cancelled)
```

| 状态 | 含义 | 是否终态? |
|--------|------|----------|
| `pending` | 作业已创建但尚未派发 | 否 |
| `queued` | 已派发至 Celery 队列 | 否 |
| `running` | Worker 正在处理 | 否 |
| `waiting_cad_worker` | 等待 Windows CAD Worker (Stage 4) | 否 |
| `validating` | 结果校验进行中 | 否 |
| `need_review` | 低置信度 -- 需要人工审核 | 否 |
| `succeeded` | 任务成功完成 | **是** |
| `failed` | 任务失败并报错 | **是** |
| `cancelled` | 用户取消了任务 | **是** |

---

## 10. 流水线常量

| 常量 | 值 | 描述 | Stage |
|----------|-------|------|-------|
| `PIPELINE_STUB` | `local_stub` | Celery report-queue 假 worker | Stage 1 |
| `PIPELINE_DXF` | `dxf_open_source` | DWG→DXF→ezdxf 解析流水线 | Stage 3 |
| `PIPELINE_CAD` | `zwcad_worker` | Windows ZWCAD 高精度流水线 | Stage 4 |

---

## 11. 功能开关

所有未来阶段的功能都受 `backend/app/core/config.py` 中的布尔型功能开关控制。当开关关闭时，相应端点返回 HTTP 503。

| 开关 | 默认值 | 控制范围 | Stage |
|------|---------|------|-------|
| `AGENT_ENABLED` | `false` | 全部 4 个 agent 端点 (3 个 `/api/v1/agent-runs/*` + `/api/v1/agent-tools`) | Stage 2 |
| `DXF_PIPELINE_ENABLED` | `false` | 作业流水线选择中的 DXF 处理 | Stage 3 |
| `CAD_WORKER_ENABLED` | `false` | 作业流水线选择中的 CAD Worker 派发 | Stage 4 |

要启用某项功能，在 `.env` 中将相应开关设为 `true` 并重启后端。

---

## 12. OpenAPI Schema

交互式 API 文档可通过以下地址访问：

```
http://localhost:8000/docs     (Swagger UI)
http://localhost:8000/redoc    (ReDoc)
```

所有请求/响应 schema 均由 Pydantic v2 模型生成，反映实时 API 接口。
