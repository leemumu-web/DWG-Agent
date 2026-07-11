# Nginx Gateway / Nginx 网关

## English

Nginx is the browser entry for the built SPA and API proxy:

```text
Browser -> Nginx -> /api/v1/*, /health*, /docs, /redoc, /openapi.json -> FastAPI
                 -> other routes -> React SPA
```

`nginx.local.conf` listens on `8080` and proxies to `127.0.0.1:8010`. `nginx.conf` listens inside the unprivileged image on `8080` and proxies to `backend-api:8000`; Compose publishes host `80`.

Current `nginx.conf` does **not** listen on `8443`, configure `ssl_certificate`, redirect HTTP, or add HSTS. The Compose `443:8443` mapping is therefore inactive and must not be described as HTTPS.

### Controls

| Control | Configuration |
|---|---|
| login rate | `2r/s`, burst 3, HTTP 429 |
| general API rate | `100r/s`, burst 20, HTTP 429 |
| per-IP connections | 20 |
| request body | 512 MiB; login override 1 KiB |
| request tracing | generate/forward `X-Request-ID` |
| SSE | buffering/cache off; one-hour read/send timeout |
| browser headers | CSP, frame, content-type, referrer, permissions, opener policies |
| SPA cache | hashed assets cached; `index.html` no-cache; BrowserRouter fallback |

FastAPI JSON errors pass through. Nginx formats only its own gateway errors. In production FastAPI disables `/docs`, `/redoc`, and `/openapi.json`; proxy locations do not override that application policy.

### Local Commands

```bash
sudo nginx -t -c "$(pwd)/infra/nginx/nginx.local.conf"
sudo nginx -c "$(pwd)/infra/nginx/nginx.local.conf"
sudo nginx -c "$(pwd)/infra/nginx/nginx.local.conf" -s reload
sudo nginx -c "$(pwd)/infra/nginx/nginx.local.conf" -s quit
```

`nginx.local.conf` contains host-specific absolute paths for root/log/PID. Review its header after moving the repository. Nginx is not an authorization layer; every proxied resource remains protected by FastAPI.

## 中文

Nginx 是 built SPA 和 API proxy 的浏览器入口：

```text
Browser -> Nginx -> /api/v1/*、/health*、/docs、/redoc、/openapi.json -> FastAPI
                 -> 其他 route -> React SPA
```

`nginx.local.conf` 监听 `8080` 并代理到 `127.0.0.1:8010`。`nginx.conf` 在 unprivileged image 内监听 `8080` 并代理到 `backend-api:8000`；Compose 发布宿主 `80`。

当前 `nginx.conf` **不**监听 `8443`、配置 `ssl_certificate`、重定向 HTTP 或添加 HSTS。因此 Compose `443:8443` 映射无效，禁止描述为 HTTPS。

### 控制

| 控制 | 配置 |
|---|---|
| login rate | `2r/s`、burst 3、HTTP 429 |
| general API rate | `100r/s`、burst 20、HTTP 429 |
| 每 IP connection | 20 |
| request body | 512 MiB；login override 1 KiB |
| request tracing | 生成/转发 `X-Request-ID` |
| SSE | buffering/cache 关闭；一小时 read/send timeout |
| browser header | CSP、frame、content-type、referrer、permissions、opener policy |
| SPA cache | hashed asset cache；`index.html` no-cache；BrowserRouter fallback |

FastAPI JSON error 原样通过。Nginx 只格式化自己的 gateway error。生产 FastAPI 关闭 `/docs`、`/redoc`、`/openapi.json`；proxy location 不能覆盖该应用策略。

### 本地命令

```bash
sudo nginx -t -c "$(pwd)/infra/nginx/nginx.local.conf"
sudo nginx -c "$(pwd)/infra/nginx/nginx.local.conf"
sudo nginx -c "$(pwd)/infra/nginx/nginx.local.conf" -s reload
sudo nginx -c "$(pwd)/infra/nginx/nginx.local.conf" -s quit
```

`nginx.local.conf` 含 root/log/PID 的 host-specific absolute path。移动仓库后检查其 header。Nginx 不是授权层；每个被代理资源仍由 FastAPI 保护。
