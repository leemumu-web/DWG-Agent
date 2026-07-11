# 全栈工作流验证

> **范围：** Nginx、FastAPI、MySQL、Celery SQL transport、MinIO、前端重试与签名下载
> **最近验证：** 2026-07-11
> **英文镜像：** [`../workflow-verification.md`](../workflow-verification.md)

## 1. 验收边界

验证必须覆盖接近生产的真实路径，不能只依赖 mocked API 测试：

```text
浏览器 -> Nginx :8080 -> FastAPI :8010 本地 / :8000 容器
                           |-> MySQL 权威状态
                           |-> MySQL Celery broker 和 result backend
                           |-> MinIO 对象
Celery worker <- MySQL 队列 -> stage 进程 -> MySQL 状态 + MinIO 结果
```

Redis/Valkey 不属于该拓扑。任务进度、token 吊销、SSE 快照、broker message 和 task result 均为持久化 MySQL 数据。

## 2. 可重复执行命令

先执行静态检查和隔离测试：

```bash
cd backend
uv run ruff check app tests
uv run pytest -q
uv run python ../scripts/check_docs.py
cd ..

cd Stages/excel_final
uv run pytest -q multi_split/tests
cd ../..

bash scripts/db.sh migration-test
bash infra/verify.sh
docker compose config --quiet

cd frontend
npm run build
npx playwright test
```

本地栈已启动时，通过 Nginx 执行非破坏性冒烟验证：

```bash
DWG_VERIFY_USERNAME=admin \
DWG_VERIFY_PASSWORD='<configured-password>' \
python tests/run_full_verify.py
```

验证器检查 liveness、readiness、OpenAPI 生成、认证、文件/任务精确分页读取和受管进程拓扑。它不会重置数据库，也不会创建业务记录。

## 3. 必须覆盖的端到端场景

| 场景 | 预期证据 |
|---|---|
| Compose 冷启动 | 空 volume 迁移到 Alembic head；backend 和 worker 进入 healthy |
| FastAPI -> MySQL | 认证请求写入并读取权威数据行 |
| FastAPI -> broker -> Celery | 提交任务离开 `queued`，记录 attempt 和 steps，并进入终态 |
| Celery -> MinIO | 成功输出同时存在 `files` 行和摘要一致的对象 |
| 签名下载 | 前端请求新 URL、下载字节，并在 URL 过期/失败后重新签名 |
| 重试 | 失败/取消任务创建下一 attempt，不覆盖此前 steps |
| SSE | 浏览器收到源自 MySQL 的当前 attempt 快照；凭据由 HttpOnly SSE cookie 携带 |
| 结果隔离 | 无项目结果的详情、下载 URL 和复核拒绝创建者/管理员之外的用户 |
| 存储中断 | `/health` 保持 200；`/health/ready` 返回 503，database 为 `ok`、storage 为 `error` |
| 存储恢复 | 原对象仍可下载，SHA-256 不变 |
| Worker 重启 | pidfile 丢失时，受管脚本也不会重复创建同名 worker |
| 旧消息投递 | 升级前单参数消息不能领取 attempt 2；`(job_id, 2)` 可以执行 |

## 4. 已验证证据

2026-07-11 验收使用全新 Compose volume 和 digest 固定的 MinIO 镜像：

- 空 MySQL schema 的 Alembic 迁移到达 `a74c2e9f1d30`。
- broker 创建 `kombu_message(queue_id, timestamp, id, visible)`，查询计划选择该复合索引。
- report 任务完整通过 API -> MySQL broker -> Celery -> MySQL 状态 -> MinIO；下载 SHA-256 与存储对象一致。
- 停止 MinIO 后 readiness 返回 503，database 仍为 `ok`；重启后对象和摘要保持不变。
- 独立 MinIO 持久化验证另行跑通 Excel Final 任务，并在重启后取回完全一致的结果字节。
- Excel Final 自身 profile/VBA parity 套件通过 254 项；legacy 二进制 `.xls` 包含 `xlrd`，文本探测失败会进入真实 Excel fallback。
- 浏览器测试覆盖真实上传、任务轮询、失败任务递增 attempt 重试、签名 URL 刷新和结果下载。
- 真实 MySQL/report worker 探针中，单参数旧消息到达后 attempt 2 任务保持 queued，仅 `(job_id, 2)` 消息使其完成。

这些记录是本次运行的证据，不代替未来代码变更后的重新执行。

## 5. 故障定位顺序

1. 检查 `bash scripts/status.sh` 和 `/health/ready`，再排查业务逻辑。
2. 在 `/tmp/dwg-agent-backend.log` 与 `/tmp/dwg-agent-worker-*.log` 查找第一处错误。
3. 确认 `alembic current` 等于文档 head，且应用 MySQL 用户能够连接。
4. 确认每个队列只有一个受管 worker 节点，Compose worker 内存在 `/tmp/dwg-celery-ready`。
5. 先比较 `files.sha256`、下载字节和 MinIO 对象，再判断前端是否有错。
6. 用浏览器网络记录区分签名 URL 过期和对象拉取失败。

不得通过启用内存 fallback 让验证变绿，否则会掩盖权威调用路径的丢失。
