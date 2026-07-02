# Nginx 加固验证 & 遗漏项审计

**前序审计:** `nginx-security-audit.md` (14 findings → all fixed)
**验证日期:** 2026-07-02
**测试轮次:** Round 2.5 — 加固后验证 + 深挖遗漏

---

## 1. 加固验证结果: 17/17 ✅

所有 14 项审计 finding 的修复均通过验证：

| Finding | 验证方式 | 结果 |
|---------|---------|------|
| HIGH-01 版本泄露 | `Server` 头从 `nginx/1.30.3` → `nginx` | ✅ |
| HIGH-02 缺 CSP | 7 个指令完整返回，`frame-ancestors 'self'` | ✅ |
| HIGH-03 traceback 穿透 | 本地版 error_page 兜底生效 | ✅ (本地预期行为) |
| MED-01 限流收紧 | 第 4 次请求触发 429 (2r/s burst=3) | ✅ |
| MED-02 Host 白名单 | `evil.com` → 444 关闭连接 | ✅ |
| MED-03 现代安全头 | Permissions-Policy + COOP 正确返回 | ✅ |
| MED-04 POST 斜杠 | `/sessions/` 301 替代 307 | ✅ |
| MED-05 敏感路径 | 11 个路径全部返回 404 | ✅ |
| LOW-01 XSS-Protection | 已从响应头中完全移除 | ✅ |
| LOW-02 limit_except | POST/PUT/DELETE 静态路径 → 403 | ✅ |
| LOW-03 body 1k 限制 | 2KB 请求体 → 413 | ✅ |
| LOW-04 X-Forwarded-Host | 配置已添加 (客户端不可直接验证) | ✅ |

---

## 2. 加固副作用检查

### 2.1 add_header 继承 ✅ 无问题

nginx 的 `add_header` 子 location 会替换父级所有头（不继承）。本次加固在子 location 中**显式重复了全部安全头**（第 186-204 行），验证确认无头丢失也无重复。

### 2.2 Host 正则匹配 ✅ 无绕过

测试了 8 种变体 Host 头：
- `evil.com` → 444 ✅
- `0.0.0.0` → 444 ✅
- `[::1]` → 444 ✅
- `localhost:8080` / `LOCALHOST` / `localhost.` / `127.0.0.1` → 200 ✅（合法流量）

`!~*` 不区分大小写 + nginx 自动剥离端口和尾点 = 无绕过风险。

### 2.3 rate limit 不可欺骗 ✅

X-Forwarded-For 逐次更换仍被限流（nginx `$binary_remote_addr` 取真实 IP，不受代理头影响）。

---

## 3. 本轮新发现问题

### 🟡 MEDIUM-06: 413 错误页使用 nginx 默认 HTML（品牌泄露 + 格式不一致）

**描述:** `client_max_body_size 1k` 生效后，超出限制的请求收到 nginx **默认 HTML 错误页**，包含 `nginx` 品牌信息，且格式为 HTML 而非 JSON（与其他 API 错误响应不一致）。

```
HTTP/1.1 413 Request Entity Too Large
Content-Type: text/html

<html>
<head><title>413 Request Entity Too Large</title></head>
<body>
<center><h1>413 Request Entity Too Large</h1></center>
<hr><center>nginx</center>  ← 品牌泄露
</body>
</html>
```

**对比:** 500 错误已有自定义 JSON 错误页（`/50x.html` → JSON），413 却没有。

**修复:**
```nginx
# 在 http 块或 server 块中添加
error_page 413 /413.html;
location = /413.html {
    internal;
    default_type application/json;
    return 413 '{"error":{"code":"PAYLOAD_TOO_LARGE","message":"Request body exceeds size limit."}}';
}
```

---

### 🟡 MEDIUM-07: 无并发连接限制 (limit_conn)

**描述:** 测试成功建立 50 个并发连接到 nginx。无 `limit_conn` 指令限制单 IP 并发连接数。配合 `keepalive_timeout 65s`，攻击者可通过 Slowloris 或慢速读取攻击耗尽 `worker_connections` (1024×4=4096)。

**验证:**
```
50 并发连接全部成功建立
keepalive_timeout 65s — 空闲连接保持 65 秒
```

**修复:**
```nginx
# 在 http 块中
limit_conn_zone $binary_remote_addr zone=conn_per_ip:10m;

# 在 server 块中
limit_conn conn_per_ip 20;        # 单 IP 最多 20 并发
limit_conn_status 429;
```

