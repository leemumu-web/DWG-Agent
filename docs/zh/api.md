# API 参考 -- DWG-Agent 平台

> 版本: v1.0 | 基础路径: `/api/v1` | Stage 1 (生产就绪骨架)
> 73 个端点位于 `/api/v1` 下，分布在 12 个路由模块中，外加 1 个健康检查端点位于 `/health`（共 74 个）。
> 规范依据: `DWG-Agent企业平台技术规范.md` (v2.0) 第 7 节。

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
| 6 | GET | `/api/v1/users` | 列出用户 (分页) | admin / super_admin |
| 7 | POST | `/api/v1/users` | 创建用户 | admin / super_admin |
| 8 | GET | `/api/v1/users/{user_id}` | 用户详情 | admin / super_admin |
| 9 | PATCH | `/api/v1/users/me` | 更新自身资料 | 已认证 |
| 10 | PATCH | `/api/v1/users/{user_id}` | 更新用户 (管理员操作) | admin (不能修改 super_admin) |
| 11 | DELETE | `/api/v1/users/{user_id}` | 软删除用户 | admin (不能删除 super_admin / 自身) |
| 12 | POST | `/api/v1/users/{user_id}/roles` | 为用户分配角色 | admin (仅 super_admin 可分配 super_admin 角色) |
| 13 | DELETE | `/api/v1/users/{user_id}/roles/{role_id}` | 移除用户角色 | admin (不能移除自身角色) |
| 14 | POST | `/api/v1/users/{user_id}/password-reset-requests` | 管理员发起密码重置 | admin |
| 15 | POST | `/api/v1/users/{user_id}/disable-requests` | 禁用用户账号 | admin (不能禁用 super_admin / 自身) |
| 16 | POST | `/api/v1/users/{user_id}/enable-requests` | 重新启用用户账号 | admin (不能启用 super_admin) |

### 1.3 角色与权限 -- `/api/v1`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 17 | GET | `/api/v1/roles` | 列出角色 | super_admin / admin |
| 18 | POST | `/api/v1/roles` | 创建角色 | super_admin |
| 19 | GET | `/api/v1/permissions` | 列出可用权限 | super_admin / admin |
| 20 | PUT | `/api/v1/roles/{role_id}/permissions` | 替换角色的权限集合 | super_admin |

### 1.4 项目 -- `/api/v1/projects`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 21 | GET | `/api/v1/projects` | 列出项目 (管理员查看全部，其他用户查看自己的) | 已认证 |
| 22 | POST | `/api/v1/projects` | 创建项目 (创建者成为 project_owner) | 已认证 |
| 23 | GET | `/api/v1/projects/{project_id}` | 项目详情 | 项目成员 |
| 24 | PATCH | `/api/v1/projects/{project_id}` | 更新项目 | project_owner / project_engineer |
| 25 | DELETE | `/api/v1/projects/{project_id}` | 软删除项目 | project_owner |
| 26 | GET | `/api/v1/projects/{project_id}/members` | 列出项目成员 | 项目成员 |
| 27 | POST | `/api/v1/projects/{project_id}/members` | 添加项目成员 | project_owner |
| 28 | PATCH | `/api/v1/projects/{project_id}/members/{member_id}` | 修改成员的项目角色 | project_owner |
| 29 | DELETE | `/api/v1/projects/{project_id}/members/{member_id}` | 移除成员 (硬删除) | project_owner |

### 1.5 文件 -- `/api/v1/files`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 30 | POST | `/api/v1/files` | 上传文件 (multipart, DWG 校验) | 已认证 |
| 31 | GET | `/api/v1/files` | 列出文件 (管理员查看全部，其他用户查看自己的) | 已认证 |
| 32 | GET | `/api/v1/files/{file_id}` | 文件元数据 | 上传者 / 管理员 / 项目成员 |
| 33 | DELETE | `/api/v1/files/{file_id}` | 软删除文件 | 上传者 / 管理员 |
| 34 | GET | `/api/v1/files/{file_id}/download-url` | 获取短期签名下载 URL (HMAC, TTL=300s) | 上传者 / 管理员 / 项目成员 |
| 35 | GET | `/api/v1/files/{file_id}/download` | 直接下载 (需要签名参数) | 上传者 / 管理员 / 项目成员 |
| 36 | POST | `/api/v1/files/upload-zip` | 上传 `.zip` 压缩包，解压匹配的 `.dwg`/`.dxf` 文件并创建批次 | 已认证 |
| 37 | GET | `/api/v1/files/batches` | 列出所有不同的批次名称（含文件数量和最新时间） | 已认证 |
| 38 | DELETE | `/api/v1/files/batches/{batch_name}` | 软删除批次中的所有文件 | 上传者 / 管理员（按文件判定） |
| 39 | GET | `/api/v1/files/batches/{batch_name}/download-zip` | 以 ZIP 流式下载批次中所有文件 | 上传者 / 管理员 / 项目成员 |
| 40 | GET | `/api/v1/files/{file_id}/excel-preview` | 预览 `.xlsx`/`.xls` 文件内容为 JSON | 上传者 / 管理员 / 项目成员 |
| 41 | POST | `/api/v1/files/bulk-delete` | 按 ID 批量软删除多个文件 | 上传者 / 管理员（按文件判定） |
| 42 | POST | `/api/v1/files/download-zip` | 下载选中文件的 DWG/DXF 版本为 ZIP（流式） | 上传者 / 管理员 / 项目成员 |

