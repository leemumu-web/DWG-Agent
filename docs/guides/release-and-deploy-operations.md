# DWG-Agent 打包加密部署完整操作手册

本文档面向正式发布与生产部署操作，把从“仓库代码”到“gg 生产运行”的每一个文件、每一条命令、
每一步操作与验收标准完整写清。以 2026-08-17 的 r39 / r39.1 发布为基准。

> 关联文档：[离线发布与 gg 部署注意事项](offline-release-deploy-notes.md)、
> [部署指南](deployment.md)、[加密和打包部署指南](../../加密和打包部署指南.md)。

---

## 一、发布体系总览

| 机器 | 角色 | 关键路径 |
|---|---|---|
| **本机**（Arch，Creeken） | 打包机 / 受控发布机 | 仓库 `/home/Creeken/Paper/CAD_research/complete_framework/` |
| **gg**（Ubuntu，Tailscale `100.99.118.50`，内网 `192.168.188.50`） | 生产机 / 部署端 | `/opt/dwg-agent/server/` |

### 1.1 保护边界

- **机密性**：发布包 GPG 公钥加密（接收者 `5B070B7819AC1334879BDD5ACE161D8F8AB15832`，`DWG Agent release r36`，rsa3072，2026-07-31 签发，2031-07-30 到期）。
- **源码降可读**：backend `protected` 镜像编译业务 Python 为字节码并删除 `.py`；`excel_stage3`/`yikongzhe` 以独立 venv 子进程方式保留源码（例外）。
- **完整性**：包内 `SHA256SUMS` + 包外 `.sha256` 双层校验。
- **秘密隔离**：`.env.docker`、数据库内容、MinIO 对象一律不进发布包。
- **运行时隔离**：容器非 root、只读根、删 capability、日志限流。

### 1.2 当前生产版本快照（2026-08-17）

| 组件 | 版本 | 说明 |
|---|---|---|
| backend（API/dispatcher/多数 worker） | `server-production-20260817-r39` | 含 PR #21 拆板收敛、excel_stage3 后端 |
| backend worker-excel-stage3 | `server-production-20260817-r39.1` | numpy 1.26.4 修复版（见 §8） |
| frontend / nginx | `server-production-20260817-r39` | 含 ExcelStage3Panel |
| mysql | `server-production-20260801-r37` | **保留原 tag**（数据库不打包） |
| minio | `server-production-20260801-r37` | **保留原 tag** |
| RELEASE 标记 | `server-production-20260817-r39` | `/opt/dwg-agent/server/RELEASE` |
| .rollback-candidate | r38.7 配置 | install 时保留的旧版本候选 |

生产服务共 **17 个**（全部 healthy）：`nginx backend-api dispatcher worker-report worker-dxf
worker-dxf2dwg worker-dxf2excel worker-dxf-classification worker-dxf-split worker-remnant-convert
worker-remnant-parse worker-excel-final worker-excel-stage2 worker-excel-stage3 worker-maintenance
mysql minio`。

---

## 二、涉及文件清单

### 2.1 打包机（本机仓库）

| 文件 | 作用 |
|---|---|
| `scripts/release.sh` | 打包总入口：构建 → 验证 → 渲染 compose → 导出镜像 → 加密。命令：`bundle` |
| `scripts/release/render_server_compose.py` | 把 `compose.yaml` 渲染成只引用固定发布镜像的 `compose.server.yaml`（移除 build/profiles） |
| `scripts/release/server-deploy.sh` | 部署器（包内 + 独立副本）：`install/up/recover/enable-service/status/smoke/down` |
| `scripts/release/server-timezone-migrate.sh` | 北京时区切换的 preflight/migrate/rollback |
| `scripts/release/verify_image_archive.py` | 导出镜像逐层审计，拒绝任何历史层含业务 `.py`（excel_stage3/yikongzhe 例外） |
| `scripts/release/verify_runtime_features.py` | 生产运行时特征矩阵烟测（功能开关 + 常开能力） |
| `scripts/release/verify_live_remnant.py` | 服务健康后余料 MySQL/MinIO 回读烟测 |
| `backend/Dockerfile` | 多阶段构建：builder → runtime-base → runtime → bytecode-compiler → protected |
| `compose.yaml` | 开发/打包源 Compose（17 个服务，含 excel-stage2/3 worker） |
| `.env.docker`（本机） | 打包用镜像名、构建环境（`DWG_AGENT_IMAGE` 等） |
| `.env.docker.example` | 无秘密配置模板，随包分发 |
| `infra/database/mysql/init.sql`、`hardware_handbook.sql` | 数据库初始化 SQL，随包分发 |
| `scripts/docker.sh` | 打包前 Docker 环境检查 |
| `scripts/lib/common.sh` | `release.sh` 依赖的公共函数 |

