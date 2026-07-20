# 运维脚本

本目录是本地、Compose、数据库、Windows 转发和质量门禁的操作入口。所有命令应从仓库根目录执行；脚本不会把“进程存在”直接解释为转换管线可用。

## 常用闭环

```bash
# 启动并按需构建前端
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
| `start-all.sh` | MySQL、五个 worker、FastAPI、前端 dist、Nginx | 已占用 8010 时不擅自重启；源码晚于进程会警告。 |
| `start-all.sh --restart-backend` | 优雅重载本项目 FastAPI | 只识别 cwd 为本仓库 `backend/` 的 Uvicorn；未知进程占端口时拒绝操作。 |
| `start-all.sh --rebuild` | 强制重建前端 | 普通启动也会在 `src/` 或依赖清单晚于 `dist/index.html` 时重建。 |
| `start-dev.sh` | Uvicorn reload + Vite HMR | 开发入口，不经过本地 Nginx 静态托管。 |
| `stop-all.sh` | 停止受管 Nginx、前端、后端和 worker | 不强杀未知 8010 进程；MySQL 默认保留。 |
| `status.sh` | 无副作用状态检查 | 检查 MySQL、worker、后端、新旧代码、前端 dist 和 Nginx。 |

## 诊断与验证

`doctor.sh` 默认读取 `infra/nginx/logs/access.log` 最近 60 分钟。它按状态、方法和去查询串路径聚合，只输出有限 request ID，不输出签名、批次名、Cookie 或 Authorization。可用 `NGINX_ACCESS_LOG=/path/access.log` 指定日志；`--log-only` 只分析日志，不探测服务。

`verify.sh quick` 执行 Shell 语法、ruff、聚焦后端/脚本回归、文档一致性和前端生产构建。`verify.sh full` 追加完整后端、Alembic、基础设施、Compose、四个 Stage、隔离 MySQL 迁移和 Playwright；DXF→Excel 源码随仓库分发，因此其内置测试是必过门禁。`--allow-blocked` 仅把明确依赖 sudo、Windows/ODA 或外部 Stage 环境的可选门禁记为 blocked；代码、测试、文档和构建失败仍返回非零。也可使用 `make verify-quick` 和 `make verify-full`。

目录迁移前后必须运行 `make architecture-check`。它比较 `runtime-contract.json` 与当前 FastAPI、ORM、Celery、React router、Compose 和 Alembic，并验证 `module-catalog.json` 对 36 张表、135 个 HTTP operation 与 11 个任务名的唯一归属。只有有意修改外部契约并同步兼容性说明时才可执行快照脚本的 `--write`；普通重构不得用重写快照消除失败。

## 数据库与容器

| 脚本 | 常用命令 | 说明 |
|---|---|---|
| `db.sh` | `start`、`check`、`status`、`migrate`、`migration-test`、`backup`、`restore`、`reap-storage` | `migration-test` 和部分系统操作可能需要 sudo；先在非生产 schema 验证。 |
| `docker.sh` | `check`、`up`、`up-workers`、`status`、`smoke`、`logs`、`backup`、`restore`、`down` | Compose 与本地 worker 不应同时消费同一 CAD 队列。 |
| `reap_storage.py` | 存储保留期回收实现 | 通常经 `bash scripts/db.sh reap-storage --dry-run` 调用，不直接猜测删除对象。 |
| `verify_storage_transactions.py` | 存储事务验证 | 用于隔离验证，不替代真实对象恢复演练。 |

## Windows 与 CAD worker

| 脚本 | 用途 |
|---|---|
| `forward-to-win11.sh` | 管理 Win11 到本地 `:8080` 的 SSH remote-forward；`status` 未运行返回 3。 |
| `run-cad-worker.sh` | 为本地 ODA worker 管理独立 Xvfb、DISPLAY、PID 和退出清理。 |
| `benchmark_cad_conversion.py` | 对真实样本测量双向转换吞吐，不作为日常启动脚本。 |

出现客户端 405 时先运行 `status.sh`；若提示运行代码过期，使用 `start-all.sh --restart-backend`。ZIP 409 先看 `doctor.sh` 的 request ID，再在弹窗重新预检格式。文件夹上传曾因浏览器并发 8 超过默认 API 连接池总容量 4 而产生 QueuePool 500；当前前端把同一时刻的文件上传限制为 4。

前端弹窗会展示后端错误原因、错误码和 request ID，422 还会标明具体字段；文件夹导入会列出失败文件样本。若生产页面仍只显示 HTTP 4xx，先用 `status.sh` 检查前端 dist 是否过期，再用 `start-all.sh --rebuild` 更新构建产物。
