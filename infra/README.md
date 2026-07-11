# 基础设施

`infra/` 维护 Nginx 网关、MySQL 初始化数据/授权和静态部署验证。最终 Compose 拓扑位于根 `compose.yaml`，详细部署和恢复边界见[部署](../docs/deployment.md)与[运维](../docs/operations.md)。

```bash
cp .env.docker.example .env.docker
# 替换 CHANGE_ME_*；可信内网纯 HTTP 场景显式设置 REFRESH_COOKIE_SECURE=false。

bash scripts/docker.sh check
bash scripts/docker.sh build
bash scripts/docker.sh up
bash scripts/docker.sh up-workers
bash scripts/docker.sh smoke

# 通过 check/build 后的直接 Compose 等价命令
docker compose up -d
docker compose --profile workers up -d
```

前端在 `frontend/Dockerfile` 内构建，并由非特权 Nginx 提供，不依赖宿主 `frontend/dist`。核心 profile 包含 Nginx、API、MySQL、MinIO 和 `worker-report`；`workers` profile 增加 `worker-dxf`、`worker-dxf2dwg`、`worker-dxf2excel`、`worker-excel-final` 与无任务实现的 `worker-agent` 占位。MySQL/MinIO 不发布宿主端口，Redis/Valkey 和 Flower 不存在。

Agent 功能保持禁用；`worker-agent` 进程健康只说明 Celery 已连接 MySQL broker，不表示存在 Agent task。

```bash
bash scripts/docker.sh backup /secure/backups/dwg-agent-YYYY-MM-DD
bash scripts/docker.sh down
bash scripts/docker.sh restore /secure/backups/dwg-agent-YYYY-MM-DD
bash scripts/docker.sh up
```

恢复会替换 MinIO volume。备份不是跨 MySQL/MinIO 原子快照，也没有调度、加密、PITR 或自动演练。当前 Compose 仅发布 HTTP，不发布 443；`Stages/dxf2excel` 仍阻断 clean clone；也没有 secret manager、可观测栈或多副本滚动部署。
