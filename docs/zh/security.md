# DWG-Agent 平台 -- 安全架构

> **目标读者：** 安全审计人员、平台运维人员、私有化部署工程师
> **最后更新：** 2026-07-03
> **范围：** 身份认证、RBAC、API 安全、文件安全、渗透测试修复、部署清单、审计日志覆盖范围

---

## 1. 身份认证流程

### 1.1 登录

```
POST /api/v1/auth/sessions
{
  "username": "10001",
  "password": "********"
}
```

认证路径如下：

1. **用户名查询** -- 通过 `username` 列查询 `sys_users` 表。
2. **常量时间验证** -- `auth_service.py` 中的 `authenticate_user()` 始终执行一次完整的 Argon2id 验证：
   - 若用户存在且状态为 `active`，则与存储的 `password_hash` 进行验证。
   - 若用户不存在或状态为 `disabled`/`deleted`，则与一个硬编码的虚拟 Argon2id 哈希进行验证。
   - 这消除了时序侧信道（此前拒绝不存在用户的响应速度快 40 倍）。参见渗透测试发现 H1。
3. **成功后颁发令牌：**
   - **访问令牌：** JWT HS256，`sub` = 用户 ID，`jti` = 随机 UUID4，`type` = `"access"`，有效期 = 30 分钟。在 JSON 响应体中返回。
   - **刷新令牌：** JWT HS256，`sub` = 用户 ID，`jti` = 随机 UUID4，`type` = `"refresh"`，有效期 = 14 天。以 `HttpOnly; SameSite=Lax` Cookie 设置在 `/api/v1/auth` 路径上。`Secure` 标志仅在 `APP_ENV=production` 时设置（开发模式下不设置）。
4. **登录响应** 包含 `access_token`、`token_type`（"Bearer"）、`expires_in`（1800 秒）以及摘要用户对象。

### 1.2 令牌结构

两种令牌类型共享相同的负载结构：

```json
{
  "sub": "1",
  "username": "admin",
  "jti": "a1b2c3d4-...",
  "iat": 1751500000,
  "exp": 1751501800,
  "type": "access"
}
```

- **`sub`：** 用户 ID（字符串化的整数）。
- **`jti`：** 唯一令牌标识符，用于黑名单机制。缺少 `jti` 的令牌仍可被接受，但会记录警告日志（发布前兼容性）。
- **`type`：** `"access"` 或 `"refresh"` -- `get_current_user` 依赖项会拒绝刷新令牌。
- **算法：** HS256，使用环境变量中的 `JWT_SECRET_KEY`。

### 1.3 令牌刷新

```
POST /api/v1/auth/tokens/refresh
```

- 刷新令牌从 `refresh_token` Cookie 中读取。
- 颁发新的访问令牌。刷新令牌本身**不会被轮换**（参见第 5.3 节，剩余缺陷）。

### 1.4 登出

```
DELETE /api/v1/auth/sessions/current
```

- 提取访问令牌的 `jti` 并存入 Redis，TTL = 令牌剩余有效期（`exp - now`）。
- 刷新令牌的 `jti` 同样被加入黑名单。
- Redis 键遵循 `blacklist:jti:{jti}` 模式 -- 它们在 TTL 到期后自动过期，无需清理作业。
- 如果 Redis 不可用，黑名单操作将被静默跳过（降级模式，记录警告日志）。

### 1.5 每次请求的令牌校验

每个经过认证的请求都经过 `app/api/deps.py` 中的 `get_current_user()` 处理：

1. 解码并验证 JWT 签名。
2. 若 `type` != `"access"` 则拒绝。
3. 在 Redis 黑名单中检查 `jti` -- 若已被列入黑名单则返回 401 `TOKEN_REVOKED`。
4. 通过 `sub` 在数据库中查找用户。
5. 若用户不存在或 `status` != `"active"` 则拒绝。
6. 检查令牌是否在最后一次密码修改之前签发 -- 若令牌已过期则返回 401 `TOKEN_REVOKED`（密码已修改）。当用户修改密码后，此检查将使所有设备上的全部令牌失效。

### 1.6 密码管理

