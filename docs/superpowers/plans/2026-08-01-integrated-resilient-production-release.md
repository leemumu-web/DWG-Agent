# Integrated Resilient Production Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把北京时间、可靠命令/事务发件箱、可恢复生产输入和前端弱网/懒加载作为一个可回滚的服务器版本完成、验证并部署。

**Architecture:** 四个子计划按共同不变量串联：业务时间先统一，新表随后直接使用北京时间；发件箱先于上传会话和前端可靠命令；上传/前端只消费已经稳定的后端契约。生产只执行一次短维护窗，保留 MySQL、MinIO 和 app_var 卷，按 MySQL/MinIO → API → dispatcher → workers → Nginx 恢复。

**Tech Stack:** Python/FastAPI/SQLAlchemy/Alembic、MySQL/MinIO/Celery、React/TypeScript/Vite/Playwright、Docker Compose、Bash、Tailscale SSH。

---

## 子计划与依赖

1. `docs/superpowers/plans/2026-08-01-beijing-timezone-migration.md` — 完成当前已开始的时间实现、迁移和维护脚本。
2. `docs/superpowers/plans/2026-08-01-production-command-delivery-reliability.md` — 依赖业务时钟；产出 operation key、command receipt、outbox 和 dispatcher。
3. `docs/superpowers/plans/2026-08-01-resumable-production-input.md` — 依赖 outbox/命令可靠性与批次版本；产出文件级恢复协议。
4. `docs/superpowers/plans/2026-08-01-frontend-network-loading-resilience.md` — 依赖稳定 API；产出全按钮策略、弱网恢复和真实分包。

所有子计划必须各自产生可测试提交；不能把四个系统压成一个无法定位回归的大提交。

### Task 1: 收敛当前北京时间 RED 状态

**Files:**
- Current modified backend/frontend files shown by `git status --short`
- Modify: `backend/app/modules/operations/daily_archive/planning.py`
- Modify: remaining naive-as-UTC helpers listed by the timezone plan
- Restore unrelated formatter-only changes in `backend/app/modules/excel_processing/header_normalization.py` and `importers.py`

- [ ] **Step 1: 记录当前边界**

Run: `git status --short`

Run: `cd backend && uv run pytest -q tests/operations/test_daily_archive.py -k business`

Expected: daily archive 因 `_business_iso` 缺失而 FAIL；未跟踪数据目录保持不变。

- [ ] **Step 2: 完成当前最小实现**

```python
def _business_iso(value: datetime | None) -> str | None:
    return as_business_time(value).isoformat() if value is not None else None


def _day_window(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=BUSINESS_TIMEZONE)
    return start, start + timedelta(days=1)
```

把 planning 中 `_utc_iso` 调用改为 `_business_iso`；其余 `replace(tzinfo=UTC)` naive helper 全部改为 `as_business_time`，epoch/签名仍保持绝对秒数。

- [ ] **Step 3: 清除无关机械 diff**

使用 `apply_patch` 恢复 header normalization/importers 中与本任务无关的 import/空行变化；随后只对明确修改文件运行 formatter，不执行全应用自动修复。

- [ ] **Step 4: 运行时间 focused tests**

Run: `cd backend && uv run pytest -q tests/infrastructure/test_business_time.py tests/infrastructure/test_db_session.py tests/operations/test_daily_archive.py tests/security tests/workflows/test_workflow_retention.py`

Run: `cd frontend && npm run build`

Expected: PASS。

- [ ] **Step 5: 提交当前时间代码**

```bash
git add backend/app backend/tests frontend/src/shared/components/ui.tsx
git commit -m "feat: use Beijing business time"
```

### Task 2: 完成北京时间配置、迁移和维护工具

**Files:**
- Files in timezone plan Tasks 4–7

- [ ] **Step 1: 按 TDD 完成 16 服务时区配置**

Compose 渲染断言所有 16 个服务为 `Asia/Shanghai`，MySQL 为 `+08:00`，Celery `timezone=settings.business_timezone` 且 `enable_utc=True`。dispatcher 直接继承 app 环境。

- [ ] **Step 2: 完成历史 DATETIME 迁移**

