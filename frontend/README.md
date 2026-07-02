# Frontend

React + TypeScript + Vite 前端骨架。

## 快速启动

```bash
# 首次安装 — 严格按 package-lock.json（推荐）
npm ci

# 日常增量安装 — 比 npm ci 快
npm install --prefer-offline

# 启动开发服务器
npm run dev
```

## .npmrc 加速

项目 `.npmrc` 已配置：

| 配置 | 作用 |
|------|------|
| `maxsockets=20` | 20 路并发下载 |
| `fetch-timeout=30000` | 30s 快速超时 |
| `audit=false` | 跳过安全审计 |
| `registry=npmmirror.com` | 国内镜像源 |

全新安装 120 packages / 207MB 约 14s。
