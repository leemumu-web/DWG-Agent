# Nginx Gateway

Nginx is the only public application entry.

```text
Browser -> Nginx -> /api/v1/*, /health*, /docs, /redoc, /openapi.json -> FastAPI
                 -> all other routes -> React SPA
```

Docker proxies to `backend-api:8000` and listens inside the unprivileged image on `8080`. Compose
publishes that as host port `80`. The local configuration listens on `8080` and proxies to
`127.0.0.1:8010`.

## Controls

| Control | Configuration |
|---|---|
| login rate limit | `2r/s`, burst 3, HTTP 429 |
| general API limit | `100r/s`, burst 20, HTTP 429 |
| per-IP connections | 20 |
| upload limit | 512 MiB |
| request tracing | generated `X-Request-ID` forwarded to FastAPI |
| SSE | buffering/cache disabled; one-hour read/send timeout |
| security headers | CSP, frame, content-type, referrer, permissions, opener policies |
| SPA | hashed assets cached; `index.html` no-cache; BrowserRouter fallback |

FastAPI JSON errors pass through unchanged. Nginx formats only its own gateway errors.

## Local Commands

```bash
sudo nginx -t -c "$(pwd)/infra/nginx/nginx.local.conf"
sudo nginx -c "$(pwd)/infra/nginx/nginx.local.conf"
sudo nginx -c "$(pwd)/infra/nginx/nginx.local.conf" -s reload
sudo nginx -c "$(pwd)/infra/nginx/nginx.local.conf" -s quit
```

`nginx.local.conf` contains host-specific absolute paths because Nginx requires them for logs, PID,
and SPA root. Its header documents the replacement command. `nginx.conf` contains only container
paths and is mounted at `/etc/nginx/nginx.conf:ro`.