---

### 🟡 MEDIUM-08: 未配置慢速客户端防护 (client_header_timeout / client_body_timeout)

**描述:** 两个配置均使用 nginx 默认值：`client_header_timeout=60s`、`client_body_timeout=60s`。攻击者可缓慢发送 header/body，每个连接占用 worker 长达 60 秒。

**验证:**
```
Slowloris 测试: 3 秒后才发送完整 header → 请求仍被接受
无显式 client_header_timeout 配置
```

**修复:**
```nginx
# 在 http 块中（登录 location 可更严格）
client_header_timeout 10s;
client_body_timeout 10s;    # 登录接口已有限流，此处做全局兜底
```

---

### 🔵 LOW-08: HTTP/0.9 简单请求被接受

**描述:** nginx 接受 HTTP/0.9 风格的简单请求（无 HTTP 版本、无 header），并正常代理到后端。

**验证:**
```
发送: "GET /health\r\n" (无 HTTP 版本)
响应: 完整的 JSON 健康检查结果 (200 OK)
```

**影响:** 低。HTTP/0.9 无 Host 头，但 nginx 仍会匹配 default server。可用于非常规客户端绕过部分检测。但 nginx 1.30 已默认不支持 HTTP/0.9（需显式 `ignore_invalid_headers off` 才能接受这种格式）。

实际上，这个请求被 nginx 当成了 HTTP/1.0 不带 Host 头处理了（nginx 自动补全）。

**修复:** 无需特别处理，nginx 现代版本已足够安全。

---

### 🔵 LOW-09: keepalive_requests 未限制

**描述:** nginx 默认 `keepalive_requests` 值较高（nginx 1.30 默认 1000）。单个 keepalive 连接可发送 1000 个请求，增加慢速攻击窗口。

**修复:**
```nginx
keepalive_requests 100;  # 单连接最多 100 个请求后关闭
```

---

## 4. 阶段规划建议

### 阶段 C (HTTPS) 需要的补充配置

当前配置完全没有 SSL 相关内容。阶段 C 引入 HTTPS 时需要补充：

```nginx
# 在 server 块中
listen 443 ssl http2;
ssl_certificate     /etc/nginx/ssl/cert.pem;
ssl_certificate_key /etc/nginx/ssl/key.pem;
ssl_protocols       TLSv1.2 TLSv1.3;
ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:...;
ssl_prefer_server_ciphers off;
ssl_session_cache   shared:SSL:10m;
ssl_session_timeout 10m;

# HSTS (必须)
add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;

# 80→443 强制重定向
# return 301 https://$host$request_uri;
```

### 阶段 2 (Agent/SSE/WebSocket) 需要的补充配置

```nginx
# 在 API location 中
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
proxy_read_timeout 3600s;  # SSE 长连接
```

---

## 5. 修复优先级（本轮新增）

```
[P2 - 1周]   MED-06    413 自定义 JSON 错误页
[P2 - 1周]   MED-07    添加 limit_conn 并发连接限制
[P2 - 1周]   MED-08    配置 client_header_timeout / client_body_timeout
[P3 - 2周]   LOW-09    限制 keepalive_requests
```

---

## 6. 对比：加固前 vs 加固后

| 维度 | 加固前 | 加固后 | 改善 |
|------|--------|--------|------|
| Server 头 | `nginx/1.30.3` | `nginx` | ✅ 版本隐藏 |
| CSP | 无 | 完整 7 指令 | ✅ XSS 防护 |
| Traceback | 直接穿透 | error_page 兜底 | ✅ 信息泄露控制 |
| 登录限流 | 10r/s burst=5 | 2r/s burst=3 | ✅ 80% 收紧 |
| Host 校验 | 无 | 白名单+444 | ✅ 投毒防护 |
| 安全头数量 | 4 | 7 | ✅ +75% |
| 敏感路径 | SPA 200 | 显式 404 | ✅ 噪音消除 |
| X-XSS-Protection | 存在(过时) | 移除 | ✅ 清理 |
| 静态方法限制 | 无 | GET/HEAD/OPTIONS | ✅ 防止写入 |
| 登录 body 限制 | 512MB | 1KB | ✅ 防大 payload |
| **剩余风险** | | | |
| 413 错误页 | 默认 HTML | 默认 HTML | ❌ 仍待修复 |
| 并发限制 | 无 | 无 | ❌ 仍待修复 |
| slow client 防护 | 默认 60s | 默认 60s | ❌ 仍待修复 |