### 2.2 生产机（gg）

| 路径 | 作用 |
|---|---|
| `/opt/dwg-agent/server/` | 发布根目录（install 目标） |
| `/opt/dwg-agent/server/compose.server.yaml` | 生产 Compose（install 写入） |
| `/opt/dwg-agent/server/.env.docker` | **生产运行秘密**（install 保留，权限 0600） |
| `/opt/dwg-agent/server/scripts/` | server-deploy.sh 等（install 覆盖） |
| `/opt/dwg-agent/server/RELEASE` | 当前版本标记 |
| `/opt/dwg-agent/server/.rollback-candidate/` | install 保存的上一版本候选（RELEASE/compose/images.manifest） |
| `/opt/dwg-agent/server/backups/` | 时区迁移等备份 |
| `/opt/dwg-agent/releases/` | 历史加密发布包（r26 → r39） |
| `/opt/dwg-agent/server/infra/` | 随包的基础设施配置 |

### 2.3 GPG 密钥

| 位置 | 内容 |
|---|---|
| 本机 keyring | 接收者私钥（`DWG Agent release r36`，指纹 `5B070B78...`) |
| gg keyring（`~/.gnupg/`） | install 解密所需私钥（部署前手动导入） |

---

## 三、打包完整流程

### 3.1 前置检查

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework

# 1. 代码与测试
git status --short            # 无未确认业务改动
bash scripts/verify.sh quick  # 快速门禁（ruff + 聚焦测试 + 前端构建）

# 2. 工具与密钥
docker version                # 需 ≥29
gpg --batch --list-secret-keys 5B070B7819AC1334879BDD5ACE161D8F8AB15832  # 私钥必须存在
command -v gzip

# 3. 磁盘与网络
df -h /                       # 至少留 20G（镜像构建 + 打包）
```

### 3.2 打包命令（必须带国内镜像源）

```bash
DEBIAN_APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian \
PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
bash scripts/release.sh bundle \
  --recipient 5B070B7819AC1334879BDD5ACE161D8F8AB15832 \
  --output releases \
  --version server-production-YYYYMMDD-rNN
```

> `--skip-build`：镜像已由同一提交构建并验证时可跳过构建（仅用于复用，禁止跨提交）。

### 3.3 Docker 镜像构建详解

#### 3.3.1 backend/Dockerfile 多阶段（每个阶段做什么）

| 阶段 | 做什么 |
|---|---|
| `builder` | 从 `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`；声明 `ARG DEBIAN_APT_MIRROR` / `ARG PYPI_INDEX_URL`；`ENV UV_DEFAULT_INDEX=$PYPI_INDEX_URL`；apt-get install ca-certificates（`DEBIAN_APT_MIRROR` 经 sed 替换 `debian.sources`）；COPY 全部 Stages + backend 源码；`uv sync --frozen --no-dev`（backend 主依赖，锁定 uv.lock） |
| `runtime-base` | 无源码基础镜像：安装后端运行时系统依赖、ODA AppImage（`Stages/dwg2dxf/tools/oda`）等 |
| `runtime` | 从 runtime-base；COPY builder 的 `/app/Stages`、`excel_final` 等完整运行文件 |
| `bytecode-compiler` | 从 runtime；`compileall --invalidation-mode checked-hash -b` 把业务 Python 编译为 `.pyc`（逻辑路径 `/opt/dwg-agent`）；写 `box_protected_runtime_manifest.json`；**删除** `/app/app`、`/app/migrations`、`/app/Stages` 下全部 `.py`、tests/samples/data/output、`pyproject.toml`、`uv.lock`、`*.egg-info`、tools 等 |
| `protected` | 从 runtime-base（不含源码的历史层）；COPY bytecode-compiler 清理后的运行树；**例外**：COPY `Stages/excel_stage3` + `Stages/yikongzhe` 源码，`uv sync --directory /app/Stages/excel_stage3 --no-dev` 预建独立 venv（protected 阶段必须**重新声明** `ARG PYPI_INDEX_URL` + `ENV UV_DEFAULT_INDEX=$PYPI_INDEX_URL`，否则该 venv 回落官方 PyPI 卡死） |

#### 3.3.2 构建参数如何传递

```text
release.sh 环境变量 → docker compose build --build-arg
  DEBIAN_APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian
  PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
      ↓
  Dockerfile ARG → ENV
  DEBIAN_APT_MIRROR → sed 替换 debian.sources（apt 走清华）
  PYPI_INDEX_URL   → UV_DEFAULT_INDEX（uv sync 走清华）
