# 架构

> 英文对应文档：[../architecture.md](../architecture.md)

## 系统上下文

DWG-Agent 是面向内部 CAD/Excel 接入、异步处理、结果复核和审计的操作平台。Nginx 提供 React SPA 并代理请求；FastAPI 负责校验和授权；MySQL 是权威状态；Celery 执行长任务；Local FS 或 MinIO 保存字节。

```text
Browser
  -> Nginx :8080 本地 / :80 Compose 宿主
     -> React SPA
     -> FastAPI :8010 本地 / backend-api:8000 Compose
        -> MySQL 业务 + Celery runtime 表
        -> LocalStorage 或 MinIO
Celery workers
  -> MySQL + storage + processing Stages
```

运行时没有 Redis/Valkey。token 吊销、密码变更检查、Agent memory、Job 进度、SSE 快照、broker message 和 task result 均使用 MySQL。

## 部署现实

| 属性 | 本地 | Compose |
|---|---|---|
| 用户入口 | Vite `5173` 或 Nginx `8080` | Nginx 宿主 `80` |
| API | `127.0.0.1:8010` | 内部 `backend-api:8000` |
| 存储 | 默认 local，MinIO 可选 | 内部 MinIO |
| TLS | 无 | 无；`443:8443` 映射没有 listener/certificate |
| 运行时文档 | development/debug 时可用 | production settings 下关闭 |

Compose 在网络隔离、非 root backend、健康依赖和持久卷方面接近生产形态，但不是完整生产平台。

## 归属边界

| 层 | 负责 | 禁止负责 |
|---|---|---|
| Nginx | 入口、SPA fallback、代理限制、SSE 传输设置 | 业务授权或当前 TLS 声明 |
| 前端 | 工作流 UX、有限重试、query cache、下载编排 | 最终权限裁决或持久状态 |
| API route/dependency | HTTP 校验、auth context、envelope | 重复 domain transaction |
| Service | transaction、permission、状态转换、storage compensation | UI 状态或 broker-specific 业务事实 |
| Worker/task | attempt 领取和 Stage 编排 | 无条件状态写入或内存 fallback |
| MySQL | 业务事实、audit row、Celery broker/result | 对象字节或高吞吐 broker 承诺 |
| Storage | 由 bucket/key 标识的不透明字节 | 用户/项目访问规则 |
| Stage | 确定性 CAD/Excel 转换 | 平台 auth、Job ownership 或 public error |

## 同步请求路径

```text
Browser -> Nginx -> FastAPI dependency auth -> service -> MySQL -> envelope response
```

列表端点在 SQL 中执行访问过滤、`COUNT(*)`、稳定排序和 `LIMIT/OFFSET`。UI guard 和 Nginx location 不替代 API 检查。生产未处理错误只在服务端记录，并返回通用 envelope。

## 异步请求路径

```text
POST
  -> feature flag + input + access validation
  -> INSERT Job(queued, attempt=N) + COMMIT
  -> 向 MySQL SQL transport 发布 (job_id, attempt)
  -> worker conditional claim
  -> attempt-scoped JobSteps 和 progress
  -> source bytes -> Stage -> result bytes
  -> files + AnalysisResult + optional domain rows
  -> conditional terminal update
```

投递补偿只更新仍 queued 的 attempt。重试递增 attempt；旧 message/worker 不能领取或更新新 attempt。worker 启动恢复处理长期 stale running Job，但不是持续 heartbeat。

## SSE 路径

原生 EventSource 携带短期 HttpOnly `dwg_sse_token` cookie。FastAPI 在 streaming 前认证并授权 Job，轮询 MySQL，发送当前 attempt 的 snapshot/progress/terminal event。重连以新的权威快照开始；不存在 event-ID replay 或 Pub/Sub 保证。

Nginx 关闭 SSE buffering/cache 并延长 read/send timeout。前端在短暂 CONNECTING 时交给浏览器重连，在 terminal event 或 hard close 时关闭。

