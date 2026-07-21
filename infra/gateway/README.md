# 网关基础设施

## 现有内容

`nginx/` 保存生产 Compose 和本地运行配置、静态 SPA fallback、`/api` 反向代理、SSE buffering/cache 关闭、上传/超时限制及日志目录。当前已验证 Compose/本地入口仍为 HTTP；SSL 目录只作为部署挂载边界。

## 输入、输出与边界

输入是 frontend dist、FastAPI `:8010`、域名/可选证书，输出是统一浏览器入口。Nginx 不验证项目/RBAC，也不能因存在 SSL 配置位就宣称 HTTPS 已交付。
