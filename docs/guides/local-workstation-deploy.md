# 本机工作站部署适配

本文记录把本仓库 Compose 栈部署到开发者工作站的适配点，供后续维护与本机复现参考。生产部署遵循 [deployment.md](deployment.md)；本节只描述工作站与标准生产服务器的差异及本机已验证的取值。

## 本机约束

- **Docker 需要 sudo**：普通用户不在 `docker` 组，`docker`/`docker compose` 必须经 `sudo` 执行。脚本已统一通过 `scripts/lib/common.sh` 的 `DOCKER_BIN`/`dck` 自动检测：交互终端下自动退化为 `sudo docker`（按需输入密码），无 sudo 或非交互环境保持 `docker` 原行为。
- **网络**：`docker.io` 直连被阻断；`ghcr.io`、`quay.io`、`public.ecr.aws`、PyPI、Debian 与 npm 均可直连。镜像拉取优先走已配置的 registry-mirrors（`/etc/docker/daemon.json`）；docker.io 镜像被镜像源白名单拦截时，用 `skopeo` 走本机 xray 代理直拉再 `docker-daemon:` 导入（见 `~/.claude` 记忆 `local-workflow-preferences`）。
- **资源**：24 vCPU / 30 GiB 内存；Compose 的 `*_CPU_LIMIT`/`*_MEMORY_LIMIT` 保持模板默认即可。

## 本机取值（`.env.docker`，已 gitignore）

- `HTTP_PORT=18080`：避免占用特权端口 80。
- `DWG_AGENT_IMAGE=dwg-agent-backend:local`、`DWG_AGENT_FRONTEND_IMAGE=dwg-agent-frontend:local`：本机从源码构建。
- `MYSQL_IMAGE` / `MINIO_IMAGE` / `NODE_IMAGE`：保持 `compose.yaml` 默认的可达 registry digest（ECR Public / quay.io），本机可直连。
- `DATABASE_URL` 与 `MYSQL_PASSWORD` 必须一致（后端 engine 与 Celery broker/result 都以该 DSN 为准）。本机已把模板占位口令替换为 URL-safe 强口令（字符集不含 `@ : / # ? % &`，避免破坏 DSN 解析）。
- 生产特性开关与 `compose.sh` 的 `compose_require_production_features` 校验保持一致。

## 构建上下文瘦身

仓库根含有大量本机工作数据（git 已忽略），累计约 20 GB+。`.dockerignore` 已追加排除：

```text
tmp_data/
BH_拆板前_dxf汇总/
BOX_拆板前_dxf汇总/
太子/
准备优化的目标图纸/
BOX拆板前分类/
BH_optimization/
```

镜像只 COPY `backend/`、`Stages/` 子集与 `scripts/`，上述目录不进入构建上下文。

## 启动与验证

```bash
# 检查（会自动用 sudo 解析 docker）
bash scripts/docker.sh check

# 构建 + 启动核心栈（nginx / backend-api / dispatcher / mysql / minio）
bash scripts/docker.sh up

# 启动转换 worker（按需）
bash scripts/docker.sh up-workers

bash scripts/docker.sh status
bash scripts/docker.sh smoke
```

- 首次 `up` 会执行 Alembic migration、幂等 seed 并创建 MinIO bucket；`backend-api` 健康后再拉起 Nginx。
- 浏览器访问 `http://localhost:18080/`（Nginx 反代 SPA + `/api`）。
- 排障：`bash scripts/docker.sh logs`；MySQL 调试可用 `compose.local-debug.yaml` 覆盖层把 3306 暴露到本机 `127.0.0.1:13306`。

## 记录

- `.env.docker`、`.env` 等密钥文件在 `.gitignore` 内，不入库。
- 脚本与 `.dockerignore` 的适配已随仓库提交，见 CHANGELOG `[Unreleased]`。

## 本机验证结果（2026-08-29）

在 24 vCPU / 30 GiB 工作站上完成验证：

- 构建：`dwg-agent-backend:local`（含 ODA、xvfb、全部 Stages editable 依赖）与 `dwg-agent-frontend:local`（npm ci + Vite）从源码构建成功。
- 核心栈 6 服务（nginx/backend-api/dispatcher/worker-report/mysql/minio）与全部 11 个转换 worker 共 **17 个容器全部 healthy**，总内存约 3.4 GiB。
- `nginx-health`、`/health/ready`（database+storage ok）、SPA 均返回 200；`docker.sh smoke` 生产特性矩阵通过。
- API 登录 + 鉴权调用（`/api/v1/auth/me`）经公共网关返回 seeded `super_admin`，MySQL 迁移出 53 张表。
- `docker.sh verify-storage` 通过 MySQL 登记的 MinIO 写/读/删事务验证。
- 修复：非 80 `HTTP_PORT` 下 Starlette 尾部斜杠 307 重定向丢失端口（nginx `Host` 头改用 `$http_host`）；`VERIFY_ADMIN_*` 对齐 seeded 超管账号使存储验证可直接运行。