- **哈希算法：** Argon2id，通过 `pwdlib.PasswordHash.recommended()` 配置（m=65536, t=3, p=4）。
- **算法存储：** `sys_users` 表中的 `password_algo = "argon2id"`。
- **最小长度：** 12 个字符（在 Pydantic schema 中强制）。
- **复杂度：** 必须包含至少一个大写字母、一个小写字母和一个数字。
- **常见密码黑名单：** 拒绝内置的常见/已泄露密码列表中的密码。
- **密码修改：** `PATCH /api/v1/auth/password` -- 需要旧密码验证，写入审计日志。
- **管理员重置：** `POST /api/v1/users/{user_id}/password-reset-requests` -- 仅限管理员，生成审计记录。

---

## 2. RBAC 模型

### 2.1 五张权限表

```
sys_users  ──< sys_user_roles  >── sys_roles  ──< sys_role_permissions  >── sys_permissions

                                    ┌─────────────────────────────┐
                                    │ sys_users                   │
                                    │  id, username, status       │
                                    │  active / disabled / deleted│
                                    └──────────┬──────────────────┘
                                               │
                                    ┌──────────▼──────────────────┐
                                    │ sys_user_roles               │
                                    │  user_id FK, role_id FK      │
                                    │  PK: (user_id, role_id)      │
                                    └──────────┬──────────────────┘
                                               │
                 ┌─────────────────────────────▼──────┐
                 │ sys_roles                           │
                 │  code, is_system                    │
                 │  super_admin, admin, engineer, ...  │
                 └──────────┬─────────────────────────┘
                            │
                 ┌──────────▼─────────────────────────┐
                 │ sys_role_permissions                │
                 │  role_id FK, permission_id FK       │
                 │  PK: (role_id, permission_id)       │
                 └──────────┬─────────────────────────┘
                            │
                 ┌──────────▼─────────────────────────┐
                 │ sys_permissions                      │
                 │  code, resource, action              │
                 │  例如 "users:read", "jobs:write"     │
                 └──────────────────────────────────────┘
```

### 2.2 七个全局角色

| 角色代码 | 显示名称 | 典型能力 |
|---|---|---|
| `super_admin` | 超级管理员 | 绕过**所有**权限检查。完全系统访问权限。 |
| `admin` | 系统管理员 | 用户管理、项目管理、作业管理。拥有 `is_admin()` 权限（等同于 `has_global_project_access`）。 |
| `engineer` | 工程师 | 在其所属项目内上传文件、创建任务、查看项目结果。 |
| `reviewer` | 审核员 | 审核分析结果，提交批准/拒绝决策。 |
| `operator` | 操作员 | 在其所属项目内执行分配的任务。 |
| `viewer` | 查看者 | 对分配的项目仅有只读访问权限。 |
| `auditor` | 审计员 | 对审计日志和系统配置仅有只读访问权限。 |

### 2.3 四个项目级角色

| 项目角色 | 项目内访问级别 |
|---|---|
| `project_owner` | 完全控制项目、其成员、文件、图纸、作业和结果。 |
| `project_engineer` | 可以上传文件、创建图纸、提交作业、查看结果。 |
| `project_reviewer` | 可以审核为该项目提交的分析结果。 |
| `project_viewer` | 对项目及其资源仅有只读访问权限。 |

### 2.4 权限决策树

