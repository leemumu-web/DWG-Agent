# Nginx 网关

Nginx 是构建后 SPA 与 API 的浏览器入口：

```text
Browser -> Nginx -> /api/v1/*、/health*、/docs、/redoc、/openapi.json -> FastAPI
                 -> 其他 route -> React SPA
```

`nginx.local.conf` 监听本机 `8080` 并代理到 `127.0.0.1:8010`；容器 `nginx.conf` 监听非特权端口 `8080` 并代理到 `backend-api:8010`，Compose 默认发布宿主 `80`。当前 Compose 不发布 443，Nginx 也没有证书、TLS listener、HTTPS 跳转或 HSTS。

| 控制 | 当前配置 |
|---|---|
| 登录限速 | `2r/s`，burst 3，网关 429 |
| 通用 API 限速 | `100r/s`，burst 20，网关 429 |
| 每 IP connection | 20 |
| request body | 520 MiB（为 512 MiB 业务文件上限预留 multipart 封装空间）；登录 1 KiB |
| 本地上传缓冲 | `logs/client-body/`；由仓库用户创建，避免依赖 `/var/lib/nginx` 权限 |
| 请求追踪 | 生成/转发 `X-Request-ID` |
| SSE | buffering/cache 关闭；read/send timeout 一小时 |
| 浏览器 header | CSP、frame、content-type、referrer、permissions、opener policy |
| SPA cache | hashed asset cache；`index.html` no-cache；BrowserRouter fallback |

FastAPI JSON 错误直接通过，Nginx 只格式化自身错误。生产 FastAPI 会关闭运行时 API 文档，代理 location 不会改变该策略。Nginx 也不是授权层，每个资源仍由 FastAPI 校验。

```bash
mkdir -p infra/gateway/nginx/logs/client-body
nginx -t -c "$(pwd)/infra/gateway/nginx/nginx.local.conf"
nginx -c "$(pwd)/infra/gateway/nginx/nginx.local.conf"
nginx -c "$(pwd)/infra/gateway/nginx/nginx.local.conf" -s reload
nginx -c "$(pwd)/infra/gateway/nginx/nginx.local.conf" -s quit
```

本地配置包含当前仓库绝对路径，移动仓库后必须检查其 header、root、log、上传临时目录和 PID 路径。
Nginx 语法和代理冒烟通过不能替代 TLS 证书、外部域名、上传大文件和长时 SSE 的生产验收；
未经这些证据不得把当前 HTTP Compose 入口写成 HTTPS 已发布。
