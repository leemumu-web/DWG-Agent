# 离线发布与 gg 生产部署注意事项（r39）

本文记录 DWG-Agent 在 2026-08-17 完成 r39（`server-production-20260817-r39`）打包与
gg 生产增量部署过程中踩过的坑和必须遵守的约定。它是对[加密和打包部署指南](../../加密和打包部署指南.md)
与[部署指南](deployment.md)的运维补充，按“打包机环境、打包命令、镜像构建坑、gg 部署坑、验证清单”分层。

## 一、打包机环境

正式打包在**本机**（Arch，docker 29.6.2 + gpg 2.4.9）执行，不是 gg。本机持有发布接收者
私钥（`DWG Agent release r36`，指纹 `5B070B7819AC1334879BDD5ACE161D8F8AB15832`）。

- Docker daemon `max-concurrent-downloads=1`：基础镜像拉取并发为 1，镜像缓存命中时影响可忽略；
  冷拉基础镜像会很慢，应尽量保留构建缓存。
- uv 全局源已配置清华 TUNA 优先、阿里云兜底（`~/.config/uv/uv.toml`），`no_proxy` 含
  `tuna.tsinghua.edu.cn` / `mirrors.aliyun.com` 直连。

## 二、打包命令（必须带国内镜像源）

后端镜像构建包含 `apt-get update`（Debian）与两处 `uv sync`（backend 依赖 + excel_stage3 独立
venv）。官方源在国内会卡死，**打包必须显式传入国内镜像源**：

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework
DEBIAN_APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian \
PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
bash scripts/release.sh bundle \
  --recipient 5B070B7819AC1334879BDD5ACE161D8F8AB15832 \
  --output releases \
  --version server-production-YYYYMMDD-rNN