```
                    ┌──────────────────────────────────────────┐
                    │         传入的 API 请求                  │
                    │   （所有业务端点均需认证）                 │
                    └────────────────────┬────────────────────┘
                                         │
                                         ▼
                    ┌─────────────────────────────────────────┐
                    │  1. 访问令牌是否有效？                   │
                    │     - JWT 签名已验证？                   │
                    │     - type == "access"？                 │
                    │     - jti 未被列入黑名单？               │
                    └────────────────────┬────────────────────┘
                              ┌─────────┴──────────┐
                              │ 是                   │ 否 → 401 (INVALID_TOKEN / TOKEN_REVOKED)
                              ▼                      │
                    ┌─────────────────────────────────────────┐
                    │  2. 用户是否处于活跃状态？               │
                    │     - 用户在数据库中是否存在？           │
                    │     - user.status == "active"？          │
                    └────────────────────┬────────────────────┘
                              ┌─────────┴──────────┐
                              │ 是                   │ 否 → 401 (USER_NOT_ACTIVE)
                              ▼                      │
                    ┌──────────────────────────────────────────┐
                    │  3. 用户是否拥有授予访问权限的           │
                    │     全局角色？                            │
                    │     - super_admin → 绕过所有检查         │
                    │     - admin → 全局项目访问权限           │
                    │     - role_codes ∩ required_roles ≠ ∅    │
                    └────────────────────┬────────────────────┘
                              ┌─────────┴──────────┐
                              │ 是                   │ 否 → 继续步骤 4
                              ▼                      ▼
                    ┌──────────────┐   ┌─────────────────────────────────────────┐
                    │  访问        │   │  4. 资源是否限定在某个项目范围内？       │
                    │  已授权      │   │     （路径/请求体中存在 project_id）     │
                    └──────────────┘   └────────────────────┬────────────────────┘
                                               ┌───────────┴──────────┐
                                               │ 是                    │ 否 → 403
                                               ▼                       │
                                    ┌─────────────────────────────────────────┐
                                    │  5. 用户是否是该项目的成员？             │
                                    │     - 检查 project_members 表           │
                                    │     - 项目必须处于活跃状态（未软删除）   │
                                    └────────────────────┬────────────────────┘
                                              ┌──────────┴──────────┐
                                              │ 是                    │ 否 → 403
                                              ▼                       │
                                    ┌─────────────────────────────────────────┐
                                    │  6. 项目角色是否允许此                  │
                                    │     特定操作？                           │
                                    │     - 例如 project_viewer 不能 POST     │
                                    │     - 例如 project_engineer 可以上传    │
                                    └────────────────────┬────────────────────┘
                                              ┌──────────┴──────────┐
                                              │ 是                    │ 否 → 403
                                              ▼                       │
                                    ┌──────────────┐                │
                                    │  访问        │                │
                                    │  已授权      │                │
                                    └──────────────┘                │
```

### 2.5 关键权限实现细节

- **`require_roles(*allowed_roles)`：** FastAPI 依赖项。若用户的角色中包含 `super_admin`，则立即授予访问权限。否则检查与 `allowed_roles` 的交集。
- **`is_admin(user)`：** 对 `super_admin` 或 `admin` 返回 True。用作 `has_global_project_access` 的门控。
- **`has_global_project_access(user)`：** `super_admin` 和 `admin` 可查看所有项目，绕过项目成员检查。
- **`require_project_member(db, user, project_id)`：** 检查 `project_members` 表。若用户拥有全局访问权限则跳过。同时验证项目处于活跃状态（未被软删除）-- 这修复了 BUG-7 软删除级联问题。
- **`require_project_role(db, user, project_id, allowed_roles)`：** 检查项目成员身份并验证成员的 `project_role` 是否在允许的集合中。
- **自操作保护：**
  - 不能删除或禁用自己的账户。
  - 非 `super_admin` 用户不能管理 `super_admin` 账户。
- **`transition_user_status()`：** 使用 `UPDATE ... WHERE id = :id AND status != 'deleted'` 并检查 `rowcount`。同时支持通过 `get_user_or_404(for_update=True)` 使用 `FOR UPDATE`。有效消除了 SELECT→UPDATE 的 TOCTOU 时间窗口。

### 2.6 种子权限

| 权限代码 | 资源 | 操作 | 说明 |
|---|---|---|---|
| `users:read` | users | read | 查看用户 |
| `users:write` | users | write | 管理用户 |
| `roles:write` | roles | write | 管理角色 |
| `projects:write` | projects | write | 管理项目 |
| `files:write` | files | write | 上传/删除文件 |
| `jobs:write` | jobs | write | 创建/管理作业 |
| `reviews:write` | reviews | write | 提交审核 |
| `audit_logs:read` | audit_logs | read | 查看审计日志 |

所有 8 项权限均在种子数据创建时授予 `super_admin`。

---

## 3. API 安全措施

### 3.1 认证强制

