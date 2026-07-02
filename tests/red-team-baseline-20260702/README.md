# DWG-Agent Platform — Red Team Baseline Assessment

**Target:** `http://localhost:8080`  
**Scope:** `/login` 入口及全部可达 API 面  
**Date:** 2026-07-02  
**Tester:** Claude (automated red team skill)  
**Constraints:** 无明文密码爆破 / 无破坏性操作 / 已清理测试数据  

---

## 1. 执行摘要

| 等级 | 数量 | 关键项 |
|------|------|--------|
| 🔴 Critical | 1 | 默认超级管理员凭据生效 |
| 🟠 High | 2 | Debug 模式泄露完整调用栈 / 生产环境 debug=true |
| 🟡 Medium | 4 | JWT 密钥长度不足 / Token 存 localStorage / 无 CSP / CORS credentials |
| 🔵 Low | 3 | SPA 全路由返回 200 / Redis 不可用 / 未实现的端点暴露 |
| ✅ Passed | 10 | SQL 注入 / NoSQL 注入 / 用户名枚举 / RBAC / Mass Assignment / JWT none 算法 / 密码哈希 / Nginx 限流 / 安全头 / 无注册端点 |

**总体风险:** 中高危 — 核心问题在于**凭据管理**和**调试配置残留**，应用层防御较完善。

---

## 2. 目标信息

| 属性 | 值 |
|------|-----|
| 应用 | DWG-Agent Platform (React 19 SPA) |
| 前端框架 | React + TypeScript + Vite + Ant Design 6 |
| 后端框架 | FastAPI (Python 3.12), Starlette |
| 反向代理 | nginx/1.30.3 |
| 数据库 | SQLite (dev), MySQL (prod) |
| 认证方式 | JWT Bearer Token (HS256) + argon2id |
| 状态管理 | Zustand (token 存在 localStorage) |
| 部署方式 | 本地开发 (非 Docker) |
| 源码路径 | `backend/`（仓库根目录下的 backend 子目录） |

### API 路由清单

```
/api/v1/auth/sessions          POST    登录 (唯一认证入口)
/api/v1/auth/sessions/current  DELETE  登出 (需认证)
/api/v1/auth/tokens/refresh    POST    刷新令牌 (501 未实现)
/api/v1/auth/me                GET     当前用户信息 (需认证)
/api/v1/auth/password          PATCH   修改密码 (501 未实现)
/api/v1/users                  GET     用户列表 (需 admin 角色)
/api/v1/users                  POST    创建用户 (需 admin 角色)
/api/v1/users/{id}             GET     用户详情 (需 admin 角色)
/api/v1/users/{id}             PATCH   更新用户 (需 admin 角色)
/api/v1/users/{id}             DELETE  删除用户 (需 admin 角色)
/api/v1/users/{id}/roles       POST    分配角色 (需 admin 角色)
/api/v1/roles                  GET     角色列表
/api/v1/projects               GET     项目列表 (需认证)
/api/v1/files                  GET     文件列表 (需认证)
/api/v1/jobs                   GET     任务列表 (需认证)
/api/v1/drawings               GET     图纸列表 (需认证)
/api/v1/results                GET     结果列表 (需认证)
/api/v1/reviews                GET     复核列表 (需认证)
/api/v1/audit-logs             GET     审计日志 (需认证)
/api/v1/agent-runs             GET     Agent 运行 (需认证)
/health                        GET     健康检查 (无需认证)
/api/v1/health                 GET     健康检查 (无需认证)
```

---

## 3. 漏洞详情

### 🔴 CRITICAL-01: 默认超级管理员凭据生效

**文件:** `backend/app/core/config.py:37-38`  
**CVSS 评分:** CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H (10.0)

```
super_admin_username: str = "admin"
super_admin_password: str = "admin123456"
```

**描述:** 源码中硬编码了默认超级管理员凭据。即使 `.env` 中设置了 `SUPER_ADMIN_PASSWORD`，`init_db()` 仅在用户不存在时创建，如果数据库已经用默认密码初始化，修改 `.env` 不会更新已有用户的密码。实测 `admin:admin123456` 成功登录并获得 `super_admin` 角色。

**PoC:**
```bash
curl -X POST http://localhost:8080/api/v1/auth/sessions \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123456"}'

# 返回: access_token + user.roles[0].code = "super_admin"
```

**影响:**
- 攻击者可获得超级管理员权限
- 完全控制用户管理（增删改查、角色分配）
- 查看所有审计日志、项目数据、文件元数据
- 可删除/修改任意数据