```

- `DEBIAN_APT_MIRROR` 由 `backend/Dockerfile` 的 `ARG` + sed 替换写入 `debian.sources`；
- `PYPI_INDEX_URL` 由 `backend/Dockerfile` 的 `ARG PYPI_INDEX_URL=` + `ENV UV_DEFAULT_INDEX=$PYPI_INDEX_URL`
  传入 **builder 与 protected 两个阶段**——protected 阶段必须在 `FROM runtime-base AS protected`
  之后重新声明 `ARG PYPI_INDEX_URL=` 与 `ENV UV_DEFAULT_INDEX=$PYPI_INDEX_URL`，
  否则 excel_stage3 的 `uv sync --directory /app/Stages/excel_stage3` 会回落到官方 PyPI 卡死。

镜像已由同一提交构建并验证时，可用 `--skip-build` 复用，跳过构建直接走验证/加密。

## 三、镜像构建坑（protected 镜像的 excel_stage3 例外）

`excel_stage3` 与 `yikongzhe` 以**独立 venv 子进程**方式保留源码（后端经
`stage_adapter.run_excel_stage3_pipeline` 以 `[stage_root]/.venv/bin/python -m excel_stage3` 调用），
与其余业务 Python 全部字节码化不同。三处发布校验必须为这个例外放行，否则打包失败：

| 校验点 | 例外规则 |
| --- | --- |
| `scripts/release.sh` `source_count` | `find` 排除 `*/Stages/excel_stage3/*` 与 `*/Stages/yikongzhe/*` |
| `scripts/release.sh` `test_count` | 排除 `*/excel_stage3/.venv/*` 与 `*/yikongzhe/.venv/*`（venv 内 numpy/shapely 自带 tests 不是业务测试） |
| `scripts/release/verify_image_archive.py` `_is_business_source` | 跳过 `app/Stages/excel_stage3` 与 `app/Stages/yikongzhe` 前缀的 `.py` |

`excel_stage3` 无 stage3 专属数据库迁移，Alembic head 与 r38 相同（`e6b1f9a2c470`）；
打包不引入新表，部署不需要跑迁移。

## 四、gg 生产增量部署

### 4.1 前置确认

- gg 生产：16 核 / 62GiB，compose 项目 `dwg-agent`，数据卷 `dwg-agent_mysql_data`、
  `dwg-agent_minio_data`、`dwg-agent_app_var`，双网络 `internal` / `public`。
- 升级前必须无运行中/排队任务（`jobs` 表无 `running`/`queued`）。
- **gg 默认没有 gpg 私钥**，`server-deploy.sh install` 解密会失败。部署前需把本地私钥
  导出传 gg 并导入（受口令保护），完成后删除临时私钥文件：
  ```bash
  gpg --batch --armor --export-secret-keys <指纹> > /tmp/dwg-release-key.asc
  scp /tmp/dwg-release-key.asc gg:/tmp/
  ssh gg 'gpg --batch --import /tmp/dwg-release-key.asc && rm -f /tmp/dwg-release-key.asc'
  ```

### 4.2 传输与安装

```bash
# 本机 -> gg
scp releases/dwg-agent-server-production-<版本>.tar.gz.gpg \
    releases/dwg-agent-server-production-<版本>-deploy.sh \
    releases/dwg-agent-server-production-<版本>.tar.gz.gpg.sha256 \
    gg:/opt/dwg-agent/releases/
# gg 上校验 + install（install 保留 .env.docker 与数据卷，生成 .rollback-candidate）
ssh gg 'cd /opt/dwg-agent/releases && \
  sha256sum -c dwg-agent-server-production-<版本>.tar.gz.gpg.sha256 && \
  ./dwg-agent-server-production-<版本>-deploy.sh install \
    dwg-agent-server-production-<版本>.tar.gz.gpg /opt/dwg-agent/server'
```

### 4.3 数据库保留原配置（强制）

打包包含 mysql/minio 镜像 tag，但其内容与旧版本相同（同镜像 ID 重新 tag）。生产按
“数据库不打包、保留原配置”约定，**install 后把 compose 的 mysql/minio 镜像 tag 改回生产
既有 tag**（r38 时代为 `server-production-20260801-r37`），再 recover：

```bash
ssh gg 'sed -i \
  "s|dwg-agent-mysql:server-production-<新版本>|dwg-agent-mysql:server-production-20260801-r37|g; \
   s|dwg-agent-minio:server-production-<新版本>|dwg-agent-minio:server-production-20260801-r37|g" \
  /opt/dwg-agent/server/compose.server.yaml'
```

数据卷、`.env.docker` 由 install 保留，recover 不删除。

### 4.4 recover 与服务数

```bash
ssh gg '/opt/dwg-agent/server/scripts/server-deploy.sh recover /opt/dwg-agent/server'
```

完整生产服务数为 **17**（含 `worker-excel-stage3`）。`server-deploy.sh` 的
`server_wait_all_services` 硬编码校验服务数，必须与当前 compose 一致：
- 服务数变化后（如新增 worker），同步 `scripts/release/server-deploy.sh` 的
  `[[ "${#services[@]}" -eq <N> ]]` 并重新打包（或 gg 上直接修改后 recover）。

recover 会重建全部容器并等待健康检查、生产特征矩阵、余料 MySQL/MinIO 回读烟测。

## 五、验证清单（部署后）

- `docker ps`：17 个容器全部 `Up (healthy)`，含 `worker-excel-stage2` 与 `worker-excel-stage3`。
- `docker ps` 镜像 tag：`dwg-agent-mysql` / `dwg-agent-minio` 为保留的旧 tag，backend/frontend 为新版本。
- 数据库数据：`jobs`、`files`、`workflow_runs`、`excel_final_batches` 计数与升级前一致（或只增加新的业务数据）。
- Alembic：`alembic current` 为 `e6b1f9a2c470 (head)`。
- 拆板约束：后端经 `invoke_splitter` 调用拆板 CLI 时传 `--lean-report`，且硬性要求子进程
  stderr 为空、stdout 为 JSON——任何拆板子进程打印日志到 stderr 都会导致任务失败。
- 前端通过 `http://localhost:1117`（本机 ssh 转发到 gg:80）确认新页面可访问。

## 六、回滚

- `.rollback-candidate/` 保留上一次安装的旧 compose、镜像清单与 RELEASE；数据卷不随镜像回滚。
- 镜像回滚前必须确认无新数据库迁移（r39 无迁移，可直接切回旧镜像）；否则按数据库迁移回退方案执行。
- 严禁 `docker compose down -v` / `docker volume rm`（会永久删除数据库与对象文件）。