- **所有业务端点均需要 `current_user: CurrentUser`** -- 没有任何端点接受 `= None` 作为默认值。
- 唯一不需要认证的端点是 `POST /auth/sessions`（登录）、`POST /auth/tokens/refresh` 和 `GET /health`。
- `OAuth2PasswordBearer` 自动提取 `Authorization: Bearer <token>` 请求头。
- WebSocket 和 SSE 端点（用于作业事件）同样在连接时验证令牌。

### 3.2 CORS 策略

```python
allow_origins = settings.cors_origins          # 来自 BACKEND_CORS_ORIGINS 环境变量
allow_credentials = True                        # HttpOnly Cookie 所需
allow_methods = ["GET", "POST", "PATCH", "PUT", "DELETE"]  # OPTIONS 自动添加
allow_headers = ["Authorization", "Content-Type"]
```

值得注意：`allow_methods` 被显式枚举（而非 `["*"]`）。`OPTIONS`、`HEAD`、`TRACE`、`CONNECT` 不会暴露。`allow_headers` 同样被显式列出 -- CORS 中间件会拒绝任意请求头。

### 3.3 输入验证

- **所有输入均通过 Pydantic v2 模型** 处理，配置为 `model_config = ConfigDict(from_attributes=True)`。
- `RequestValidationError` 被全局处理器捕获并返回 422 及结构化的错误详情（绝不暴露原始 Pydantic 堆栈跟踪）。
- 特定字段级别的约束：
  - **用户名：** `^[a-zA-Z0-9_.@-]+$`（修复 H6：通过空格/Unicode 进行用户名注入）。
  - **真实姓名：** HTML 标签拒绝（修复 BUG-3：HTML 注入）。
  - **密码：** min_length=12，需要大写+小写+数字，常见密码黑名单（修复 BUG-2）。
  - **task_type：** `^[a-z][a-z0-9_]+$` 模式（修复 BUG-8）。
  - **email：** 有效的 `EmailStr` 格式。

### 3.4 异常处理与信息泄露

四个异常处理器覆盖了完整的错误面：

| 处理器 | 状态码 | 行为 |
|---|---|---|
| `AppHTTPException` | 可变 | 将业务错误代码/消息/详情格式化为标准错误信封。 |
| `StarletteHTTPException` | 可变 | 捕获框架级别的 HTTP 错误（例如 405 Method Not Allowed）。 |
| `RequestValidationError` | 422 | 返回结构化的 Pydantic 错误详情。 |
| `Exception`（兜底捕获） | 500 | 内部记录完整堆栈跟踪。当 `debug=False` 时返回 `"Internal server error."`。仅在 `debug=True` 时返回 `str(exc)`。**绝不泄露堆栈跟踪。** |

健康检查端点返回 `{"data": {"status": "ok"}, "meta": {"request_id": "...", "timestamp": "..."}}` -- 不包含数据库状态、版本、运行时间或依赖项信息（修复 BUG-4）。

### 3.5 资源隔离

- **管理员用户**（`super_admin`、`admin`）：可以列出和访问所有项目、文件、图纸、作业、结果。
- **普通用户：** 只能看到其所属项目。文件、图纸、作业和结果按项目成员身份过滤。
- **文件下载：** 需要全局项目访问权限或文件关联项目的成员身份。在触及存储层之前，API 层即会拒绝跨项目文件访问。

### 3.6 竞态条件防护

- **用户创建：** 捕获重复用户名的 `IntegrityError` 并转换为 409 `USERNAME_EXISTS`（修复 BUG-6）。
- **状态转换：** `transition_user_status()` 使用 `UPDATE ... WHERE` 并检查 rowcount -- 不存在 SELECT 后 UPDATE 的时间间隙。
- **`FOR UPDATE`：** 可通过 `get_user_or_404(for_update=True)` 在需要时使用悲观锁。

---

## 4. 文件安全措施

### 4.1 上传验证链路

每个文件上传按顺序经过以下流水线：