**修复建议:**
1. 移除 config.py 中的默认明文密码，改为启动时从环境变量强制读取
2. 如果 `SUPER_ADMIN_PASSWORD` 未设置或为空，拒绝启动
3. 首次部署后强制要求修改密码
4. `init_db()` 中检测到用户已存在但密码为默认值时，打印严重警告并拒绝启动

---

### 🟠 HIGH-01: Debug 模式泄露完整 Python 调用栈

**文件:** `backend/app/main.py:32` + `backend/app/core/config.py:16`  
**确认:** `.env` 中 `DEBUG=true`

```
app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
```

**描述:** 当请求的 `Content-Type` 不是 `application/json`（如 `text/plain` 或 `application/x-www-form-urlencoded`）时，FastAPI 的 Pydantic 验证失败会绕过自定义 `validation_exception_handler`，直接由 Starlette 的 `ServerErrorMiddleware` 输出完整 Python traceback。

**PoC:**
```bash
curl -X POST http://localhost:8080/api/v1/auth/sessions \
  -H "Content-Type: text/plain" \
  -d '{"username":"test"}'

# 返回完整 traceback，包含:
# - 文件路径: backend/app/api/v1/auth_api.py（仓库相对路径）
# - Python 版本: 3.12.13
# - 框架: FastAPI + Starlette + Pydantic
# - 中间件链: add_request_id → CORS → errors → asyncexitstack
# - 异常类型: RequestValidationError → TypeError: bytes not JSON serializable
```

**泄露的信息:**
- 服务器文件系统绝对路径
- Python 版本 (3.12.13)
- 所有中间件堆栈顺序
- 内部异常处理逻辑中的 bug（bytes 类型未处理）

**修复建议:**
1. **立即:** 将 `.env` 中 `DEBUG=false`
2. 修复 `validation_exception_handler` 中对非 JSON body 的处理（bytes → str 转换）
3. 生产环境配置检查清单中加入 `debug` 字段

---

### 🟠 HIGH-02: 生产环境 debug=true

**文件:** `backend/.env:3`

```
DEBUG=true
```

**描述:** 这是 `debug=True` 的根本原因。FastAPI `debug=True` 不仅输出 traceback，还会在异常时显示更多内部信息。该配置项在本地开发和当前部署中均为 `true`。

**修复建议:** 将 `.env` 中 `DEBUG=false`，或通过环境变量覆盖。

---

### 🟡 MEDIUM-01: JWT 签名密钥长度不足

**文件:** `backend/.env` (JWT_SECRET_KEY)  

**描述:** 当前 JWT 使用 HS256 算法，但密钥仅为 16 字节。RFC 7518 §3.2 要求 HMAC-SHA256 密钥至少 32 字节。PyJWT 库已发出 `InsecureKeyLengthWarning` 警告。

```
# 验证代码:
jwt.decode(token, secret, algorithms=['HS256'])
# InsecureKeyLengthWarning: The HMAC key is 16 bytes long, 
# which is below the minimum recommended length of 32 bytes for SHA256
```

**影响:** 较短的密钥使暴力破解 JWT 签名变得相对更容易（虽然实际利用仍需大量计算资源）。

**修复建议:** 生成至少 32 字节的随机密钥:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

### 🟡 MEDIUM-02: JWT Token 存储在 localStorage

**文件:** `frontend/src/stores/auth.store.ts`

```javascript
localStorage.setItem('dwg_access_token', token)
localStorage.setItem('dwg_user', JSON.stringify(user))
```

**描述:** JWT access token 和用户信息存储在 `localStorage` 中。任何 XSS 漏洞都可直接读取 token 并冒充用户。

**修复建议:**
1. 使用 HttpOnly + Secure + SameSite=Strict Cookie 传递 token
2. 实现 refresh token 轮换机制
3. 短期 access token (当前 30min 可接受) + 长期 refresh token
4. 如必须用 localStorage，至少实现 Content-Security-Policy

---

### 🟡 MEDIUM-03: 缺少 Content-Security-Policy 响应头

**描述:** 响应头中没有 CSP。配合前端 React SPA 架构，一旦存在 XSS，攻击者可以加载任意外部脚本，轻易窃取 localStorage 中的 JWT token。

**当前安全头:**
| Header | Value |
|--------|-------|
| X-Frame-Options | SAMEORIGIN ✅ |
| X-Content-Type-Options | nosniff ✅ |
| X-XSS-Protection | 1; mode=block ✅ (legacy) |
| Referrer-Policy | strict-origin-when-cross-origin ✅ |
| Content-Security-Policy | **缺失** ❌ |

**修复建议:**
```nginx
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' http://localhost:*;" always;
```

---

### 🟡 MEDIUM-04: CORS allow_credentials=true 配合可配置 Origin

