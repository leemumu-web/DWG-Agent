# 配置

> 英文对应文档：[../configuration.md](../configuration.md)

## 加载与优先级

`backend/app/core/config.py` 使用 Pydantic Settings，并从进程工作目录解析 `.env`。仓库脚本从 `backend/` 启动 Python，因此 API 和 worker 通常读取 `backend/.env`；根目录数据库和 shell 工具读取根 `.env`。两份文件的 `MYSQL_*` 必须一致。Compose 通过 `env_file` 读取 `.env.docker`。

环境变量覆盖 `.env`。设置 `DATABASE_URL` 时，它覆盖 SQLAlchemy 的组件字段；若为 MySQL URL，也成为 Celery 的派生来源，否则 Celery 仍从 `MYSQL_*` 生成 MySQL URL。除测试外，不支持非 MySQL 运行时覆盖。

## 应用与网络

| 变量 | 默认值 | 含义 |
|---|---|---|
| `APP_NAME` | `DWG-Agent Platform` | OpenAPI/应用显示名称 |
| `APP_ENV` | `development` | 控制生产 cookie 和文档行为 |
| `DEBUG` | `true` | 启用开发文档和详细未处理错误；生产必须为 false |
| `API_V1_PREFIX` | `/api/v1` | 路由前缀；Nginx 和前端依赖该值 |
| `BACKEND_CORS_ORIGINS` | 本地 Vite origins | 逗号分隔的精确 origin；允许 credentials |
| `VITE_API_BASE_URL` | 空 | 空表示同源 Nginx；Vite 直连使用 `http://127.0.0.1:8010` |

FastAPI 本地端口 `8010`、容器端口 `8000`、Vite `5173` 和本地 Nginx `8080` 是脚本/配置常量，不是 Pydantic 字段。

## 数据库与连接池

| 变量 | 默认值 | 含义 |
|---|---|---|
| `DATABASE_URL` | 未设置 | 可选完整 SQLAlchemy DSN；正常部署避免与 `MYSQL_*` 重复配置 |
| `MYSQL_HOST` | `127.0.0.1` | Compose 覆盖为 `mysql` |
| `MYSQL_PORT` | `3306` | MySQL 服务端口 |
| `MYSQL_DATABASE` | `dwg_agent` | 应用 schema |
| `MYSQL_USER` | `dwg_user` | 应用用户 |
| `MYSQL_PASSWORD` | 空 | 非一次性开发环境必须设置 |
| `MYSQL_ROOT_PASSWORD` | 仅模板 | 由数据库脚本/Compose 使用，不是 `Settings` 字段 |
| `DB_POOL_SIZE` | 2 | 每进程持久应用连接数 |
| `DB_POOL_MAX_OVERFLOW` | 2 | 每进程突发连接数 |
| `DB_POOL_TIMEOUT_SECONDS` | 30 | 获取连接等待超时 |
| `DB_POOL_RECYCLE_SECONDS` | 3600 | 连接回收周期 |

Celery broker/result URL 分别计算为 `sqla+<effective-mysql-dsn>` 和 `db+<effective-mysql-dsn>`。受支持配置中有意不提供独立 broker URL。

## 存储与上传

| 变量 | 默认值 | 含义 |
|---|---|---|
| `STORAGE_BACKEND` | `local` | 只能是 `local` 或 `minio` |
| `LOCAL_STORAGE_ROOT` | `./var/storage` | 相对于 backend 进程工作目录 |
| `MAX_UPLOAD_SIZE_MB` | 512 | 单次上传流式限制 |
| `MAX_ZIP_EXTRACT_MB` | 2048 | ZIP 总解压大小限制 |
| `MAX_ZIP_ENTRY_COUNT` | 1000 | ZIP entry 数量限制 |
| `MINIO_ENDPOINT` | `http://localhost:9000` | API endpoint，不是 console endpoint |
| `MINIO_ACCESS_KEY` | 空 | MinIO 客户端身份 |
| `MINIO_SECRET_KEY` | 空 | MinIO 客户端 secret |
| `MINIO_ROOT_USER` | 仅模板 | Compose server 设置，不是 backend `Settings` 字段 |
| `MINIO_ROOT_PASSWORD` | 仅模板 | Compose server 设置，不是 backend `Settings` 字段 |

bucket 默认值为 `MINIO_BUCKET_ORIGINAL=dwg-original`、`MINIO_BUCKET_DERIVED=dwg-derived`、`MINIO_BUCKET_REPORTS=dwg-reports`、`MINIO_BUCKET_TEMP=dwg-temp`、`MINIO_BUCKET_DXF_ORIGINAL=dxf-original` 和 `MINIO_BUCKET_DXF_DERIVED=dxf-derived`。

两个 ZIP 限制和 DXF bucket 覆盖已存在于代码，但当前没有同时作为活动行列在两份环境模板中。因此除非操作员显式加入，否则使用默认值。

## 认证与初始化

