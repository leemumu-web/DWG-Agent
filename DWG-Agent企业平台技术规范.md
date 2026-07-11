# DWG-Agent 企业平台技术规范

**版本：2.0**
**状态：与 `codex` 分支当前实现同步**
**适用范围：Nginx、React、FastAPI、MySQL、Celery、MinIO、本地存储和 CAD/Excel 处理阶段**

## 1. 目标与边界

平台接收 DWG、DXF、XLS/XLSX 文件，异步执行转换或清单计算，持久化任务步骤与结果，并提供项目权限、复核、审计和可靠下载闭环。

当前交付包含：

- 用户、角色、权限、项目成员和审计。
- 文件、批次、图纸版本、任务、结果和复核。
- DWG -> DXF、DXF -> DWG、DXF -> Excel、Excel Final。
- 本地 FS 与 MinIO。
- MySQL-backed Celery 和 MySQL-backed SSE。

当前不包含：

- 可用的 Agent 推理任务体（`AGENT_ENABLED=false`）。
- 可用的 Windows CAD worker（`CAD_WORKER_ENABLED=false`）。
- 多节点高吞吐消息中间件；如规模超过少量 worker，应单独评估 RabbitMQ，而不是恢复 Redis 作为业务状态源。

## 2. 架构原则

1. **MySQL 是唯一业务事实源。** 认证吊销、密码变更、Agent memory、Job、JobStep 和进度事件均落 MySQL。
2. **Redis 不在运行时架构中。** 不存在 Redis 客户端、缓存 fallback、Pub/Sub 或 Redis broker。
3. **对象与元数据分离。** MySQL 保存对象元数据和 SHA-256；Local/MinIO 保存字节。
4. **事务边界可补偿。** 对象写入成功但数据库回滚时删除待提交对象；任务提交失败时条件更新作业失败状态。
5. **长任务跨 Celery 边界。** FastAPI 只校验、持久化和投递；worker 负责处理。
6. **接口和文档同源。** API 路由参考由 OpenAPI 自动生成，中英文同步。

## 3. 物理拓扑

```text
Browser
  -> Nginx
     -> React static assets
     -> /api/v1, /health -> FastAPI
FastAPI
  -> MySQL (business + broker + results)
  -> LocalStorage or MinIO
Celery workers
  -> MySQL broker/result
  -> MySQL business state
  -> LocalStorage or MinIO
  -> Stage child process / ODA
```

端口：

- 本地 API：`127.0.0.1:8010`。
- 本地 Nginx：`:8080`。
- 本地 Vite：`:5173`，占用时可使用 `:5174`。
- Compose 内部 FastAPI：`:8000`。
- Compose 内部 MySQL：`:3306`；MinIO：`:9000`。

## 4. 服务职责

| 组件 | 职责 | 不负责 |
|---|---|---|
| Nginx | TLS/入口、SPA、API 代理、SSE 代理、错误映射 | 业务鉴权 |
| React | 工作流 UI、重试、签名下载、sessionStorage token | 权限最终裁决 |
| FastAPI | 校验、RBAC、事务、任务投递、查询、SSE | 长耗时转换 |
| MySQL | 业务事实、token blacklist、memory、broker/result | 大文件字节 |
| Celery | 队列消费、任务状态机、恢复 | 业务数据最终展示缓存 |
| MinIO/Local | 文件字节、派生结果 | 访问控制元数据 |

## 5. 数据库与连接

- 运行时仅接受 MySQL DSN；SQLite 仅用于进程内 pytest double。
- FastAPI 连接池参数由 `DB_POOL_SIZE`、`DB_POOL_MAX_OVERFLOW`、`DB_POOL_TIMEOUT_SECONDS` 和 `DB_POOL_RECYCLE_SECONDS` 控制。
- Celery broker：`sqla+mysql+pymysql://...`。
- Celery result backend：`db+mysql+pymysql://...`。
- 两者从有效 MySQL DSN 派生，不允许重复配置出不同数据库。
- Celery engine 使用小连接池、`pool_pre_ping`、`pool_recycle` 和 `READ COMMITTED`。
- `kombu_message` 必须有 `(queue_id, timestamp, id, visible)` 索引，避免按全局 timestamp 跨队列锁行。
- worker 在 consumer 建立前声明 bootstrap queue 并确保索引存在。