```

注意：
- `ENV UV_DEFAULT_INDEX=$PYPI_INDEX_URL` 必须在 **builder 与 protected 两个阶段分别声明**（protected 从 runtime-base 重新开始，不继承 builder 的 ENV）。
- 不传镜像源时，apt-get update 与 excel_stage3 的 uv sync 会访问官方源，国内会卡死（见 §11）。

#### 3.3.3 前端镜像构建（nginx）

```text
frontend-builder 阶段
  ARG VITE_API_BASE_URL=
  COPY frontend/
  RUN npm ci && npm run build      # npm run build = check:architecture + tsc -b + vite build
runtime 阶段（FROM ghcr.io/nginxinc/nginx-unprivileged:1.27-alpine）
  COPY --from=frontend-builder dist/ /usr/share/nginx/html/
```

前端架构检查（`frontend/scripts/check-architecture.mjs`）有**单文件 600 行上限**。单文件超限会使
`npm run build` 失败（实例：`ExcelFinalPage.tsx` 曾被注释批推到 601 行，需把常量/helper 拆分到子文件）。

#### 3.3.4 镜像缓存与构建加速

- 缓存挂载：`--mount=type=cache,target=/root/.cache/uv`、`/var/cache/apt`、`/var/lib/apt`；`UV_LINK_MODE=copy` 避免符号链接跨层失效。
- 本地已有 r36~r39 镜像与约 18GB 构建缓存，重复构建命中大部分层，只有变更层重建。
- 首次冷拉基础镜像受 Docker daemon `max-concurrent-downloads=1` 限制会较慢；registry-mirrors（`docker.m.daocloud.io`）已配置加速。

### 3.4 release.sh bundle 逐步做了什么

| 步骤 | 命令/文件 | 说明 |
|---|---|---|
| 1 | `scripts/docker.sh check` | 检查 Docker 可用 |
| 2 | `docker compose build backend-api nginx` | 构建受保护 backend 镜像 + 前端镜像；`DEBIAN_APT_MIRROR`/`PYPI_INDEX_URL` 作为 build-arg 传入 |
| 3 | `docker image tag` × 4 | 为 backend/frontend/mysql/minio 打 `:${version}` 标签 |
| 4 | `release_verify_protected_image` | 只读容器内检查：无业务 `.py`（excel_stage3/yikongzhe 例外）、无 Excel 样本、无 Stage tests（venv 内第三方 tests 例外）、关键模块可导入、单迁移 head、CLI 可用、**excel_stage3 venv numpy 基线 ≤ SSE3** |
| 5 | `release_verify_oda_roundtrip` | 镜像内 ODA 完成一次 DXF→DWG→DXF 真实转换 |
| 6 | `render_server_compose.py` | 生成只引用发布镜像的 `compose.server.yaml` |
| 7 | `verify_image_archive.py` | 导出 `images.tar` 并逐层审计，拒绝任何历史层含业务 `.py`（excel_stage3/yikongzhe 例外） |
| 8 | SHA256SUMS | 生成包内清单 |
| 9 | `gpg --encrypt --recipient` | `tar → gzip -9 → gpg` 混合加密 |
| 10 | 立即解密验证 | 用本地私钥解密 + 遍历 tar，失败则删除密文 |
| 11 | 产物 | `releases/dwg-agent-server-production-<版本>.tar.gz.gpg` + `-deploy.sh` + `.sha256` |

### 3.5 产物

```text
releases/
  dwg-agent-server-production-<版本>.tar.gz.gpg       # 加密发布包（~720MB）
  dwg-agent-server-production-<版本>-deploy.sh         # 独立部署器（复制包内 server-deploy.sh）
  dwg-agent-server-production-<版本>.tar.gz.gpg.sha256 # 包外哈希（加密包 + deploy.sh）