```
1. 扩展名白名单    → 仅 .dwg（ALLOWED_UPLOAD_EXTENSIONS = {".dwg"}）
2. MIME 类型检查   → 8 种可接受的 DWG 相关 MIME 类型（application/acad, application/dwg 等）
3. DWG 文件头验证  → 前 6 个字节必须匹配 AC1012-AC1032（AutoCAD R13 至 2018+）
4. 大小限制        → 最大值：max_upload_size_mb（默认 512 MiB），最小值：1024 字节
5. 流式哈希        → 分块读取时计算 SHA-256 + MD5
6. 临时缓冲清理    → SpooledTemporaryFile 在使用后自动清理内存/OS 缓冲区。然而，若存储后端写入（`put_fileobj`）在中途失败，存储后端（如 MinIO 或本地文件系统）中可能会遗留部分不完整的文件 -- 应用层不会尝试从后端移除已部分写入的文件。
```

### 4.2 支持的 DWG 版本

| 魔数 | AutoCAD 版本 |
|---|---|
| `AC1012` | R13 |
| `AC1014` | R14 |
| `AC1015` | 2000 / 2000i / 2002 |
| `AC1018` | 2004 / 2005 / 2006 |
| `AC1021` | 2007 / 2008 / 2009 |
| `AC1024` | 2010 / 2011 / 2012 |
| `AC1027` | 2013-2017 |
| `AC1032` | 2018+ |

文件头不在此集合内的文件将被拒绝，返回 415 `FILE_NOT_DWG`。

### 4.3 存储路径安全

- **存储路径绝不使用用户提供的文件名。** `storage_key` 的格式为 `uploads/{uuid4().hex}{ext}`。
- **`original_name`** 仅作为元数据存储，绝不拼接到文件路径中。
- **路径穿越防护：** `ensure_within_root(root, candidate)` 解析两个路径并检查候选路径的解析结果是否以根路径的解析结果为前缀。任何逃逸尝试都会引发 400 `INVALID_STORAGE_PATH`。
- **原始文件绝不覆盖。** 每次上传都会创建新的存储键。

### 4.4 下载安全

- **HMAC 签名的下载 URL**（`GET /files/{file_id}/download-url`）：URL 包含 `expires`（TTL=300s）和 `signature` 参数。签名是对 `file_id:expires` 的 HMAC-SHA256 计算值。
- **URL 生成前的权限检查：** 调用者必须拥有文件所属项目的访问权限（或全局访问权限）。在任何 URL 生成之前即拒绝跨项目下载请求。
- **注意：** 签名 URL 的 TTL 在后端下载时被强制校验，但该 URL 本身并非一个密码学上自包含的能力令牌 -- 下载端点同样需要认证（参见第 5.3 节，剩余缺陷）。

### 4.5 文件哈希

- **SHA-256：** 主要完整性哈希，存储于 `files.sha256`，建立索引以支持去重查询。
- **MD5：** 次要哈希，用于遗留兼容性，存储于 `files.md5`。
- 两者均在流式上传过程中计算（对文件数据单次遍历）。

---

## 5. 渗透测试发现处置

### 5.1 已修复（18 项中的 12 项）