### 1.6 图纸 -- `/api/v1/drawings`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 43 | GET | `/api/v1/drawings` | 列出图纸 (管理员查看全部，其他用户查看自己项目的) | 已认证 |
| 44 | POST | `/api/v1/drawings` | 创建图纸 (可选初始 file_id) | project_owner / project_engineer |
| 45 | GET | `/api/v1/drawings/{drawing_id}` | 图纸详情 (级联项目活跃状态检查) | 项目成员 |
| 46 | PATCH | `/api/v1/drawings/{drawing_id}` | 更新图纸元数据 | project_owner / project_engineer |
| 47 | DELETE | `/api/v1/drawings/{drawing_id}` | 归档图纸 (软删除) | project_owner / project_engineer |
| 48 | GET | `/api/v1/drawings/{drawing_id}/versions` | 列出图纸版本 | 项目成员 |
| 49 | POST | `/api/v1/drawings/{drawing_id}/versions` | 上传新版本 (自动递增 version_no) | project_owner / project_engineer |
| 50 | GET | `/api/v1/drawings/{drawing_id}/preview` | 获取图纸预览 (Stage 1 占位) | 项目成员 |

### 1.7 作业 -- `/api/v1/jobs`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 51 | GET | `/api/v1/jobs` | 列出作业 (管理员查看全部，其他用户查看自己项目的) | 已认证 |
| 52 | POST | `/api/v1/jobs` | 创建处理作业 | project_owner / project_engineer |
| 53 | GET | `/api/v1/jobs/{job_id}` | 作业详情 (状态、进度、错误信息) | 项目成员 |
| 54 | POST | `/api/v1/jobs/{job_id}/cancellation-requests` | 请求取消 (仅 queued/running 状态) | project_owner / project_engineer |
| 55 | POST | `/api/v1/jobs/{job_id}/retry-requests` | 请求重试 (仅 failed/cancelled 状态) | project_owner / project_engineer |
| 56 | POST | `/api/v1/jobs/cancel-all-active` | 系统级批量取消所有 queued/running/pending 作业（最佳努力 Celery 撤销 + 队列清理） | admin / super_admin |
| 57 | GET | `/api/v1/jobs/{job_id}/steps` | 列出作业执行步骤 | 项目成员 |
| 58 | GET | `/api/v1/jobs/{job_id}/logs` | 获取作业日志 (Stage 1 占位) | 项目成员 |
| 59 | GET | `/api/v1/jobs/{job_id}/events` | SSE 事件流 (Stage 1 占位) | 项目成员 |
| 60 | GET | `/api/v1/jobs/{job_id}/results` | 列出此作业的分析结果 | 项目成员 |

### 1.8 结果与审核 -- `/api/v1`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 61 | GET | `/api/v1/results/{result_id}` | 结果详情 | 项目成员 (通过 job → drawing → project 链路) |
| 62 | GET | `/api/v1/results/{result_id}/download-url` | 结果文件下载 URL | 项目成员 |
| 63 | POST | `/api/v1/results/{result_id}/reviews` | 提交审核 (approved / rejected / needs_revision) | project_owner / project_reviewer |
| 64 | GET | `/api/v1/results/{result_id}/reviews` | 审核历史 | 项目成员 |

### 1.9 审核列表 -- `/api/v1`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 65 | GET | `/api/v1/reviews/pending` | 列出待审核结果 (need_review 状态) | 已认证 (管理员查看全部；其他用户按项目过滤) |

### 1.10 审计日志 -- `/api/v1/audit-logs`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 66 | GET | `/api/v1/audit-logs` | 列出审计日志 (最近 200 条，分页) | super_admin / auditor |
| 67 | GET | `/api/v1/audit-logs/{audit_log_id}` | 审计日志详情 | super_admin / auditor |

### 1.11 Agent -- `/api/v1`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 68 | POST | `/api/v1/agent-runs` | 创建 Agent 执行 (Stage 1: 返回 503) | 已认证 |
| 69 | GET | `/api/v1/agent-runs/{agent_run_id}` | Agent 执行详情 (Stage 1: 返回 503) | 已认证 |
| 70 | GET | `/api/v1/agent-runs/{agent_run_id}/steps` | Agent 执行步骤 (Stage 1: 返回 503) | 已认证 |
| 71 | GET | `/api/v1/agent-tools` | 可用 Agent 工具 (Stage 1: 返回 503) | 已认证 |