| 变量 | 默认值 | 含义 |
|---|---|---|
| `JWT_SECRET_KEY` | 不安全的开发字符串 | 共享使用前替换为高熵 secret |
| `JWT_ALGORITHM` | `HS256` | token 签名算法 |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | 30 | access 与 SSE cookie 生命周期 |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | 14 | refresh cookie 生命周期 |
| `REFRESH_COOKIE_SECURE` | 自动 | `APP_ENV=production` 时 Secure；显式 false 是私有 HTTP 风险接受 |
| `SUPER_ADMIN_USERNAME` | `admin` | 种子用户名 |
| `SUPER_ADMIN_PASSWORD` | 不安全的开发值 | 仅在种子用户不存在时使用 |
| `SUPER_ADMIN_REAL_NAME` | `系统管理员` | 种子显示名 |

修改 `SUPER_ADMIN_PASSWORD` 不会轮换已有账号。应使用已认证的改密或管理员重置 API；不要删除已被引用的 super-admin 行来强制重新 seed。

## 功能开关与处理

| 变量 | 默认值 | 含义 |
|---|---|---|
| `AGENT_ENABLED` | `false` | Agent task 仍是占位时必须保持 false |
| `DXF_PIPELINE_ENABLED` | `false` | 启用 DWG -> DXF Job 创建 |
| `DXF2DWG_PIPELINE_ENABLED` | `false` | 启用 DXF -> DWG Job 创建 |
| `DXF2EXCEL_PIPELINE_ENABLED` | `false` | 启用 DXF batch -> Excel Job 创建 |
| `EXCEL_FINAL_PIPELINE_ENABLED` | `false` | 启用 Excel Final 端点/Job |
| `CAD_WORKER_ENABLED` | `false` | Windows worker 缺失时必须保持 false |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | 测试/开发捷径，不是生产 worker 拓扑 |
| `CELERY_STALE_JOB_TIMEOUT_SECONDS` | 7200 | running Job 无更新阈值 |
| `EXCEL_FINAL_STAGE_ROOT` | 自动探测 | 可选 Stage 路径覆盖；代码字段存在但模板未列出 |
| `EXCEL_FINAL_TIMEOUT_SECONDS` | 1800 | 子进程超时，限制为 30-7200 秒 |

ODA 字段为 `ODA_CONVERTER_VERSION=ACAD2018`、`ODA_CONVERTER_AUDIT=true`、`ODA_CONVERTER_TIMEOUT=300`、`ODA_CONVERTER_RETRIES=1`、`ODA_XVFB_RUN=true`、`DXF2DWG_CONVERTER_VERSION=ACAD2018`、`DXF2DWG_CONVERTER_AUDIT=true`、`DXF2DWG_CONVERTER_TIMEOUT=300`、`DXF2DWG_CONVERTER_RETRIES=1`，`ODA_HOME` 默认空。

## 手册库、Agent 与 CAD 占位

Excel Final 手册库默认复用平台 MySQL host/user/password，数据库为 `hardware_handbook`。独立只读账号使用 `HANDBOOK_MYSQL_HOST`、`HANDBOOK_MYSQL_PORT`、`HANDBOOK_MYSQL_DATABASE`、`HANDBOOK_MYSQL_USER` 和 `HANDBOOK_MYSQL_PASSWORD`。

Agent 占位字段为 `MODEL_NAME=deepseek-chat`、`MODEL_API_KEY`、`MODEL_BASE_URL=https://api.deepseek.com`、`MCP_CAD_COMMAND=uvx`、`MCP_CAD_ARGS=cad-mcp-server,stdio`、`AGENT_MEMORY_TTL=7200` 和 `AGENT_MAX_MESSAGES=20`。Windows 占位字段为 `CAD_WORKER_API_BASE=http://cad-worker.internal:8080` 和 `CAD_WORKER_API_KEY`。设置这些值不会实现或启用缺失 task。

## 密钥分类

| 类别 | 示例 | 规则 |
|---|---|---|
| Secret | `JWT_SECRET_KEY`、`MYSQL_PASSWORD`、`MYSQL_ROOT_PASSWORD`、`MINIO_SECRET_KEY`、`MINIO_ROOT_PASSWORD`、`SUPER_ADMIN_PASSWORD`、`MODEL_API_KEY`、`CAD_WORKER_API_KEY`、`HANDBOOK_MYSQL_PASSWORD` | 禁止提交、打印、进入客户端 bundle 或 Job error |
| 部署敏感 | host、origin、bucket 名、`REFRESH_COOKIE_SECURE` | 每个环境审查，并记录有意例外 |
| 安全默认 | pool size、timeout、feature flag | 仍需根据负载和依赖验证 |

`.env`、`backend/.env` 和 `.env.docker` 是被忽略的本地密钥文件。仓库未提供 secret manager、自动轮换或加密配置备份。

## 验证

```bash
# 解析模板与 Compose 契约
bash infra/verify.sh
docker compose config --quiet

# 验证 Settings 与数据库行为
cd backend
uv run pytest -q tests/test_config.py tests/test_mysql_runtime.py tests/test_compose.py
```

启用管线前必须同时验证 flag 和依赖 readiness。通用 worker 健康但 flag 关闭或 Stage 缺失，不能证明管线可用。