| ID | 发现 | 严重程度 | 修复方案 | 文件 |
|---|---|---|---|---|
| H1 | 时序预言机 -- 通过登录进行用户枚举的 40 倍时间差 | **严重** | 当用户不存在/不活跃时使用虚拟 Argon2id 哈希。两条代码路径均执行一次完整的 argon2id 验证。 | `app/services/auth_service.py` |
| H6 | 通过空格和 Unicode 字符进行用户名注入 | **高危** | 在 Pydantic schema 的 `username` 字段上添加模式约束 `^[a-zA-Z0-9_.@-]+$`。 | `app/schemas/user_schema.py` |
| BUG-1 | 通过 `UserCreate` 批量赋值 `role_codes` | **高危** | 从 `UserCreate` schema 中移除 `role_codes` 字段。角色分配现在通过独立的 `POST /users/{id}/roles` 端点进行，受 RBAC 保护。 | `app/schemas/user_schema.py` |
| BUG-2 | 弱密码策略 -- 无最小长度或复杂度要求 | **高危** | `min_length=12`，需要大写+小写+数字，常见密码黑名单。 | `app/schemas/user_schema.py` |
| BUG-3 | `real_name` 字段中的 HTML 注入 | **中危** | 在 Pydantic 验证器中拒绝 HTML 标签模式。 | `app/schemas/user_schema.py` |
| BUG-4 | 健康检查端点信息泄露 -- 数据库状态、版本 | **低危** | 简化为 `{"data": {"status": "ok"}}`。 | `app/main.py` |
| BUG-5 | DWG 大小验证过小 -- 接受小于 1024 字节的文件 | **中危** | 上传后强制 `MIN_DWG_SIZE_BYTES = 1024`，结合文件头验证。 | `app/services/storage_service.py` |
| BUG-6 | 竞态条件导致 500 错误并泄露堆栈跟踪 | **中危** | 捕获 `IntegrityError` 并转换为 409。兜底 `Exception` 处理器在生产环境返回 `"Internal server error."`。 | `app/services/user_service.py`、`app/main.py` |
| BUG-7 | 软删除级联 -- 已删除项目仍在文件列表中可见 | **中危** | 在 `require_project_member()` 中添加 `require_active_project()` 检查。文件列表按项目成员身份过滤。 | `app/api/deps.py` |
| BUG-8 | `task_type` 字段未验证 -- 接受任意字符串 | **低危** | 模式约束 `^[a-z][a-z0-9_]+$`。 | `app/schemas/job_schema.py` |
| BUG-9 | 无状态保护的重试 -- 任何作业都可被重试 | **中危** | 仅 `failed` 或 `cancelled` 状态的作业可重试。 | `app/services/job_service.py` |
| BUG-12 | 用户无自助更新端点 | **低危** | 新增 `PATCH /users/me`，使用 `UserSelfUpdate` schema（不允许更改状态）。 | `app/api/v1/users_api.py` |

### 5.2 按设计不修复（18 项中的 6 项）

| ID | 发现 | 理由 |
|---|---|---|
| BUG-10 | 纳秒级 TOCTOU 时间窗口 | 实际风险可忽略 -- 在 Web 应用场景中该窗口太小，无法可靠利用。不值得引入应用级可序列化事务的复杂度。 |
| BUG-11 | 根本原因不明，无法复现 | 多次尝试后无法复现。无遥测数据可供诊断。已归档供生产环境监控。 |
| BUG-13 | 当前 API 中不存在该参数 | 发现中引用的参数在任何已部署的 API 端点中均不存在。该发现可能针对的是过时/预发布版本。 |
| BUG-14 | 当前 API 中不存在该参数 | 同 BUG-13。 |
| C1 | JWT 密钥强度 | 部署层面的问题，非代码问题。生产部署必须使用密码学安全的随机密钥（参见清单 6.1）。 |
| C2 | 端口 8000 暴露 | 基础设施层面的问题。Docker Compose 将 backend-api 仅放置在 `internal` 网络上。Nginx 是面向公网的服务，监听 80/443 端口。若不使用 Docker 部署，请遵循清单（第 6.5 节）。 |

### 5.3 剩余缺陷（已确认，尚未解决）

| 缺陷 | 影响 | 缓解措施 |
|---|---|---|
| **令牌黑名单中间件** | 登出时被列入黑名单的访问令牌在每次请求时都会被检查（通过 `get_current_user` 中的 `is_token_blacklisted(jti)`），这是正确的做法。但是，除了 Redis TTL 自动过期外，没有对黑名单的定期清理。 | 可接受 -- Redis TTL 会自动清理键。 |
| **无登录速率限制** | 暴力登录尝试不被限流。时序预言机修复（H1）阻止了用户枚举，但大规模密码猜测仍然可能。 | **生产环境必须添加速率限制**（例如 slowapi 或 nginx `limit_req_zone`）。建议：每 IP 每分钟 5 次尝试，逐步升级锁定。 |
| **无刷新令牌轮换** | 若刷新令牌被窃取，攻击者可在长达 14 天内持续生成新的访问令牌。 | **考虑实现令牌轮换** -- 每次使用刷新令牌时颁发新的刷新令牌，并使旧的失效。这是标准的 OAuth 2.0 最佳实践。 |
| **签名下载 URL** | HMAC 签名的 URL 包含 TTL=300s 的 `expires` 参数，但下载端点还会检查认证。该 URL 并非独立的能力令牌。 | 这实际上是纵深防御的选择，但这意味着签名并未提供预期的时限匿名访问能力。评估是否真正需要过期能力 URL。 |
| **无审计日志保留策略** | 审计日志在数据库中无限制增长。 | 添加保留策略（例如将超过 N 个月的日志归档）。 |

