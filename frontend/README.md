# Frontend

React + TypeScript + Vite 前端骨架。

## 快速启动

```bash
# 首次安装（或 node_modules 不存在时）
npm install

# 日常增量安装（node_modules 已存在，比 npm ci 快 10 倍以上）
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
