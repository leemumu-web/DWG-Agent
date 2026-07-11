# 架构

> 英文对应文档：[../architecture.md](../architecture.md)

## 系统上下文

DWG-Agent 是面向企业操作人员的 CAD 处理平台。Nginx 是部署时唯一公网入口，React 承载交互，FastAPI 负责校验和授权，MySQL 是权威状态源，Celery 执行长任务，Local FS 或 MinIO 保存文件字节。

```text
Browser
  -> Nginx :8080 本地 / :80,:443 Compose
     -> React SPA
     -> FastAPI :8010 本地 / :8000 容器
        -> MySQL 业务 schema
        -> MySQL Celery broker/result 表
        -> LocalStorage 或 MinIO
Celery workers
  -> MySQL + 存储 + processing Stages
```

运行时不依赖 Redis/Valkey。token 吊销、密码变更检查、Agent memory、任务进度、SSE 快照、broker message 和 task result 均使用 MySQL。

## 边界

| 层 | 负责 | 不负责 |
|---|---|---|
| Nginx | 入口、TLS、SPA fallback、SSE 代理 | 业务授权 |
| 前端 | 交互、重试和下载编排 | 最终权限裁决 |
| API | 校验、RBAC、事务、投递、查询 | 长耗时 CAD/Excel 执行 |
| Service | 状态转换和业务不变量 | HTTP 展示 |
| Worker | 任务领取和管线执行 | 无条件状态写入 |
| MySQL | 业务事实、broker/result、审计 | 大文件字节 |
| Storage | 不透明对象字节 | 用户/项目授权 |

## 调用路径

### 普通 API

```text
Browser -> Nginx -> FastAPI dependency auth -> service -> MySQL -> envelope response
```

列表端点在 SQL 内执行 `COUNT(*)` 和 `LIMIT/OFFSET`，稳定排序追加 ID。权限过滤属于 SQL 查询，文件列表不会逐行额外查询授权。

### 异步任务

```text
POST request
  -> 校验项目/文件权限
  -> INSERT jobs(status=queued, attempt=1)
  -> COMMIT
  -> 向 MySQL SQLAlchemy transport 发布 (job_id, attempt)
  -> worker 原子领取 queued+expected attempt
  -> 写入 attempt-scoped JobSteps
  -> 写对象 + DB 元数据/结果
  -> 条件完成同一 attempt
```

投递补偿只更新仍为 queued 的同一 attempt。worker 已领取后 API 不得覆盖。重试递增 `jobs.attempt`，旧消息和旧 worker 都无法领取或更新新世代。升级前单参数 Celery 消息默认 attempt 1。

### SSE

EventSource 使用短期 `dwg_sse_token` cookie。FastAPI 检查 Job 权限、轮询 MySQL，并发送仅含当前 attempt steps 的 snapshot 和 terminal event。重连首先发送新的权威快照；事件流不承诺按 event ID 回放。URL 不含 access token，也不需要 Pub/Sub。

### 下载

客户端先在正常鉴权后获取签名下载 URL，再携带 Bearer token 下载。重试必须获得新签名。同一源文件存在多次成功转换时，文件和 ZIP 解析确定性选择最新 Job 与最新 result 行。本地存储返回文件，MinIO 流式返回对象，数据库 SHA-256 是完整性依据。

## 存储一致性

数据库 commit 前写入的对象登记在 SQLAlchemy session 上。rollback 删除待提交对象；commit 清除补偿表。worker 只有在对象和数据库结果都成功后才暴露结果。

## MySQL Celery

broker 为 `sqla+mysql+pymysql://...`，result backend 为 `db+mysql+pymysql://...`。连接池受限并使用 `READ COMMITTED`。消息表包含 `(queue_id, timestamp, id, visible)`，避免消费者扫描或锁住其他队列。

SQL transport 不支持 fanout remote control。健康检查使用 `worker_ready` marker 加 PID 1 检查。本地启动器还按命令行发现受管 worker，pidfile 丢失不会重复启动。

## 处理管线

| 管线 | 队列 | 输出 |
|---|---|---|
| framework smoke | `report` | JSON 结果 |
| DWG -> DXF | `dxf` | DXF |
| DXF -> DWG | `dxf2dwg` | DWG |
| DXF -> Excel | `dxf2excel` | XLSX |
| Excel Final | `excel_final` | final XLSX + 关系型零件/构件 |

Excel Final 在子进程中运行 legacy Stage。有效内容为 Tekla 制表符/空白文本导出（通常命名为 `.xls`），或具备钢构清单必需 schema 的 Excel 初始表；只有扩展名匹配的任意工作簿并不保证可处理。legacy 二进制 `.xls` 由 `xlrd` 解析。该边界隔离 import，并阻止 child stderr 进入 API 错误。

## 安全模型

全局角色为 `super_admin/admin/engineer/reviewer/operator/viewer/auditor`。项目成员还有 owner/engineer/reviewer/viewer 范围。除明确的管理员访问外，全局角色不能代替资源校验。

文件可由管理员、上传者或关联活跃项目成员读取。结果详情、下载 URL 与复核继承 Job 权限；无项目 Job 仅管理员或创建者可访问。Agent run 启用后，仅管理员、创建者或关联项目成员可读。

## 健康检查

- `/health`：仅进程 liveness。
- `/health/ready`：独立探测 MySQL 和存储，任一失败返回 503。
- Worker 容器：ready marker 加 Celery PID。
- MinIO 恢复不要求重启 API，命名卷中的对象仍可读。

## 功能开关

`AGENT_ENABLED` 和 `CAD_WORKER_ENABLED` 默认关闭。转换开关为 `DXF_PIPELINE_ENABLED`、`DXF2DWG_PIPELINE_ENABLED`、`DXF2EXCEL_PIPELINE_ENABLED`、`EXCEL_FINAL_PIPELINE_ENABLED`。

## 端口

本地 API 固定为 `8010`。容器 `8000` 仅为内部部署细节。本地 Nginx 为 `8080 -> 8010`，Compose Nginx 代理到 `backend-api:8000`。