实现 `a4c8e1f2b730`，down revision 为当前 `d1e7f3a9c520`；只转换迁移前已存在的 126 个业务 DATETIME 列，排除 3 个 Celery UTC 协议列。后续 `b6d2c8f4e910`、`c2f7a9d4e610` 新表从创建起写北京时间，不参与 +8。

- [ ] **Step 3: 完成一次维护窗工具**

维护脚本同时安装最终 16 服务 release，不允许先部署时间版再部署可靠性版。预检增加 pending/leased outbox、活动 upload session 和现有 multipart/transfer 检查。

- [ ] **Step 4: 运行迁移门禁并提交**

Run: `cd backend && uv run pytest -q tests/infrastructure/test_beijing_time_migration.py tests/infrastructure/test_compose.py tests/infrastructure/test_server_release.py`

Run: `bash scripts/db.sh migration-test`

Expected: PASS。

```bash
git add compose.yaml .env.docker.example backend/Dockerfile frontend/Dockerfile backend/migrations scripts/release backend/tests/infrastructure docs/reference
git commit -m "feat: prepare integrated Beijing migration"
```

### Task 3: 执行可靠命令与发件箱子计划

**Files:**
- Exact files listed in `2026-08-01-production-command-delivery-reliability.md`

- [ ] **Step 1: 完成 Tasks 1–3**

门禁：跨账号 operation key、同事务 dispatch、租约和发布后响应丢失测试全部 PASS。

- [ ] **Step 2: 完成 Tasks 4–7**

门禁：8 个直接 Job 发布入口归零、command receipt 重放、迁移、真实 MySQL 双账号/双 dispatcher 竞争和 16 服务 Compose PASS。

- [ ] **Step 3: 完成全按钮策略 Task 8**

Run: `rg -n 'useMutation' frontend/src/features`

Expected: feature 业务组件无直接 `useMutation`；所有 mutation 经 `useAppMutation` 声明五类策略，所有 Job 类按钮走 outbox。

- [ ] **Step 4: 记录子计划提交和门禁**

Run: `git log --oneline 6bc7ae3..HEAD`

Expected: 每个可靠性任务有独立提交，无未提交混杂。

### Task 4: 执行可恢复生产输入子计划

**Files:**
- Exact files listed in `2026-08-01-resumable-production-input.md`

- [ ] **Step 1: 完成后端会话、item 和 completion**

门禁：清单攻击/5000 边界、单文件成功重放、失败 attempt、整批原子完成、版本冲突和旧入口兼容 PASS。

- [ ] **Step 2: 完成前端三路恢复**

门禁：并发常量为 3、断线只重传失败文件、刷新后重选恢复、不同 manifest 不复用、页面内进度单调。

- [ ] **Step 3: 完成真实 MySQL completion 竞争**

Run: `cd backend && uv run pytest -q tests/workflows/test_input_upload_sessions_mysql.py`

Expected: 两个账号只有一个 session 完成，批次版本只增加一次。

### Task 5: 执行前端网络与加载子计划

**Files:**
- Exact files listed in `2026-08-01-frontend-network-loading-resilience.md`

- [ ] **Step 1: 完成查询/重连状态机**

门禁：GET 有界重试、mutation 全局 retry=false、可靠命令同键、offline/recovering/online 同步状态 PASS。

- [ ] **Step 2: 完成路由和阶段动态边界**

门禁：直接页面 import、局部 Suspense、未选择的重型阶段不请求、下一阶段只在允许时空闲预取。

- [ ] **Step 3: 完成构建预算**

Run: `cd frontend && npm run build`

Expected: Vite 无大于 500 kB 警告，manifest 预算 PASS，列表/详情/重型阶段为独立依赖。

### Task 6: 本地集成和故障注入

**Files:**
- Test suites and release scripts from all subplans

- [ ] **Step 1: 运行静态/架构门禁**

Run: `cd backend && uv run ruff check app tests`

Run: `cd frontend && npm run check:architecture && npm run build`

Expected: PASS。

- [ ] **Step 2: 运行后端全量**

Run: `cd backend && uv run pytest -q`

Expected: PASS；记录 passed/skipped，环境依赖测试未实际运行时明确列为未验证。

- [ ] **Step 3: 运行真实 MySQL 与迁移**