```

包内结构：`SHA256SUMS RELEASE images.tar images.manifest .env.docker.example compose.server.yaml
scripts/server-deploy.sh scripts/server-timezone-migrate.sh infra/database/mysql/init.sql infra/database/mysql/hardware_handbook.sql`

---

## 四、传输与部署流程（gg 增量部署）

### 4.1 前置：gg 私钥导入（仅首次或密钥丢失后）

```bash
# 本机导出 → 传 gg → gg 导入 → 删除临时文件
gpg --batch --armor --export-secret-keys 5B070B7819AC1334879BDD5ACE161D8F8AB15832 > /tmp/dwg-release-key.asc
scp /tmp/dwg-release-key.asc gg:/tmp/
ssh gg 'gpg --batch --import /tmp/dwg-release-key.asc && rm -f /tmp/dwg-release-key.asc'
ssh gg 'gpg --batch --list-secret-keys'   # 确认可见 sec rsa3072
rm -f /tmp/dwg-release-key.asc            # 本机也清理
```

### 4.2 升级前检查（gg）

```bash
ssh gg 'docker exec dwg-agent-mysql-1 mysql -uroot -p"$(grep MYSQL_ROOT_PASSWORD /opt/dwg-agent/server/.env.docker | cut -d= -f2)" dwg_agent -e "SELECT status, COUNT(*) FROM jobs GROUP BY status;"'
# 必须无 running / queued（只允许 succeeded / failed / cancelled）
```

### 4.3 传输

```bash
scp releases/dwg-agent-server-production-<版本>.tar.gz.gpg \
    releases/dwg-agent-server-production-<版本>-deploy.sh \
    releases/dwg-agent-server-production-<版本>.tar.gz.gpg.sha256 \
    gg:/opt/dwg-agent/releases/
# Tailscale 约 0.9 MB/s，720MB 包约 13 分钟；等待 sha256 一致再继续
ssh gg 'cd /opt/dwg-agent/releases && sha256sum -c dwg-agent-server-production-<版本>.tar.gz.gpg.sha256'
```

### 4.4 install（保留 .env.docker 与数据卷）

```bash
ssh gg 'cd /opt/dwg-agent/releases && \
  ./dwg-agent-server-production-<版本>-deploy.sh install \
    dwg-agent-server-production-<版本>.tar.gz.gpg /opt/dwg-agent/server'
```

install 做什么：校验包外 sha256 → gpg 解密 → 校验包内 SHA256SUMS → `docker load images.tar`（4 个镜像）→ 原子备份旧 compose 到 `.rollback-candidate/` → 安装新 compose.server.yaml 与 scripts → **保留原 `.env.docker` 与三个数据卷**。

### 4.5 数据库保留原配置（强制，每次必做）

```bash
# install 会把 mysql/minio 指向新版本 tag（内容与旧版同 ID），按约定改回生产既有 tag：
ssh gg 'sed -i \
  "s|dwg-agent-mysql:server-production-<新版本>|dwg-agent-mysql:server-production-20260801-r37|g; \
   s|dwg-agent-minio:server-production-<新版本>|dwg-agent-minio:server-production-20260801-r37|g" \
  /opt/dwg-agent/server/compose.server.yaml'
