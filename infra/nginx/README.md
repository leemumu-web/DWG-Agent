# Nginx — DWG-Agent 网关配置

Nginx 是平台**唯一对外入口**（规范 §3）。

```
浏览器 → Nginx ─┬─ /api/v1/* ──→ FastAPI backend-api:8000
                 ├─ /health   ──→ FastAPI backend-api:8000
                 └─ /*         ──→ React SPA (/usr/share/nginx/html)
```

## 核心功能

| 功能 | 实现 | 规范 |
|------|------|------|
| SPA 静态托管 | `try_files $uri /index.html` | BrowserRouter |
| API 反向代理 | `proxy_pass http://backend` | `/api/v1/*` |
| 登录防爆破 | 10 req/s, burst 5 | §3 |
| API 限流 | 100 req/s, burst 20 | §3 |
| 上传限制 | `client_max_body_size 512m` | 匹配后端 |
| 请求追踪 | `X-Request-ID: $request_id` | 全链路 |
| SSE 兼容 | `proxy_buffering off` | 阶段二 |

## 文件结构

```
infra/nginx/
├── nginx.conf          # Docker 版 — 自包含，容器内路径（§17.4 挂载）
├── nginx.local.conf    # 本地开发版 — 宿主机路径，监听 8080
├── ssl/                # SSL 证书占位（阶段 C）
└── logs/               # 运行时日志（.gitignore）
```

`nginx.conf` 是完全自包含的单文件（nginx:1.27-alpine 只挂载此文件和 `frontend/dist`）。

## 启动

### 阶段 A — 本地开发

```bash
# 从仓库根目录执行
# 前置: 后端已启动（127.0.0.1:8000），前端已构建（frontend/dist/）

sudo nginx -t -c $(pwd)/infra/nginx/nginx.local.conf        # 语法检查
sudo nginx -c $(pwd)/infra/nginx/nginx.local.conf            # 启动
sudo nginx -c $(pwd)/infra/nginx/nginx.local.conf -s reload  # 热重载
sudo nginx -c $(pwd)/infra/nginx/nginx.local.conf -s quit    # 停止
```

访问: `http://localhost:8080`

### 阶段 B — Docker Compose

```bash
# 根目录
docker compose up -d                              # 核心服务
docker compose --profile workers up -d            # + Celery Workers
docker compose --profile monitoring up -d         # + Flower

docker compose exec nginx nginx -s reload         # 单独重载 nginx
```

访问: `http://localhost`

### 阶段 C — HTTPS

1. 证书放入 `ssl/`，更新 compose.yaml 挂载 `./infra/nginx/ssl:/etc/nginx/ssl:ro`
2. 在 `nginx.conf` 的 server 块增加 `listen 443 ssl;` + `ssl_certificate` 等指令
3. 添加 `add_header Strict-Transport-Security ...`（HSTS）
4. `docker compose restart nginx`

## Docker 挂载（§17.4）

| 宿主机 | 容器内 |
|--------|--------|
| `infra/nginx/nginx.conf` | `/etc/nginx/nginx.conf:ro` |
| `frontend/dist/` | `/usr/share/nginx/html:ro` |

## 设计决策

| 决策 | 原因 |
|------|------|
| 单文件自包含 | §17.4 只挂载 nginx.conf，无 conf.d/snippets 依赖 |
| Docker 80 / 本地 8080 | 容器标准端口 / 宿主机非特权端口 |
| `try_files $uri /index.html` | React BrowserRouter |
| `proxy_buffering off` | SSE 事件流（阶段二） |
| `proxy_read_timeout 120s` | 匹配 Gunicorn `--timeout 120` |
| `upstream keepalive 32` | 连接池复用 |
| `expires 7d` 静态资源 | JS/CSS content-hash 可长缓存 |
| `expires -1` index.html | 更新即时生效 |