Run: `cd backend && uv run pytest -q tests/jobs/test_job_outbox_mysql.py tests/workflows/test_input_upload_sessions_mysql.py tests/excel_processing/test_excel_final_idempotency_mysql.py tests/remnant_inventory/test_inventory_mysql.py`

Run: `bash scripts/db.sh migration-test`

Expected: PASS。

- [ ] **Step 4: 运行 Playwright 全量**

Run: `cd frontend && npx playwright test tests/e2e`

Expected: PASS；弱网故障注入、双击、响应截断和上传恢复均包含在内。

- [ ] **Step 5: 运行项目验证脚本**

Run: `bash scripts/verify.sh full`

Expected: `FAIL=0 BLOCKED=0`；PASS 数以本次实际输出为准。

### Task 7: 构建离线 no-build 容器包

**Files:**
- `scripts/release.sh`, renderer, deploy scripts, generated release under ignored `releases/`

- [ ] **Step 1: 检查 git 与用户数据边界**

Run: `git status --short`

Expected: 只有明确保留的 `Stages/excel_final/data/`、`output/`、`releases/` 未跟踪目录；源码和文档无未提交变化。

- [ ] **Step 2: 构建版本化镜像**

使用下一可用 release 版本执行仓库 `scripts/release.sh`，复用固定上游镜像 digest；backend 镜像同时供 API、dispatcher 和 workers，前端单独镜像，MySQL/MinIO 使用清单中的现成镜像。

- [ ] **Step 3: 验证离线包**

验证 GPG 解密、SHA256SUMS、images.manifest ID、compose 16 服务、`pull_policy: never`、无 build 字段、运行时源码保护和 ODA 实际 roundtrip。

- [ ] **Step 4: 本地冷启动和重启恢复**

在隔离 Compose project/临时端口启动 release：MySQL/MinIO → API → dispatcher → 其余服务。模拟 API/dispatcher/worker 退出和整栈重启，断言 pending outbox、上传会话和现有卷恢复。

### Task 8: 生产只读预检、备份和一次维护窗部署

**Files:**
- Remote `/opt/dwg-agent/server`
- Encrypted release bundle and backup directory

- [ ] **Step 1: 通过 Tailscale 做只读预检**

确认 15 个旧容器健康、无活动 multipart/file transfer/Job、两个项目和用户/文件/对象清单一致、磁盘余量、MySQL 连接水位、MinIO 内存与对象一致性。任何活动上传存在时继续等待，不停止服务。

- [ ] **Step 2: 安装 release 但不切换**

上传加密包，验证 hash/image ID，保留 `.env.docker` 和三个持久卷。不得 `git pull`、不得 build、不得清空项目数据。

- [ ] **Step 3: 进入一次短维护窗**

优雅停止 Nginx 接受新写入，等待在途请求为零，创建并恢复验证 MySQL dump，记录 MinIO/数据库/对象计数和时间抽样。

- [ ] **Step 4: 切换 16 服务**

按 MySQL/MinIO → API（Alembic、时间迁移、seed、broker schema）→ dispatcher → workers → Nginx 启动。每层健康后才继续。

- [ ] **Step 5: 生产验收**

验证容器/Python/MySQL/Celery 时间为北京时间；16/16 健康；主页/LAN/Tailscale URL 200；两个现有项目和文件不变；outbox 无超龄 pending；第一步转换用独立可清理的小型验证数据证明按钮只生成一套任务；上传会话中断恢复；数据库与 MinIO 无不一致。

- [ ] **Step 6: 清理测试和调试痕迹**

删除仅用于验收的可清理业务记录和对象，保留审计要求的发布证据；清除临时端口、临时 Compose project、传输临时文件和过期镜像，不删除备份和持久卷。

### Task 9: 最终记录

**Files:**
- Release notes and applicable runbooks

- [ ] **Step 1: 记录事实**

写入实际 commit、镜像 ID、bundle SHA-256、测试 counts、迁移 revision、备份位置、16 容器状态、公开 LAN/Tailscale URL 和回滚点。不得沿用历史测试数字。

- [ ] **Step 2: 最终 git 检查**

Run: `git status --short`

Expected: 源码/计划/文档干净，只有用户明确保留的数据和 release 目录。

- [ ] **Step 3: 提交发布记录**

```bash
git add docs scripts
git commit -m "docs: record resilient production release"
```