```

> 若将来要升级 mysql/minio 镜像本身，必须单独走数据库镜像升级流程并验证备份，不得随业务增量包隐式更换。

### 4.6 recover 启动

```bash
ssh gg '/opt/dwg-agent/server/scripts/server-deploy.sh recover /opt/dwg-agent/server'
```

recover 按 MySQL/MinIO → backend → Nginx/workers 顺序重建全部容器，等待健康检查、生产特征矩阵、
余料 MySQL/MinIO 回读烟测。**recover 会重建全部容器**；若只想更新个别服务（如 excel-stage3 worker），
用 `docker compose -f compose.server.yaml --env-file .env.docker up -d --no-deps <服务>`。

### 4.7 服务数校验

`server-deploy.sh` 的 `server_wait_all_services` 硬编码校验服务数（当前 **17**）。服务数变化时：
同步修改 `scripts/release/server-deploy.sh` 的 `[[ "${#services[@]}" -eq 17 ]]`，并重新打包（或 gg 上改后 recover）。

---

## 五、部署后验证清单

```bash
# 1. 容器健康（17 个全部 Up healthy）
ssh gg 'docker ps --format "{{.Names}} {{.Status}} {{.Image}}"'

# 2. 镜像 tag 正确（backend/frontend 新版本；mysql/minio 保留 r37）
ssh gg 'docker ps --format "{{.Names}} {{.Image}}" | grep -E "mysql|minio"'

# 3. 数据库数据保留
ssh gg 'docker exec dwg-agent-mysql-1 mysql -uroot -p"$(grep MYSQL_ROOT_PASSWORD /opt/dwg-agent/server/.env.docker | cut -d= -f2)" dwg_agent -e "SELECT (SELECT COUNT(*) FROM jobs) j,(SELECT COUNT(*) FROM files) f,(SELECT COUNT(*) FROM workflow_runs) r;"'

# 4. Alembic 无迁移漂移
ssh gg 'docker exec dwg-agent-backend-api-1 sh -c "cd /app && alembic current"'

# 5. excel_stage3 worker venv numpy 可导入（旧 CPU 兼容）
ssh gg 'docker exec dwg-agent-worker-excel-stage3-1 sh -c "/app/Stages/excel_stage3/.venv/bin/python -c \"import numpy; print(numpy.__version__)\""'

