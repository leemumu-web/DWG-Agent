# 运维脚本

本目录是本地、Compose、数据库、Windows 转发和质量门禁的操作入口。所有命令应从仓库根目录执行；脚本不会把“进程存在”直接解释为转换管线可用。根目录 Shell 文件是稳定的人机/自动化接口，具体实现按职责放在 `lib/`、`cad/`、`docs/`、`storage/`、`windows/`，调用者不应绕过稳定入口复制数据库或进程控制逻辑。

## 目录责任

| 目录或文件 | 单一责任 |
|---|---|
| `lib/common.sh` | 仓库路径、输出、环境文件、端口与宿主服务原语。 |
| `lib/database.sh` | MySQL 配置、迁移、种子、备份恢复和 `db_main`。 |
| `lib/compose.sh` | Compose 校验、生命周期、MySQL/MinIO 备份恢复和 `compose_main`。 |
| `lib/local_stack.sh` | 本地 FastAPI/Vite 进程归属、PID 和构建/运行版本新旧检查。 |
| `lib/cad_worker.sh` | Celery 队列拓扑、worker 生命周期及 Xvfb 所有权。 |
| `lib.sh` | 仅供旧调用者兼容的聚合导出；新增入口应按需加载上述库。 |
| `cad/` | 真实 CAD 样本基准工具。 |
| `docs/` | API 文档生成与仓库文档契约检查。 |
| `storage/` | 对象回收与数据库/对象存储事务探针。 |
| `windows/` | Linux 侧访问 Windows 节点的通信脚本。 |
| `release.sh`、`release/` | 构建不含业务 Python 源码的镜像，生成加密离线发布包、服务器 Compose 与独立部署器。 |

## 常用闭环

```bash
# 按当前代码重启全部受管服务并重建前端
bash scripts/start-all.sh

# 查看服务、worker、运行代码版本和前端构建新旧
bash scripts/status.sh

# 汇总依赖、代码漂移以及近期 4xx/5xx/499
bash scripts/doctor.sh --since-minutes 60

# 运行快速或完整质量门禁
bash scripts/verify.sh quick
bash scripts/verify.sh full --allow-blocked
```

`status.sh` 正常返回 0；服务异常或运行代码过期返回 1。`doctor.sh` 未发现需立即处理的问题返回 0，发现 405/409/5xx 或服务异常返回 1，参数错误或日志无法检查返回 2。Nginx 499 单独显示为客户端断开，不等同于 FastAPI 返回的错误。

## 启动与停止

| 脚本 | 用途 | 关键边界 |
|---|---|---|
| `start-all.sh` | MySQL、九个 worker、FastAPI、前端 dist、Nginx | 每次停止本项目旧实例、同步后端锁定依赖、重建前端并重启全部受管应用服务；未知进程占用端口时拒绝覆盖。 |
| `start-all.sh --restart-backend` / `--rebuild` | 旧调用兼容参数 | 当前普通启动已默认重启后端和重建前端，参数无需再显式传入。 |
| `start-dev.sh` | Uvicorn reload + Vite HMR | 开发入口，不经过本地 Nginx 静态托管。 |
| `stop-all.sh` | 停止受管 Nginx、前端、后端和 worker | 不强杀未知 8010 进程；MySQL 默认保留。 |
| `status.sh` | 无副作用状态检查 | 检查 MySQL、worker、后端、新旧代码、前端 dist 和 Nginx。 |

## 诊断与验证

`doctor.sh` 默认读取 `infra/gateway/nginx/logs/access.log` 最近 60 分钟。它按状态、方法和去查询串路径聚合，只输出有限 request ID，不输出签名、批次名、Cookie 或 Authorization。可用 `NGINX_ACCESS_LOG=/path/access.log` 指定日志；`--log-only` 只分析日志，不探测服务。

`verify.sh quick` 执行 Shell 语法、ruff、聚焦后端/脚本回归、文档一致性和前端生产构建。`verify.sh full` 追加完整后端、Alembic、基础设施、Compose、六个 Stage、隔离 MySQL 迁移和 Playwright；DXF→Excel、Steel DXF Classifier 与 Steel DXF Split 源码随仓库分发，因此其内置测试是必过门禁。`--allow-blocked` 仅把明确依赖 sudo、Windows/ODA 或外部 Stage 环境的可选门禁记为 blocked；代码、测试、文档和构建失败仍返回非零。也可使用 `make verify-quick` 和 `make verify-full`。