权威迁移 head：`a74c2e9f1d30`。主要新增：

- `token_blacklist`、`agent_memory`、`jobs.progress_data`。
- Excel Final batches、parts、components。
- `jobs.attempt` 与 `job_steps.attempt`。

## 6. 任务状态机与 attempt

```text
queued -> running -> succeeded
                  -> failed
queued/running    -> cancelled
failed/cancelled  -> retry -> queued (attempt + 1)
```

规则：

- Celery 消息必须携带 `(job_id, attempt)`；升级前单参数消息默认 attempt 1。
- worker 只能用 `WHERE id=:id AND status='queued' AND attempt=:attempt` 原子领取，旧消息不得领取重试世代。
- 进度、完成、失败、取消均带 status 和 attempt 条件。
- dispatch compensation 同样带 queued+attempt 条件，worker 已领取时不得反向覆盖。
- `job_steps` 每行保存 attempt；查询可筛选 attempt。
- SSE 当前态只包含当前 attempt 的 steps；完整历史通过 steps 查询获取。
- worker 启动扫描长期无更新的 running job，条件失败为 `CELERY_WORKER_LOST`。
- Excel Final 的临时 batch 仅在条件状态更新成功后清理。

## 7. Celery 队列

| 队列 | 任务 |
|---|---|
| `report` | 框架 smoke/stub 结果 |
| `dxf` | DWG -> DXF |
| `dxf2dwg` | DXF -> DWG |
| `dxf2excel` | DXF batch -> Excel |
| `excel_final` | Excel -> 企业零件清单 |
| `agent` | 预留 |
| `cad` | 预留 |

SQLAlchemy transport 不支持 fanout remote control；事件和 `inspect` 不作为健康检查。worker 健康条件是：PID 1 为 Celery 且 `/tmp/dwg-celery-ready` 已由 `worker_ready` 信号写入。

## 8. 存储

`STORAGE_BACKEND` 只能为 `local` 或 `minio`。

主要 bucket：

- `dwg-original`、`dwg-derived`
- `dxf-original`、`dxf-derived`
- `dwg-reports`、`dwg-temp`

上传要求：

- 扩展名白名单 `.dwg/.dxf/.zip/.xls/.xlsx`。
- DWG header 为 AC1012-AC1032，最小 1024 字节。
- 流式计算 SHA-256/MD5，限制单文件、ZIP entry 数和解压总量。
- MinIO 不可达时写入返回 503 `STORAGE_WRITE_FAILED`。

下载要求：

1. 调用 `/files/{id}/download-url` 进行权限检查并获得 300 秒签名。
2. 调用 `/files/{id}/download` 时同时校验 Bearer token、资源权限、expires 和 HMAC。
3. 前端遇到网络、403、408、429 或 5xx，最多再试一次，并重新获取签名。
4. 下载字节 SHA-256 必须等于 `files.sha256`。

## 9. 认证与权限

- 密码使用 Argon2id。
- access token 默认 30 分钟；refresh cookie 默认 14 天。
- access/refresh token 类型不可互换。
- token `jti` 写入 MySQL `token_blacklist`；密码变更时间也由 MySQL 检查。
- 前端 access token 和用户信息使用 `sessionStorage`，不跨浏览器 tab 持久化。
- SSE 使用短期 HttpOnly `dwg_sse_token` cookie，禁止 URL token。

全局角色：`super_admin/admin/engineer/reviewer/operator/viewer/auditor`。项目资源还必须通过项目成员角色检查。文件读取允许管理员、上传者或关联项目成员；列表权限必须在 SQL 中过滤，禁止 N+1 逐文件鉴权。

Result 详情、下载 URL 和复核继承父 Job 权限；无项目 Job 仅管理员和创建者可访问。Agent run 启用后：管理员、创建者或关联项目成员可读；无项目 run 仅管理员和创建者可读。

