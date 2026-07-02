# Nginx — DWG-Agent 网关配置

## 架构职责

Nginx 是平台**唯一对外入口**（规范 §3, §4）：

```
浏览器 → Nginx ─┬─ /api/v1/* ──→ FastAPI backend-api:8000
                 ├─ /health   ──→ FastAPI backend-api:8000
                 └─ /*         ──→ React SPA (/usr/share/nginx/html)
```

**核心功能:**

| 功能 | 实现 | 规范 |
|------|------|------|
| React SPA 静态托管 | `try_files $uri /index.html` | BrowserRouter fallback |
| API 反向代理 | `proxy_pass http://backend` | `/api/v1/*` → backend-api:8000 |
| 登录防爆破 | 10 req/s, burst 5 | §3 安全 |
| 通用 API 限流 | 100 req/s, burst 20 | §3 安全 |
| 上传大小限制 | `client_max_body_size 512m` | 匹配 backend max_upload_size_mb |
| X-Request-ID 追踪 | `$request_id` → proxy header | 全链路追踪 |
| SSE 兼容 | `proxy_buffering off` | 阶段二 Agent 事件流 |
| 安全响应头 | X-Frame-Options, nosniff, etc. | §3 安全 |
| 访问日志 | extended 格式 | 含 upstream 耗时 |

## 文件结构

```
infra/nginx/
├── nginx.conf                    # Docker / 生产版主配置（容器内路径）
├── nginx.local.conf              # 本地开发版主配置（宿主机绝对路径）
├── conf.d/
│   └── dwg-agent.conf            # server 块：HTTP + HTTPS（阶段 C 启用）
├── snippets/
│   ├── proxy-params.conf         # 反向代理公共参数（/api 引用）
│   ├── security-headers.conf     # 安全响应头（server 块引用）
│   └── rate-limit.conf           # 限流 zone 定义（nginx.conf 内联，此为文档）
├── ssl/                          # SSL 证书（阶段 C 使用）
└── logs/                         # 运行时日志（.gitignore 排除）
```

## 启动方式

### 阶段 A — 本地开发（无 Docker）

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework

# 前置条件
cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 &
cd frontend && npm run build

# Nginx 操作
sudo nginx -t -c $(pwd)/infra/nginx/nginx.local.conf        # 语法检查
sudo nginx -c $(pwd)/infra/nginx/nginx.local.conf            # 启动
sudo nginx -c $(pwd)/infra/nginx/nginx.local.conf -s reload  # 热重载
sudo nginx -c $(pwd)/infra/nginx/nginx.local.conf -s quit    # 停止
```

启动后访问: **`http://localhost:8080`**

### 阶段 B — Docker Compose

```bash
# 根目录执行
docker compose up -d                          # 核心服务（nginx + backend + mysql + redis + minio）
docker compose --profile workers up -d        # 含 Celery Workers
docker compose --profile workers --profile monitoring up -d  # 完整平台 + Flower 监控

# Nginx 单独重载（不中断服务）
docker compose exec nginx nginx -s reload
```

启动后访问: **`http://localhost`**（端口 80）

### 阶段 C — HTTPS 生产

1. 获取 SSL 证书，放入 `infra/nginx/ssl/`
2. 编辑 `conf.d/dwg-agent.conf`，取消 HTTPS server 块注释
3. 编辑 `compose.yaml`，取消端口 443 映射注释
4. 添加 ssl 目录挂载: `- ./infra/nginx/ssl:/etc/nginx/ssl:ro`
5. `docker compose restart nginx`

## 关键设计决策

| 决策 | 原因 | 规范 |
|------|------|------|
| Docker 版监听 80 | 容器内标准 HTTP 端口 | §17.4 |
| 本地版监听 8080 | 非特权端口，不干扰系统 nginx | - |
| `try_files $uri /index.html` | React BrowserRouter 支持 | §4 |
| `client_max_body_size 512m` | 匹配 backend max_upload_size_mb | §7.3 |
| `proxy_buffering off` | SSE 事件流实时推送 | 阶段二 |
| `proxy_read_timeout 120s` | 匹配 Gunicorn `--timeout 120` | §17.4 |
| `X-Request-ID $request_id` | Nginx 生成 → FastAPI 透传 | 全链路追踪 |
| `expires 7d` 静态资源 | JS/CSS 文件名 content-hash，可长缓存 | - |
| `expires -1` index.html | 确保更新后即时生效 | - |
| `upstream keepalive 32` | 连接池复用，降低延迟 | §17.4 |
| `limit_req_status 429` | 限流时返回标准 HTTP 状态码 | REST API |

## Docker Compose 挂载映射

| 宿主机路径 | 容器内路径 | 用途 |
|-----------|-----------|------|
| `infra/nginx/nginx.conf` | `/etc/nginx/nginx.conf` | 主配置 |
| `infra/nginx/conf.d/` | `/etc/nginx/conf.d/` | server 块 |
| `infra/nginx/snippets/` | `/etc/nginx/snippets/` | 公共参数 |
| `infra/nginx/ssl/` | `/etc/nginx/ssl/` | SSL 证书（阶段 C） |
| `frontend/dist/` | `/usr/share/nginx/html/` | React SPA |