**文件:** `backend/app/main.py:34-40`

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,  # http://localhost:5173,http://127.0.0.1:5173
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**风险:** 如果 `BACKEND_CORS_ORIGINS` 配置不当（加入 `*` 或被注入），配合 `allow_credentials=True` 将允许任意源携带凭据跨域请求。

**修复建议:**
1. 严格限定 origin 白名单
2. 确保 nginx 层面也做 CORS 校验
3. 生产环境移除 `localhost` origin

---

### 🔵 LOW-01: SPA 全路由 200 (无真实 404)

**描述:** React SPA 客户端路由导致所有非 API 路径返回 `index.html` + 200。这可能绕过某些基于 HTTP 状态码的安全检测。

**修复建议:** 在 nginx 中为非 SPA 路由的敏感路径（如 `/.env`、`/.git`、`/config` 等）显式返回 404。

---

### 🔵 LOW-02: Redis 不可用

**描述:** `/health` 显示 `redis: unavailable`。这意味着依赖 Redis 的功能（session 存储、分布式限流等）不可用，仅靠 nginx 层面的 IP 限流。

**修复建议:** 启动 Redis 服务或排查连接问题。

---

### 🔵 LOW-03: 未实现端点暴露信息

**描述:** 
- `POST /api/v1/auth/tokens/refresh` → 501 (Refresh token is not implemented in local skeleton.)
- `PATCH /api/v1/auth/password` → 501 (Password change will be implemented after account policy is finalized.)

这些端点暴露了开发计划和内部实现进度信息。

**修复建议:** 在 API 路由注册层面移除未实现的端点，或返回通用的 404。

---

## 4. 通过的安全测试项 ✅

| 测试项 | 方法 | 结果 |
|--------|------|------|
| SQL 注入 | `' OR 1=1--`, `UNION SELECT`, `pg_sleep()`, `SLEEP()` | ✅ 参数化查询，所有注入被阻止 |
| NoSQL 注入 | `$ne`, `$gt`, `$regex`, `$exists`, `$where` | ✅ Pydantic strict string 验证拒绝 object 类型 |
| 用户名枚举 | 多个用户名 + 固定错误密码，对比响应差异 | ✅ 所有用户名返回相同错误 `INVALID_CREDENTIALS` |
| JWT none 算法 | `{"alg":"none"}` header | ✅ 签名验证拒绝无签名 token |
| JWT 篡改 | 修改 sub 字段 | ✅ 签名不匹配，拒绝 |
| Mass Assignment | 创建用户时传 `roles:[{id:1}]`, `status:"active"` | ✅ Pydantic schema 过滤，仅接受定义字段 |
| RBAC 越权 | 无角色用户访问 `/api/v1/users` | ✅ 返回 403 FORBIDDEN |
| 未认证访问 | 无 token 访问受保护端点 | ✅ 返回 401 Not authenticated |
| TRACE 方法 | `TRACE /`, `TRACE /login` | ✅ 返回 405 |
| 自注册 | `POST /api/v1/auth/register`, `/api/v1/register` | ✅ 不存在 (404)，用户创建需 admin 角色 |
| 密码哈希 | 源码审计 `app/core/security.py` | ✅ argon2id via pwdlib PasswordHash.recommended() |
| Nginx 限流 | 连续请求 >6 次 | ✅ 返回 429 Too Many Requests |

---

## 5. 修复优先级排序

```
[P0 - 立即] CRITICAL-01 默认管理员凭据 → 改密码 + 移除源码默认值
[P0 - 立即] HIGH-02     production debug=true → 改 DEBUG=false
[P1 - 24h]  HIGH-01     调用栈泄露 → 修复异常处理器 + 关闭 debug 后自动缓解
[P2 - 1周]  MEDIUM-01   JWT 密钥长度 → 生成 32+ 字节新密钥
[P2 - 1周]  MEDIUM-03   缺少 CSP 头 → nginx 添加 CSP
[P3 - 2周]  MEDIUM-02   Token localStorage → 迁移到 HttpOnly Cookie
[P3 - 2周]  MEDIUM-04   CORS credentials → 审查 origin 配置
[P4 - 1月]  LOW-01~03   次要改进项
```

---

## 6. 测试方法附录

```
信息收集       → WebFetch + curl headers + JS bundle 逆向
目录枚举       → 常见敏感路径扫描
注入测试       → SQL/NoSQL 完整 payload 集
认证绕过       → JWT none/篡改 + Header 伪造 + 方法覆盖
源码审计       → FastAPI 源码 (已知路径后)
访问控制       → 创建低权限用户 → 越权访问
清理           → 删除测试用户 (id=2,3)
```

**测试过程创建的临时数据均已清理。**