# 6. 前端
curl -s http://localhost:1117/nginx-health   # 本机 ssh 转发
curl -s http://localhost:1117/ | grep -oE "assets/index-[A-Za-z0-9_]+\.js"
```

---

## 六、本机端口映射（长期稳定）

```bash
# systemd 用户服务（已配置）：
cat ~/.config/systemd/user/dwg-agent-tunnel.service
# ExecStart=/usr/bin/ssh -N -T -L 1117:localhost:80 gg -o ServerAliveInterval=30 ...
systemctl --user status dwg-agent-tunnel   # active
# 访问：http://localhost:1117
```

---

## 七、版本号约定

格式：`server-production-YYYYMMDD-rNN`。同一版本名不可重复构建/覆盖；代码变化必须提升版本号。

| 版本 | 日期 | 要点 |
|---|---|---|
| r36 | 07-31 | 全量加密发布（GPG 自检解密起） |
| r37.1 / r37.2 / r37.3 | 08-01 ~ 08-07 | 增量（images/remote），excel_stage2 上线 |
| r38 系列（r38~r38.7） | 08-07 ~ 08-09 | 前端/后端多轮增量，gg 本机打包 |
| **r39** | 08-17 | 完整增量：PR #21 拆板 + excel_stage3 全链路 + 前端 |
| **r39.1** | 08-17 | excel_stage3 venv numpy 1.26.4 修复（仅 worker-excel-stage3 更新） |
| r40（下一版） | 待定 | 见 §9 要求 |

---

## 八、当前生产已知边界与注意事项

1. **gg CPU 是 Intel Core2 Duo（仅 SSE3/SSE4_2，不支持 AVX/X86_V2）**：所有 NumPy 依赖必须 ≤ SSE3 基线。
   backend 主环境早已约束；`excel_stage3` 独立 venv 在 r39.1 起锁定 `numpy>=1.26,<2`。release.sh 已加
   excel_stage3 venv numpy 基线验证，防止回归。
2. **数据库不打包、保留原配置**：mysql/minio 镜像 tag 保持 r37，数据卷/`.env.docker` 不动。
3. **拆板子进程约束**：后端经 `invoke_splitter` 调用拆板 CLI 传 `--lean-report`，且硬性要求子进程
   stderr 为空、stdout 为 JSON。任何拆板打印到 stderr 都会导致任务失败。
4. **excel_stage3 worker 需要独立 venv 且 CPU 兼容**，见 §9。
5. RELEASE 文件与 images.manifest 曾在 r38.5 后过时，install 会正确更新 RELEASE。

---

## 九、下一次增量更新的要求（r40 检查清单）

| # | 要求 | 操作 |
|---|---|---|
| 1 | **打包必须带镜像源** | `DEBIAN_APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian` + `PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`，否则 apt-get update 与 excel_stage3 uv sync 卡死 |
| 2 | **版本号提升** | `--version server-production-YYYYMMDD-r40`，与现有 r39/r39.1 不同 |
| 3 | **数据库保留原配置** | install 后按 §4.5 把 mysql/minio tag 改回 `server-production-20260801-r37` |
| 4 | **服务数校验** | 若服务数变化，同步 `server-deploy.sh` 的 `-eq <N>`；当前 17 |
| 5 | **excel_stage3 numpy 兼容** | 保持 `numpy>=1.26,<2` 约束；release.sh 会验证 venv numpy 基线 ≤ SSE3 |
| 6 | **发布校验例外** | source_count / test_count / verify_image_archive 对 excel_stage3/yikongzhe 的例外已在 release.sh/verify_image_archive.py 固化 |
| 7 | **gg 私钥** | 已导入 gg；若更换接收者密钥需重新导入 |
| 8 | **升级前无运行任务** | `jobs` 表无 running/queued |
| 9 | **验证清单** | 按 §5 逐项执行；特别确认 excel-stage3 worker 健康且 venv numpy 可导入 |
| 10 | **前端强刷** | 浏览器 `Ctrl+Shift+R` 避开旧缓存 |

**增量部署的最小化路径**（不重建全部容器）：只更新变化的服务时，用
`docker save` 单个 backend 镜像 → `scp` → `docker load` → 改 `compose.server.yaml` 对应服务 tag →
`docker compose up -d --no-deps <服务>`，其余容器不受影响。

---

## 十、回滚

1. `.rollback-candidate/` 保留上一版本（RELEASE/compose/images.manifest）。
2. 镜像回滚前确认无新数据库迁移（r39 无迁移，可直切旧镜像）；否则按数据库迁移回退方案。
3. 回滚顺序与 recover 相同（MySQL/MinIO → backend → Nginx/workers）。
4. **严禁** `docker compose down -v` / `docker volume rm` / `docker system prune --volumes`（会永久删除数据库与对象文件）。

---

## 十一、常见问题

| 症状 | 处理 |
|---|---|
| 打包卡在 apt-get update / uv sync | 未传镜像源；按 §3.2 显式传 `DEBIAN_APT_MIRROR` + `PYPI_INDEX_URL` |
| 打包报 "Stage tests remain" | 校验未排除 excel_stage3/yikongzhe venv；确认 release.sh 为最新 |
| 打包报 "business Python source exists in an image layer" | verify_image_archive 未排除 excel_stage3；确认脚本为最新 |
| recover 报 "must contain exactly N services" | `server-deploy.sh` 服务数与 compose 不一致；同步为 17 |
| excel-stage3 worker 任务失败 `NumPy ... X86_V2` | venv numpy 被升到 2.x；锁定 `numpy>=1.26,<2` 并重建镜像 |
| gg install 报 gpg 解密失败 | gg 无私钥；按 §4.1 导入 |
| 浏览器看不到新前端 | 强刷 `Ctrl+Shift+R`；确认 nginx 镜像为新版本 |