## 下载路径

```text
Bearer request -> permission check -> 300-second HMAC path
Bearer download -> permission + expiry + signature check -> storage stream
```

前端单文件最多尝试两次，每次重新签名。只重试网络、403、408、429 和 5xx。ZIP 下载使用认证 POST stream，不共享该重签名循环。数据库 SHA-256 是完整性依据。

## 存储一致性

数据库 commit 前写入的对象登记在 SQLAlchemy session 上。rollback best-effort 删除；commit 清除补偿列表。只有对象和 metadata 均持久化后才暴露成功输出。当前没有后台对象/数据库 reconciler，因此运维必须发现缺失和孤儿对象。

## MySQL 与 Celery

broker 为 `sqla+mysql+pymysql://...`，result backend 为 `db+mysql+pymysql://...`，都从有效应用 MySQL DSN 派生。Celery engine 使用 `READ COMMITTED`、有界 pool、pre-ping、LIFO 和 recycle。`kombu_message(queue_id, timestamp, id, visible)` 按队列缩小 message claim。

SQL transport 没有 fanout remote control。worker 健康使用 Celery PID 加 `worker_ready` marker，不使用 `inspect`。result row 在 24 小时后过期；业务 Job 历史没有自动保留策略。

## 处理边界

| 管线 | 队列 | 实现 | 交付限制 |
|---|---|---|---|
| framework smoke | `report` | 可执行 stub result | 不是 report Agent |
| DWG -> DXF | `dxf` | ODA service/task | flag 关闭；外部 ODA/runtime/样本兼容性 |
| DXF -> DWG | `dxf2dwg` | ODA service/task | flag 关闭；外部 ODA/runtime/样本兼容性 |
| DXF -> Excel | `dxf2excel` | service/task 与本地文件 | flag 关闭；父仓库 gitlink 损坏阻断 clean clone |
| Excel Final | `excel_final` | 隔离 Stage + 关系化导入 | flag 关闭；需要内容 schema 和手册库 |
| Agent | `agent` | 只有 API/model | task module 是空占位 |
| Windows CAD | `cad` | 配置占位 | 无 task、worker、service 或 Compose node |

步骤名、格式、输出和启用检查见[处理管线](processing-pipelines.md)。

## 安全模型

全局角色为 `super_admin/admin/engineer/reviewer/operator/viewer/auditor`；项目成员增加 owner/engineer/reviewer/viewer 范围。管理员访问是明确规则。文件读取要求管理员、上传者或关联活跃项目成员。结果和复核继承 Job 边界；无项目 Job 仅管理员或创建者可读。

access token 位于 `sessionStorage`，因此同源 XSS 仍是威胁。refresh/SSE token 是 HttpOnly、SameSite=Lax cookie。公网部署需要真实 TLS 和 Secure cookie，而当前 Compose 尚未交付。

## 健康与可观测性

- `/health` 仅 liveness。
- `/health/ready` 分别报告 MySQL 和已配置 storage，任一失败返回 503。
- 通用 worker 健康只证明 broker 连接，不证明 pipeline dependency。
- 本地日志使用 `/tmp`；Compose 使用 container stdout/stderr。
- metrics、distributed tracing、central logging、alerting、SLO 和自动保留均未实现。

## 架构约束

- MySQL/storage 失败时禁止增加进程内正确性 fallback。
- 没有显式迁移设计时，禁止让 broker 凭据脱离权威 MySQL DSN。
- Agent/CAD task 仍占位时禁止启用对应 flag。
- 修复 `Stages/dxf2excel` 归属前，禁止声称 clean-clone/Docker 可复现。
- Nginx 有已测试 TLS listener 和证书生命周期前，禁止声称 HTTPS。
- worker 规模超过有界 SQL transport 时评估 RabbitMQ，同时保持 MySQL 为业务事实。