### 1.12 健康检查

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| -- | GET | `/health` | 服务健康检查 | 公开 |

### 1.13 系统 -- `/api/v1/system`

| # | Method | Path | Summary | Auth |
|---|--------|------|---------|------|
| 72 | GET | `/api/v1/system/health` | 深度健康检查：Redis 可达性、功能开关、存储后端 | 已认证 |
| 73 | GET | `/api/v1/system/health/oda` | ODA File Converter 可用性探针（永不返回 503；状态在响应体中） | 已认证 |

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
| 401 | `INVALID_CREDENTIALS` | 账号已被禁用或删除（时序安全：与密码错误返回相同响应） |
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
    "changed": true
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

| 字段 | 类型 | 位置 | 必填 | 描述 |
|------|------|------|------|------|
| `upload` | file (二进制) | form | 是 | 要上传的 DWG 文件 |
| `batch_name` | string | query | 否 | 可选的批次标签，用于将本次上传与其他文件归为一组 |

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
  -F "upload=@building-A.dwg"
```

**成功响应 (201 Created):**
```json
{
  "data": {
    "id": 1001,
    "bucket": "dwg-original",
    "storage_key": "uploads/a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6.dwg",
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

#### POST /api/v1/files/upload-zip -- 上传 ZIP 压缩包 (批量导入)

上传 `.zip` 压缩包，解压每个扩展名匹配 `file_ext` 的文件，并为每个文件自动创建 `StoredFile` 记录。ZIP 文件名（去掉 `.zip`）将成为所有解压文件的 `batch_name`；如果清理后为空或 `unnamed`，则生成回退名称 `导入_YYYYMMDD_HHMMSS`。不匹配 `file_ext` 的文件（以及未通过 DWG 头检查的 `.dwg` 条目）将计入跳过数。

**授权:** 任何已认证用户。解压出的文件归调用者所有（`uploaded_by`）。

**请求:** `multipart/form-data`

| 字段 | 类型 | 必填 | 描述 |
|-------|------|------|------|
| `upload` | file (二进制) | 是 | 要导入的 `.zip` 压缩包 |

**查询参数:**

| 参数 | 类型 | 必填 | 默认值 | 描述 |
|-----------|------|------|---------|------|
| `file_ext` | string | 否 | `.dwg` | 要提取的扩展名；必须是 `.dwg` 或 `.dxf` |

**解压限制与校验:**
1. 上传文件名必须以 `.zip` 结尾且在上传白名单中（`.dwg`、`.dxf`、`.zip`）。
2. ZIP 总大小不得超过 `MAX_UPLOAD_SIZE_MB`（默认 512 MB）。
3. 压缩包必须是有效、完整的 ZIP（`testzip` 通过）。
4. 条目数不得超过 `max_zip_entry_count`（默认 1000）。
5. 解压后总大小不得超过 `max_zip_extract_mb`（默认 2048 MB）。
6. `.dwg` 条目必须通过 DWG 头检查（AC1012–AC1032）；失败的条目将被跳过，不会导致致命错误。
7. 路径遍历防护：条目名称经过清理，去除目录部分。

**cURL 示例:**
```bash
curl -X POST "http://localhost:8000/api/v1/files/upload-zip?file_ext=.dxf" \
  -H "Authorization: Bearer $TOKEN" \
  -F "upload=@楼层图纸.zip"
```

**成功响应 (201 Created):**
```json
{
  "data": {
    "batch_name": "楼层图纸",
    "files": [
      {
        "id": 2101,
        "bucket": "dxf-original",
        "storage_key": "uploads/a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6.dxf",
        "original_name": "F1-plan.dxf",
        "file_ext": ".dxf",
        "content_type": "image/vnd.dxf",
        "size_bytes": 245760,
        "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "md5": "d41d8cd98f00b204e9800998ecf8427e",
        "uploaded_by": 1,
        "status": "available",
        "batch_name": "楼层图纸",
        "created_at": "2026-07-03T10:05:00+00:00",
        "updated_at": "2026-07-03T10:05:00+00:00"
      }
    ],
    "success_count": 1,
    "skipped_count": 2
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
| 415 | `FILE_TYPE_NOT_ALLOWED` | 上传文件不是 `.zip` 压缩包 |
| 422 | `INVALID_PARAMS` | `file_ext` 为空、不是 `.dwg`/`.dxf`，或不在上传白名单中 |
| 413 | `FILE_TOO_LARGE` | ZIP 压缩包超过 `MAX_UPLOAD_SIZE_MB` |
| 415 | `FILE_NOT_ZIP` | 上传文件不是有效的 ZIP 压缩包 |
| 415 | `ZIP_CORRUPTED` | ZIP 包含损坏的条目（`testzip` 失败） |
| 413 | `ZIP_TOO_MANY_FILES` | ZIP 包含超过 `max_zip_entry_count` 个文件 |
| 413 | `ZIP_TOO_LARGE` | 解压内容超过 `max_zip_extract_mb` |
| 422 | `ZIP_EMPTY` | 压缩包中没有任何文件（没有解压出任何文件且没有跳过任何文件） |

---

#### GET /api/v1/files/batches -- 列出批次

返回所有未删除文件中的不同批次名称，每个批次包含文件数量和该批次中最新的 `created_at`。结果在 Redis 中缓存 30 秒，以减少高频轮询时的数据库负载。

**授权:** 任何已认证用户。注意：此端点返回所有未删除文件的批次聚合数据，**不**应用逐文件访问过滤。

**查询参数:**

| 参数 | 类型 | 必填 | 默认值 | 描述 |
|-----------|------|------|---------|------|
| `file_ext` | string | 否 | `""` (全部) | 按文件扩展名过滤批次，如 `.dwg` 或 `.dxf` |

**成功响应 (200 OK):**
```json
{
  "data": [
    {
      "name": "楼层图纸",
      "file_count": 12,
      "latest_created_at": "2026-07-03T10:05:00+00:00"
    },
    {
      "name": "导入_20260702_143000",
      "file_count": 3,
      "latest_created_at": "2026-07-02T14:30:00+00:00"
    }
  ],
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-03T10:05:00+00:00"
  }
}
```

`data` 字段为普通数组（不分页）。批次按 `latest_created_at` 降序排列。

**错误响应:** 除标准认证错误 (`401`) 外无其他错误。

---

#### DELETE /api/v1/files/batches/{batch_name} -- 删除批次

软删除 `batch_name` 匹配的每个未删除文件。访问控制按文件强制执行：如果批次中**任何**文件不可被调用者删除，整个操作将被拒绝，且不删除任何文件。

**授权:** 对于批次中的每个文件，调用者必须是上传者或管理员（具有全局项目访问权限）。

**路径参数:**

| 参数 | 类型 | 描述 |
|-----------|------|------|
| `batch_name` | string | 要删除的批次名称 |

**请求体:** 无。

**成功响应 (204 No Content):**
```
HTTP/1.1 204 No Content
```

**错误响应:**
| 状态码 | 代码 | 条件 |
|--------|------|------|
| 404 | `NOT_FOUND` | 不存在具有该批次名称的未删除文件 |
| 403 | `FORBIDDEN` | 批次中至少有一个文件不可被调用者删除 |

---

#### GET /api/v1/files/batches/{batch_name}/download-zip -- 下载批次为 ZIP

将批次中所有未删除文件作为单个 ZIP 压缩包流式传输。仅包含原始文件格式（不包含转换结果）。压缩包格式由第一个文件的扩展名决定（`dwg` 或 `dxf`，默认为 `dxf`）。响应从临时文件流式传输，文件在传输完成后删除。

**授权:** 对于批次中的每个文件，调用者必须是上传者、管理员或使用了该文件的项目的成员。

**路径参数:**

| 参数 | 类型 | 描述 |
|-----------|------|------|
| `batch_name` | string | 要下载的批次名称 |

**请求体:** 无。无查询参数。

**成功响应 (200 OK):** 二进制 ZIP 流（非 JSON）。
```
HTTP/1.1 200 OK
Content-Type: application/zip
Content-Disposition: attachment; filename*=UTF-8''%E6%A5%BC%E5%B1%82%E5%9B%BE%E7%BA%B8.zip
Content-Length: 184320

<binary ZIP stream>
```

**错误响应:**
| 状态码 | 代码 | 条件 |
|--------|------|------|
| 404 | `NOT_FOUND` | 不存在具有该批次名称的未删除文件 |
| 403 | `FORBIDDEN` | 批次中至少有一个文件不可被调用者读取 |

---

#### GET /api/v1/files/{file_id}/excel-preview -- 预览 Excel 文件

将 Excel 文件内容以 JSON 格式返回，用于浏览器预览。仅支持 `.xlsx` / `.xls` 文件。第一行作为表头；空表头单元格变为 `Col A`、`Col B` 等，重复表头添加数字后缀（`Name`、`Name_2`）。结果在 Redis 中缓存 5 分钟，按文件和表单键索引。

**授权:** 上传者、管理员或使用了该文件的项目的成员。

**路径参数:**

| 参数 | 类型 | 描述 |
|-----------|------|------|
| `file_id` | int | 存储文件的 ID |

**查询参数:**

| 参数 | 类型 | 必填 | 默认值 | 描述 |
|-----------|------|------|---------|------|
| `sheet` | string | 否 | `""` (第一个工作表) | 要预览的工作表名称 |

**成功响应 (200 OK):**
```json
{
  "data": {
    "file": "楼层图纸.xlsx",
    "file_id": 2050,
    "sheets": ["Sheet1", "材料表"],
    "sheet": "Sheet1",
    "headers": ["序号", "名称", "规格", "数量"],
    "rows": [
      {"序号": 1, "名称": "角钢", "规格": "L50x5", "数量": 4},
      {"序号": 2, "名称": "钢板", "规格": "δ10", "数量": 2}
    ],
    "total_rows": 128
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
| 404 | `NOT_FOUND` | 文件不存在或已被软删除 |
| 403 | `FORBIDDEN` | 调用者缺少对该文件的读取权限 |
| 415 | `NOT_EXCEL` | 文件扩展名不是 `.xlsx` / `.xls` |
| 404 | `NOT_FOUND` | 存储后端中缺少该存储对象 |
| 503 | `STORAGE_READ_FAILED` | 存储后端无法读取文件对象 |
| 503 | `OPENPYXL_UNAVAILABLE` | `openpyxl` 未安装 |
| 415 | `EXCEL_PARSE_ERROR` | `openpyxl` 无法解析工作簿 |
| 415 | `EXCEL_EMPTY` | 工作簿中没有工作表 |
| 422 | `SHEET_NOT_FOUND` | 请求的 `sheet` 名称不在工作簿中 |

---

#### POST /api/v1/files/bulk-delete -- 批量软删除文件

按 ID 软删除多个文件。仅影响当前存在且未被删除的文件；未知或已删除的 ID 将被静默忽略。删除前按文件检查访问权限。

**授权:** 对于每个匹配的文件，调用者必须是上传者或管理员。

**请求体:**
```json
{
  "file_ids": [1001, 1002, 1003]
}
```

| 字段 | 类型 | 必填 | 约束 |
|-------|------|------|----------|
| `file_ids` | array of int | 是 | 不能为空 |

**成功响应 (204 No Content):**
```
HTTP/1.1 204 No Content
```

**错误响应:**
| 状态码 | 代码 | 条件 |
|--------|------|------|
| 422 | `INVALID_PARAMS` | `file_ids` 为空 |
| 403 | `FORBIDDEN` | 至少有一个匹配的文件不可被调用者删除 |

---

#### POST /api/v1/files/download-zip -- 下载选中文件为 ZIP

构建一个 ZIP 压缩包，包含所选源文件的请求格式版本（DWG 和/或 DXF）并直接流式传输。对于每个源文件，当其扩展名匹配时，DWG/DXF 版本来自源文件本身，否则来自成功的转换结果。文件名冲突时通过 `(1)`、`(2)` 等进行消歧。响应从临时文件流式传输，文件在传输完成后删除。

**授权:** 每个请求的文件必须可被调用者读取（上传者、管理员或项目成员）。

**请求体:**
```json
{
  "file_ids": [1001, 1002],
  "formats": ["dwg", "dxf"],
  "folder_name": "图纸导出"
}
```

| 字段 | 类型 | 必填 | 默认值 | 约束 |
|-------|------|------|---------|------|
| `file_ids` | array of int | 是 | -- | 不能为空；每个 ID 必须对应一个存在且未删除的文件 |
| `formats` | array of string | 是 | -- | 不能为空；每个元素为 `"dwg"` 或 `"dxf"` |
| `folder_name` | string | 否 | `"图纸导出"` | 压缩包内的顶层文件夹名称（经清理处理） |

**成功响应 (200 OK):** 二进制 ZIP 流（非 JSON）。
```
HTTP/1.1 200 OK
Content-Type: application/zip
Content-Disposition: attachment; filename*=UTF-8''%E5%9B%BE%E7%BA%B8%E5%AF%BC%E5%87%BA.zip
Content-Length: 262144

<binary ZIP stream>
```

**错误响应:**
| 状态码 | 代码 | 条件 |
|--------|------|------|
| 422 | `INVALID_PARAMS` | `file_ids` 为空 |
| 422 | `INVALID_PARAMS` | `formats` 为空 |
| 404 | `NOT_FOUND` | 一个或多个请求的文件 ID 不存在或已被软删除 |
| 403 | `FORBIDDEN` | 至少有一个请求的文件不可被调用者读取 |

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

#### POST /api/v1/jobs/cancel-all-active -- 取消所有活跃作业

管理员紧急停止开关。批量取消**整个系统**中所有当前处于 `queued`、`running` 或 `pending` 状态的作业（不限调用者自身作业或项目范围）。通过单次批量 SQL `UPDATE` 执行（刻意绕过 ORM，以避免与 Celery worker 同时触碰相同行时的乐观锁 / MySQL 错误 1020 冲突），然后尝试撤销活跃/已预留的 Celery 任务（`terminate=True`、`SIGTERM`）并清空 `dxf`、`report`、`agent` 和 `cad` 队列。每个被取消的行将被标记 `error_code = "CANCELLED_BY_ADMIN"` 和 `error_message = "Cancelled by administrator via bulk cancel."`。生成一条批量审计日志条目（`action = "jobs.cancel_all"`），记录 `cancelled_count` 和 `cancelled_ids`。

**授权:** 调用者必须具备全局项目访问权限（`super_admin` / `admin`）。所有其他已认证用户将收到 403。

**请求体:** 无需 (空请求体)。

**成功响应 (200 OK):**
```json
{
  "data": {
    "cancelled_count": 7,
    "celery_revoked": 3
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-08T10:00:00+00:00"
  }
}
```

| 字段 | 类型 | 描述 |
|-------|------|------|
| `cancelled_count` | int | 转换为 `cancelled` 状态的数据库行数（批量 `UPDATE` 影响的行数，针对之前处于 `queued`/`running`/`pending` 的作业） |
| `celery_revoked` | int | 已撤销的活跃 Celery 任务数（最佳努力；Celery 不可达时为 `0` —— 数据库取消仍会成功） |

注意：与其他作业操作（`cancellation-requests` / `retry-requests` 返回 **202 Accepted**）不同，此端点返回 **200 OK**（路由上未设置显式状态码）。

**错误响应:**
| 状态码 | 代码 | 条件 |
|--------|------|------|
| 403 | `FORBIDDEN` | 调用者不是 `admin` / `super_admin`（消息："Only administrators can cancel all jobs."） |
| 401 | `INVALID_TOKEN` | Token 缺失、过期或已被加入黑名单 |

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

### 3.6 系统与健康检查

#### GET /api/v1/system/health -- 深度健康检查

需认证的运行状态概览。报告 Redis 客户端可用性、三个功能开关以及活跃的存储后端。`status` 为**静态** `"ok"` 字面量（非动态计算的聚合值）。此端点**不**执行数据库探测，且尽管其文档字符串如此描述，也**不**包含 ODA 检查（请使用 `/system/health/oda` 进行 ODA 检查）。此处仅展示三个流水线开关 —— `dxf2dwg_pipeline_enabled` 和 `dxf2excel_pipeline_enabled` 不被报告。

**请求体:** 无。

**成功响应 (200 OK):**
```json
{
  "data": {
    "status": "ok",
    "redis": true,
    "features": {
      "agent": false,
      "dxf_pipeline": false,
      "cad_worker": false
    },
    "storage_backend": "local"
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-08T10:00:00+00:00"
  }
}
```

| 字段 | 类型 | 描述 |
|-------|------|------|
| `status` | string | 始终为字面量 `"ok"`（静态；不反映子系统故障） |
| `redis` | bool | 当可获取 Redis 客户端（`get_redis()` 不为 `None`）时为 `true`，否则为 `false` |
| `features.agent` | bool | `AGENT_ENABLED` 的值 |
| `features.dxf_pipeline` | bool | `DXF_PIPELINE_ENABLED` 的值 |
| `features.cad_worker` | bool | `CAD_WORKER_ENABLED` 的值 |
| `storage_backend` | string | `STORAGE_BACKEND` 的值 -- `"local"` 或 `"minio"` |

**错误响应:**
| 状态码 | 代码 | 条件 |
|--------|------|------|
| 401 | `INVALID_TOKEN` | Token 缺失、过期或已被加入黑名单 |

---

#### GET /api/v1/system/health/oda -- ODA File Converter 探针

需认证的 ODA File Converter 二进制文件环境探针，供 DXF 流水线使用。委托给 `dwg_converter.framework.health_check()`，该方法检查环境（定位 ODA 可执行文件、检查 `ezdxf` 是否可导入）并**永不抛出异常** —— 不健康的环境在响应体中报告，因此该端点始终返回 **200 OK**（永不返回 503）。响应体为 `HealthStatus.to_dict()` 的逐字输出。

**请求体:** 无。

**成功响应 (200 OK -- ODA 可用):**
```json
{
  "data": {
    "healthy": true,
    "oda_found": true,
    "oda_executable": "/opt/oda/ODAFileConverter",
    "ezdxf_available": true,
    "messages": ["ODA File Converter located", "ezdxf import OK"],
    "error_code": null
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-08T10:00:00+00:00"
  }
}
```

**成功响应 (200 OK -- ODA 缺失):**
```json
{
  "data": {
    "healthy": false,
    "oda_found": false,
    "oda_executable": null,
    "ezdxf_available": true,
    "messages": ["ODA File Converter 未找到 ..."],
    "error_code": "ODA_NOT_FOUND"
  },
  "meta": {
    "request_id": "req_...",
    "timestamp": "2026-07-08T10:00:00+00:00"
  }
}
```

| 字段 | 类型 | 描述 |
|-------|------|------|
| `healthy` | bool | 仅当 ODA 可执行文件已定位时为 `true` |
| `oda_found` | bool | 是否找到 ODA File Converter 二进制文件 |
| `oda_executable` | string \| null | 已定位的二进制文件的绝对路径；未找到时为 `null` |
| `ezdxf_available` | bool | `ezdxf`（解析阶段依赖）是否可导入 |
| `messages` | string[] | 详细的环境检查日志行 |
| `error_code` | string \| null | 不健康时为 `"ODA_NOT_FOUND"`；健康时为 `null` |

**错误响应:**
| 状态码 | 代码 | 条件 |
|--------|------|------|
| 401 | `INVALID_TOKEN` | Token 缺失、过期或已被加入黑名单 |

（此端点不发出 503：ODA 环境不可用时返回 200，响应体中包含 `healthy: false` 和 `error_code: "ODA_NOT_FOUND"`。）

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
| `PROJECT_MEMBER_EXISTS` | 409 | 用户已是该项目的成员。 |

### 7.4 文件错误

| 代码 | HTTP | 消息 |
|------|------|------|
| `FILE_TYPE_NOT_ALLOWED` | 415 | 仅接受 `.dwg` 文件。 |
| `FILE_MIME_NOT_ALLOWED` | 415 | MIME 类型不在 DWG 白名单中。 |
| `FILE_NOT_DWG` | 415 | 文件不包含有效的 DWG 头，或文件太小 (< 1024 字节)。 |
| `FILE_TOO_LARGE` | 413 | 文件超过最大上传大小。 |
| `FILE_NOT_ZIP` | 415 | 上传文件不是有效的 ZIP 压缩包。 |
| `ZIP_CORRUPTED` | 415 | ZIP 压缩包已损坏或无法打开。 |
| `ZIP_TOO_MANY_FILES` | 413 | ZIP 包含的条目数超过 `MAX_ZIP_ENTRY_COUNT`。 |
| `ZIP_TOO_LARGE` | 413 | ZIP 解压后大小超过 `MAX_ZIP_EXTRACT_MB`。 |
| `ZIP_EMPTY` | 422 | ZIP 压缩包中不包含可用文件。 |
| `NOT_EXCEL` | 415 | 仅 `.xlsx` / `.xls` 文件可被预览。 |
| `EXCEL_PARSE_ERROR` | 415 | 无法解析 Excel 文件。 |
| `EXCEL_EMPTY` | 415 | Excel 文件没有工作表。 |
| `SHEET_NOT_FOUND` | 422 | 请求的工作表不在工作簿中。 |

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
| `INVALID_STORAGE_PATH` | 400 | 路径超出配置的存储根目录范围。 |

### 7.7 作业错误

| 代码 | HTTP | 消息 |
|------|------|------|
| `JOB_NOT_CANCELLABLE` | 409 | 作业无法取消 (已处于终态)。 |
| `JOB_NOT_RETRYABLE` | 409 | 作业当前状态不能重试 (仅 failed/cancelled 可重试)。 |
| `JOB_ENQUEUE_FAILED` | 503 | 作业已创建但无法派发至 Celery。 |
| `DXF_PIPELINE_DISABLED` | 503 | DWG→DXF 流水线已禁用。设置 `DXF_PIPELINE_ENABLED=true` 以启用。 |
| `DXF2DWG_PIPELINE_DISABLED` | 503 | DXF→DWG 流水线已禁用。设置 `DXF2DWG_PIPELINE_ENABLED=true` 以启用。 |
| `DXF2EXCEL_PIPELINE_DISABLED` | 503 | DXF→Excel 流水线已禁用。设置 `DXF2EXCEL_PIPELINE_ENABLED=true` 以启用。 |
| `STUB_WORKER_FAILED` | — (异步) | Stage-1 桩 worker 抛出异常。 |
| `CANCELLED_BY_ADMIN` | — (异步) | 作业已被管理员取消。 |
| `DXF_CONVERSION_FAILED` | — (异步) | DWG→DXF ODA 转换失败。 |
| `DXF_SOURCE_MISSING` | — (异步) | DWG→DXF 作业的源 DWG 文件缺失。 |
| `DWG_CONVERSION_FAILED` | — (异步) | DXF→DWG ODA 转换失败。 |
| `DXF_SOURCE_FILE_MISSING` | — (异步) | DXF→DWG 作业的源 DXF 文件缺失。 |
| `DXF2EXCEL_EMPTY_BATCH` | — (异步) | DXF→Excel 作业的批次中没有 DXF 文件。 |
| `DXF2EXCEL_UNAVAILABLE` | — (异步) | DXF→Excel 流水线依赖不可用。 |
| `DXF2EXCEL_PIPELINE_FAILED` | — (异步) | DXF→Excel 提取失败。 |
| `DXF2EXCEL_NO_OUTPUT` | — (异步) | DXF→Excel 未生成输出文件。 |
| `DXF2EXCEL_STORAGE_FAILED` | — (异步) | 无法将 DXF→Excel 结果持久化到存储。 |

### 7.8 服务错误

| 代码 | HTTP | 消息 |
|------|------|------|
| `AGENT_DISABLED` | 503 | Agent 子系统在 Stage 1 中有意禁用。 |
| `INTERNAL_ERROR` | 500 | 发生意外的服务器错误。 |
| `VALIDATION_ERROR` | 422 | 请求校验失败 (Pydantic)。 |
| `INVALID_SORT_COLUMN` | 422 | 提供的排序列不在该资源允许的列列表中。 |
| `INVALID_SORT_RESOURCE` | 422 | 该资源类型不支持排序。 |
| `INVALID_PARAMS` | 422 | 必需的查询/请求体参数缺失或无效。 |
| `INVALID_STATUS_FILTER` | 422 | 状态过滤器必须是 `active`、`deleted` 或省略。 |
| `OPENPYXL_UNAVAILABLE` | 503 | openpyxl 未安装 — 无法预览 Excel 文件。 |
| `HTTP_ERROR` | (匹配抛出的状态码) | 全局异常处理器（`main.py`）为缺少结构化 `{code,message}` 详情的普通 `HTTPException` 发出的通用回退代码。 |

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
| `PIPELINE_STUB` | `local_stub` | Celery report-queue 假 worker（`_pipeline_for` 中的默认回退） | Stage 1 |
| `PIPELINE_DXF` | `dxf_open_source` | 通过 ODA File Converter 进行 DWG→DXF 转换，然后 ezdxf 解析（`convert_dwg_to_dxf`） | Stage 3 |
| `PIPELINE_DXF2DWG` | `dxf2dwg_open_source` | 通过 ODA File Converter 进行 DXF→DWG 反向转换（`convert_dxf_to_dwg`） | Stage 3 |
| `PIPELINE_DXF2EXCEL` | `dxf2excel` | DXF→Excel 材料表提取（`extract_dxf_to_excel`） | Stage 3 |
| `PIPELINE_CAD` | `zwcad_worker` | Windows ZWCAD 高精度流水线（仅定义；尚未被 `_pipeline_for` 选用） | Stage 4 |

---

## 11. 功能开关

所有未来阶段的功能都受 `backend/app/core/config.py` 中的布尔型功能开关控制（全部默认 `false`）。当开关关闭时，请求被拒绝并返回 HTTP 503 —— 要么在端点自身处（`AGENT_ENABLED`，在 `agent_runs_api.py` 中强制执行），要么在 `POST /api/v1/jobs` 的作业流水线选择期间（三个 DXF 开关，在 `jobs_api.py` 中强制执行）。`CAD_WORKER_ENABLED` 已定义并由系统开关端点报告，但**尚未在任何地方强制执行**（Stage 4 派发尚未接入）。要启用某项功能，在 `.env` 中将相应开关设为 `true` 并重启后端。

| 开关 | 默认值 | 控制范围 | Stage |
|------|---------|------|-------|
| `AGENT_ENABLED` | `false` | 全部 4 个 agent 端点（3 个 `/api/v1/agent-runs/*` + `/api/v1/agent-tools`）；关闭时每个端点返回 503 `AGENT_DISABLED`（在 `agent_runs_api.py` 中强制执行） | Stage 2 |
| `DXF_PIPELINE_ENABLED` | `false` | `POST /api/v1/jobs` 中的 DWG→DXF 选择（`task_type=convert_dwg_to_dxf`）；关闭时返回 503 `DXF_PIPELINE_DISABLED`（在 `jobs_api.py` 中强制执行） | Stage 3 |
| `DXF2DWG_PIPELINE_ENABLED` | `false` | `POST /api/v1/jobs` 中的 DXF→DWG 反向选择（`task_type=convert_dxf_to_dwg`）；关闭时返回 503 `DXF2DWG_PIPELINE_DISABLED`（在 `jobs_api.py` 中强制执行） | Stage 3 |
| `DXF2EXCEL_PIPELINE_ENABLED` | `false` | `POST /api/v1/jobs` 中的 DXF→Excel 选择（`task_type=extract_dxf_to_excel`）；关闭时返回 503 `DXF2EXCEL_PIPELINE_DISABLED`（在 `jobs_api.py` 中强制执行） | Stage 3 |
| `CAD_WORKER_ENABLED` | `false` | 预留给 CAD Worker 派发选择；目前仅由系统开关端点（`system_api.py`）展示 —— `jobs_api.py` / `job_service.py` 中尚未接入 503 门控 | Stage 4 |

---

## 12. OpenAPI Schema

交互式 API 文档可通过以下地址访问：

```
http://localhost:8000/docs     (Swagger UI)
http://localhost:8000/redoc    (ReDoc)
```

所有请求/响应 schema 均由 Pydantic v2 模型生成，反映实时 API 接口。