---

## 6. 生产部署安全清单

### 6.1 密钥管理

- [ ] **`JWT_SECRET_KEY`**：使用 `openssl rand -hex 32` 生成。必须至少具有 256 位熵。
- [ ] **`SUPER_ADMIN_PASSWORD`**：在任何用户创建之前，从种子默认值更改。
- [ ] **`MYSQL_PASSWORD`**、**`MYSQL_ROOT_PASSWORD`**：使用强密码，且各自唯一。
- [ ] **`REDIS_PASSWORD`**：在生产环境中设置（Redis AUTH）。
- [ ] **`MINIO_ROOT_USER`**、**`MINIO_ROOT_PASSWORD`**：使用强密码，且各自唯一。
- [ ] **`.env` 和 `.env.docker`**：绝不提交到 Git。验证 `.gitignore` 已包含这些文件。
- [ ] **`MODEL_API_KEY`**：若启用了 Agent 功能，必须设置 LLM API 密钥。

### 6.2 网络安全

- [ ] **数据库端口（3306）**：不暴露到公网。Docker：仅在 `internal` 网络上。
- [ ] **Redis 端口（6379）**：不暴露。Docker：仅在 `internal` 网络上。
- [ ] **MinIO 端口（9000, 9001）**：不暴露。Docker：仅在 `internal` 网络上。
- [ ] **后端端口（8000）**：不直接暴露。所有流量经过 Nginx。
- [ ] **Nginx**：仅暴露 80 和 443 端口。生产环境中将 HTTP 重定向到 HTTPS。
- [ ] **CAD Worker 节点**：隔离网络，需要 API Key 认证（规范第 19.4 节）。

### 6.3 TLS/HTTPS

- [ ] 获取 TLS 证书（Let's Encrypt 或内部 CA）。
- [ ] 在 Nginx 中配置 `ssl_certificate` 和 `ssl_certificate_key`。
- [ ] 在 Cookie 上设置 `secure` 标志（`refresh_token` Cookie 的代码中已包含）。
- [ ] 在 Nginx 中设置 HSTS 头部。

### 6.4 应用加固

- [ ] **`DEBUG=false`**：生产环境中必须设置（防止兜底处理器泄露堆栈跟踪）。
- [ ] **CORS 来源**：将 `BACKEND_CORS_ORIGINS` 仅设置为生产前端域名 -- 不可为 `*`。
- [ ] **上传大小限制**：适当设置 `MAX_UPLOAD_SIZE_MB`（默认 512 MiB）。
- [ ] **登录速率限制**：部署速率限制中间件（例如 slowapi）或为 `/api/v1/auth/sessions` 配置 Nginx `limit_req_zone`。
- [ ] **刷新令牌轮换**：评估按第 5.3 节实现。

### 6.5 数据库安全

- [ ] MySQL 用户 `dwg_user` 仅拥有所需权限（对 `dwg_agent.*` 的 SELECT、INSERT、UPDATE、DELETE）。
- [ ] MySQL root 密码安全存储，不被应用程序使用。
- [ ] 配置定期备份（参见 `docs/database.md`，第 6 节）。
- [ ] 若 MySQL 位于独立主机，则连接使用 `mysql+pymysql` 并启用 TLS。

### 6.6 Docker 安全

- [ ] 后端容器以非 root 用户运行（生产 `Dockerfile` 包含非 root `USER` 指令）。
- [ ] 生产部署时使用 `--no-cache` 构建镜像。
- [ ] Docker 套接字不挂载到任何容器中。
- [ ] 设置容器资源限制（CPU、内存）以防止资源耗尽。

### 6.7 日志与监控

- [ ] 审计日志涵盖：用户 CRUD、角色变更、登录/登出、密码修改、文件上传、作业创建、审核决策、Agent 运行。
- [ ] 应用日志包含 `request_id`、`user_id` 和资源 ID 以便追踪。
- [ ] 配置日志聚合（例如 Docker 日志驱动 → ELK/Loki）。
- [ ] 配置告警：重复的 401/403 响应、高错误率、异常文件上传模式。

