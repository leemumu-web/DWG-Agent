# Infra — 基础设施配置

本目录存放 Docker Compose、Nginx、MySQL、Redis、MinIO 的生产部署配置。

## 当前状态

| 组件 | 状态 | 启动方式 |
|------|------|---------|
| **Nginx** | ✅ 可用 | `docker compose up -d nginx` 或本地 `nginx.local.conf` |
| **MySQL** | ✅ 可用 | `docker compose up -d mysql` |
| **Redis** | ✅ 可用 | `docker compose up -d redis` |
| **MinIO** | ✅ 可用 | `docker compose up -d minio` |
| **Backend API** | ✅ 可用 | `docker compose up -d backend-api` |
| **Celery Workers** | 配置文件就绪 | `docker compose --profile workers up -d`（阶段二实现） |
| **Flower 监控** | 配置文件就绪 | `docker compose --profile monitoring up -d` |

## 快速开始

### 本地开发（无 Docker）— 阶段 A

```bash
# 1. 后端
cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 &

# 2. 前端构建
cd frontend && npm run build

# 3. Nginx（可选，统一入口到 8080）
sudo nginx -c $(pwd)/infra/nginx/nginx.local.conf
# 访问 http://localhost:8080
```

### Docker Compose — 阶段 B

```bash
# 前置 1: 配置 .env 中的密码变量（MYSQL_PASSWORD, REDIS_PASSWORD 等）
# 前置 2: 前端已构建（cd frontend && npm run build）

# 启动核心服务
docker compose up -d

# 查看日志
docker compose logs -f nginx backend-api

# 停止
docker compose down
```

访问: `http://localhost`

## 目录

```
infra/
├── nginx/             # Nginx 网关（详见 nginx/README.md）
│   ├── nginx.conf         # Docker 版 — 单文件自包含
│   ├── nginx.local.conf   # 本地开发版
│   ├── ssl/               # SSL 证书占位（阶段 C）
│   └── logs/              # 运行时日志（.gitignore）
├── mysql/
│   └── init.sql           # MySQL 初始化（compose 自动挂载到 /docker-entrypoint-initdb.d/）
├── redis/                 # Redis 配置占位
└── minio/                 # MinIO 配置占位
```