## 10. API 约定

- 版本前缀：`/api/v1`。
- 成功：`{data, meta}`。
- 分页：`{data, pagination, meta}`，最大页大小 200。
- 错误：`{error: {code, message, details}, meta}`。
- 列表必须使用 SQL `COUNT + LIMIT/OFFSET` 和稳定 ID tie-breaker。
- 详细端点表由 `cd backend && uv run python ../scripts/generate_api_docs.py` 生成，见 `docs/api.md` 和 `docs/zh/api.md`。

## 11. SSE

`GET /jobs/{id}/events`：

- 首先执行与普通 Job GET 相同的权限检查。
- 从 MySQL 读取 job 和当前 attempt 的 steps，生成 snapshot/progress/terminal 事件。
- 断线重连后重新发送 MySQL 当前权威快照；当前不提供基于 event ID 的历史回放。
- Nginx 对 SSE 禁用 buffering 并延长读取超时。

## 12. Excel Final

- 输入内容必须是 Tekla 制表符/空白文本导出，或包含钢构清单必需列的初始表；不能仅凭 `.xls/.xlsx` 扩展名判定业务可处理性。
- legacy 二进制 `.xls` 使用显式锁定的 `xlrd` 读取；文本探测失败必须继续进入真实 Excel fallback。
- Stage 工程不作为 backend 包直接导入，而由 `app.integrations.excel_final_runner` 隔离子进程执行。
- 子进程 stdout 只返回结构化 JSON；完整 stderr 仅写 server log。
- 客户端只收到稳定错误码与安全消息。
- worker 使用同一 SQLAlchemy session 写失败 step 与 job 失败状态，避免二次 session 锁冲突。
- 成功时导入 batch/part/component，写结果对象并登记 `AnalysisResult`。
- `hardware_handbook` 使用只读凭据；Compose 初始化为应用用户授予 `SELECT`。

## 13. 健康与恢复

- `/health`：进程 liveness，不探测外部组件。
- `/health/ready`：同时探测 MySQL 和配置的存储，任一失败返回 503，并分别报告组件状态。
- MinIO 恢复后不得要求重启 API；持久卷中的旧对象必须仍可读。
- Celery 启动清理过期 result rows、已消费 broker rows，并恢复 stale running jobs。
- 本地脚本按命令行身份发现 worker，pidfile 丢失不能导致重复消费。

## 14. Compose

核心服务：`nginx/backend-api/mysql/minio/worker-report`。`workers` profile 增加其余管线 worker。

- backend 仅在 `internal` 网络；Nginx 跨 public/internal。
- MySQL/MinIO 使用命名卷。
- MinIO 固定到验证过的 registry digest。
- backend 镜像以非 root `appuser` 运行。
- `.dockerignore` 排除 Stage 样本、虚拟环境、本地存储和 third-party 预览应用；实测 context 约 89MB。

## 15. 测试与验收

最小合并门槛：

```bash
cd backend
uv run ruff check app tests ../tests/run_full_verify.py
uv run pytest -q
uv run alembic check

cd ../frontend
npm run build
npx playwright test

cd ..
cd Stages/excel_final && uv run pytest -q multi_split/tests && cd ../..
bash scripts/db.sh migration-test
bash infra/verify.sh
docker compose config --quiet
```

高风险变更必须增加：

- 状态竞态/attempt 回归。
- 权限横向越权回归。
- 存储失败补偿和恢复回归。
- 浏览器下载重签名与哈希回归。
- 空 MySQL schema + Compose 冷启动验证。

## 16. 文档同步

- `docs/` 与 `docs/zh/` 文件结构一一对应。
- 命令、端点、环境变量和状态名在两种语言中完全一致。
- 修改路由后必须运行 `cd backend && uv run python ../scripts/generate_api_docs.py`。
- 提交前必须运行 `make docs-check`，验证双语结构、技术 token、生成 API、本地链接和端口约定。
- 历史方案不得继续描述为当前运行架构；Redis 只能出现在迁移历史或“已移除”说明中。