---

## 7. 审计日志覆盖范围

### 7.1 审计日志 schema

```text
audit_logs
├── id              BIGINT 主键
├── actor_user_id   BIGINT 外键 → sys_users.id（可为空 -- 用于系统操作）
├── action          VARCHAR(128)    例如 "user.create"、"file.upload"、"auth.logout"
├── resource_type   VARCHAR(64)     例如 "user"、"project"、"file"、"job"、"result"
├── resource_id     BIGINT          受影响资源的 ID
├── ip_address      VARCHAR(64)     请求中的客户端 IP
├── user_agent      VARCHAR(512)    User-Agent 请求头
├── before_json     JSON            操作前的资源状态（用于更新/删除）
├── after_json      JSON            操作后的资源状态（用于创建/更新）
├── created_at      DATETIME        操作的时间戳
```

### 7.2 产生审计记录的操作

| 操作代码 | 资源类型 | 触发条件 |
|---|---|---|
| `auth.login` | user | 用户登录成功 |
| `auth.logout` | user | 用户登出 |
| `auth.password_change` | user | 用户修改自己的密码 |
| `users.create` | user | 管理员创建新用户 |
| `users.update` | user | 管理员修改用户详情 |
| `users.update_self` | user | 用户通过 /users/me 更新自己的个人资料 |
| `users.delete` | user | 管理员软删除用户 |
| `users.disable` | user | 管理员禁用用户账户 |
| `users.enable` | user | 管理员重新启用用户账户 |
| `users.password_reset` | user | 管理员重置用户密码 |
| `users.roles.add` | user | 管理员为用户分配角色 |
| `users.roles.remove` | user | 管理员移除用户的角色 |
| `roles.create` | role | 超级管理员创建新角色 |
| `roles.permissions.replace` | role | 超级管理员更新角色的权限 |
| `projects.create` | project | 用户创建项目 |
| `projects.update` | project | 用户修改项目详情 |
| `projects.delete` | project | 用户软删除/归档项目 |
| `project_members.create` | project | 项目所有者向项目添加成员 |
| `project_members.update` | project_member | 项目所有者更改成员的项目角色 |
| `project_members.delete` | project_member | 项目所有者从项目中移除成员 |
| `files.upload` | file | 用户上传 DWG 文件 |
| `files.delete` | file | 用户删除文件 |
| `files.download_url` | file | 用户请求签名下载 URL |
| `files.download` | file | 用户通过签名 URL 下载文件 |
| `drawings.create` | drawing | 用户创建图纸 |
| `drawings.update` | drawing | 用户修改图纸元数据 |
| `drawings.delete` | drawing | 用户归档图纸 |
| `drawing_versions.create` | drawing | 用户上传新图纸版本 |
| `jobs.create` | job | 用户提交处理作业 |
| `jobs.cancel` | job | 用户取消作业 |
| `jobs.retry` | job | 用户重试失败/已取消的作业 |
| `agent_runs.create` | agent_run | 用户创建 Agent 运行 |
| `reviews.create` | result | 审核员批准或拒绝分析结果 |

### 7.3 审计日志的访问控制

- **`GET /api/v1/audit-logs`**：需要 `super_admin` 或 `auditor` 全局角色。
- **`GET /api/v1/audit-logs/{audit_log_id}`**：相同的访问控制。
- 审计日志是**不可变的** -- 没有任何 API 端点可以修改或删除它们。删除需要 DBA 直接访问数据库。
- 对于系统发起的操作（例如种子数据创建、自动清理），`actor_user_id` 可以为 `NULL`。

### 7.4 审计日志查询注意事项

- `action` 和 `resource_id` 列已建立索引以便高效过滤。
- `before_json` 和 `after_json` 列记录完整快照 -- 这对调查很有价值，但可能增长很大。考虑生产环境的归档策略。
- 对于 GDPR/隐私合规，`ip_address` 列捕获了个人身份信息（PII）。确保您的隐私政策和保留计划对此有所考虑。