目录迁移前后必须运行 `make architecture-check`。它比较 `runtime-contract.json` 与当前 FastAPI、ORM、Celery、React router、Compose 和 Alembic，锁定 14 个任务名及 13 条 `pattern -> queue` 路由，并验证 `module-catalog.json` 对 46 张表、197 个 HTTP operation 与 14 个任务名的唯一归属。只有有意修改外部契约并同步兼容性说明时才可执行快照脚本的 `--write`；普通重构不得用重写快照消除失败。

## 数据库与容器

| 脚本 | 常用命令 | 说明 |
|---|---|---|
| `db.sh` | `start`、`check`、`status`、`migrate`、`migration-test`、`backup`、`restore`、`reap-storage` | `migration-test` 和部分系统操作可能需要 sudo；先在非生产 schema 验证。 |
| `docker.sh` | `check`、`up`、`up-workers`、`status`、`smoke`、`verify-storage`、`logs`、`backup`、`restore`、`down` | Compose 与本地 worker 不应同时消费同一 CAD 队列。 |
| `storage/reap.py` | 存储保留期回收实现 | 通常经 `bash scripts/db.sh reap-storage --dry-run` 调用，不直接猜测删除对象。 |
| `storage/verify_transactions.py` | 存储事务验证 | 用于隔离验证，不替代真实对象恢复演练。 |

`bash scripts/docker.sh up-workers` 每次都会从当前代码构建镜像并强制重建全部
容器，动态读取 Compose 完整服务清单，并在 180 秒内等待全部服务运行且健康；随后执行 Nginx 与后端
readiness smoke。服务退出、重启、不健康或超时会输出受影响服务的状态与最近
80 行日志并返回非零。`docker.sh down` 会包含 workers profile，避免残留转换
容器。`bash scripts/start-all.sh` 启动前会停止现有本地服务和本项目 Compose
容器，同步锁文件限定的后端依赖，并无条件重建前端；成功摘要前执行
`scripts/status.sh`，只有本地 MySQL、全部受管 worker、FastAPI、最新前端构建和
Nginx API/SPA 探针全部通过才返回零。

`bash scripts/docker.sh verify-storage` 只在 `backend-api` 运行且健康时执行。它在
容器内创建本次唯一的 Excel 与 DXF 探针，经应用 Files 路径写入 MinIO、登记
MySQL、核对预览与传输流水，最后软删除登记并物理移除本次探针对象。任一环节失败
返回非零；命令不打印数据库 DSN、对象存储密钥或管理员密码，也不会扫描或删除既有
业务对象。部署、升级和存储恢复后应执行一次。
保留数据库中的超管密码若已不同于首次种子值，可在 `.env.docker` 同时设置
`VERIFY_ADMIN_USERNAME` 与 `VERIFY_ADMIN_PASSWORD`，脚本只把它们注入本次探针，
不会重置账号；只设置其中一项会直接拒绝执行。

## Windows 与 CAD worker

| 脚本 | 用途 |
|---|---|
| `windows/forward_to_win11.sh` | 管理 Win11 到本地 `:8080` 的 SSH remote-forward；`status` 未运行返回 3。 |
| `run-cad-worker.sh` | 为本地 ODA worker 管理独立 Xvfb、DISPLAY、PID 和退出清理。 |
| `run-worker.sh` | 容器 worker 启动前有界等待 MySQL，就绪后把原 worker 保持为 PID 1。 |
| `cad/benchmark_conversion.py` | 对真实样本测量双向转换吞吐，不作为日常启动脚本。 |

出现客户端 405 时先运行 `status.sh`；若提示运行代码过期，重新运行
`start-all.sh` 即会替换旧实例。ZIP 409 先看 `doctor.sh` 的 request ID，再在弹窗重新预检格式。文件夹上传曾因浏览器并发 8 超过默认 API 连接池总容量 4 而产生 QueuePool 500；当前前端把同一时刻的文件上传限制为 4。

前端弹窗只向工人展示经过过滤的中文原因、建议动作和请求编号，错误码仅供页面内部选择恢复动作；422 会标明翻译后的业务字段，文件夹导入会列出失败文件样本。若生产页面仍只显示 HTTP 4xx，先用 `status.sh` 检查前端 dist 是否过期，再运行 `start-all.sh` 更新全部运行实例。后台日志、Traceback、数据库驱动和本机路径不得复制到工人界面。
