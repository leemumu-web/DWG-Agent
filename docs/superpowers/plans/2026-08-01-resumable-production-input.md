# Resumable Production Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用可查询、可按文件恢复的上传会话替代前端单个超长 DWG 文件夹请求，同时保持整批校验、相对路径、5000 文件上限和工作流血缘不变。

**Architecture:** 会话表冻结规范化清单，item 表逐文件记录 attempt、StoredFile 和 transfer；单文件请求可安全重复，全部项目完成后才在输入批次行锁内一次性登记 WorkflowInputItem。前端固定三路并发，断网后先同步会话，只重传缺失项；旧 `/input-dwg-folder` 保留兼容但新 UI 不再使用。

**Tech Stack:** FastAPI、Starlette UploadFile、SQLAlchemy/Alembic、MySQL、MinIO/LocalStorage、React 19、React Query、Axios、Playwright。

---

## 文件结构

- Create: `backend/app/modules/workflows/models/uploads.py` — upload session/item 持久模型。
- Modify: `backend/app/modules/workflows/models/__init__.py` — 注册模型。
- Create: `backend/app/modules/workflows/schemas/uploads.py` — manifest、session、item 和 completion 合同。
- Modify: `backend/app/modules/workflows/schemas/__init__.py` — 聚合公开 schema。
- Create: `backend/app/modules/workflows/intake/upload_sessions.py` — 会话创建、状态查询、项目上传、完成、取消。
- Modify: `backend/app/modules/workflows/intake/__init__.py` — 稳定应用接口。
- Create: `backend/app/modules/workflows/routes/upload_sessions.py` — HTTP 边界。
- Modify: `backend/app/modules/workflows/routes/router.py` — 注册路由。
- Modify: `backend/app/modules/workflows/intake/registration.py` — 复用清单校验和整批登记原语。
- Modify: `backend/app/modules/files/storage_transactions.py` — item attempt 的 transfer 结算保持现有补偿。
- Create: `backend/migrations/versions/c2f7a9d4e610_add_input_upload_sessions.py` — 会话结构和索引。
- Modify: `frontend/src/features/workflows/workflow-inputs.api.ts` — 新会话 API。
- Create: `frontend/src/features/workflows/useResumableInputUpload.ts` — 三路调度、重连同步和聚合进度。
- Modify: `frontend/src/features/workflows/ProductionInputPanel.tsx` — 会话 UI 与失败项重试。
- Modify: `frontend/src/features/workflows/workflow-input.ts` — 类型。
- Create: `backend/tests/workflows/test_input_upload_sessions.py`, `backend/tests/workflows/test_input_upload_sessions_mysql.py`。
- Modify: `frontend/tests/e2e/workflows/workflow-input.spec.ts` — 弱网恢复和多账号冲突。

### Task 1: 建立上传会话模型和清单合同

**Files:**
- Create: model/schema files listed above
- Modify: model/schema `__init__.py`
- Test: `backend/tests/workflows/test_input_upload_sessions.py`

- [ ] **Step 1: 写清单边界失败测试**

```python
def test_create_session_freezes_normalized_manifest(db, workflow, owner):
    request = UploadSessionCreate(
        root_name="生产图纸",
        items=[UploadManifestItem(ordinal=0, relative_path="生产图纸/A.dwg", original_name="A.dwg", size_bytes=2048, last_modified_ms=1)],
    )
    session = create_or_reuse_upload_session(db, workflow, owner.id, "folder-key-1", request)
    assert session.expected_file_count == 1
    assert session.expected_total_bytes == 2048
    assert len(session.items) == 1
    assert len(session.manifest_sha256) == 64
```

同时参数化 0/5001 项、4 MiB 以上 manifest、绝对路径、`..`、多根目录、非 DWG、重复路径和规范化同名，断言稳定 413/422 错误码。

- [ ] **Step 2: 运行并确认模块不存在**

Run: `cd backend && uv run pytest -q tests/workflows/test_input_upload_sessions.py -k manifest`

Expected: FAIL，缺少 upload session 模型或 service。

- [ ] **Step 3: 实现模型**

```python
class WorkflowInputUploadSession(TimestampMixin, Base):
    __tablename__ = "workflow_input_upload_sessions"
    __table_args__ = (
        UniqueConstraint("created_by", "workflow_run_id", "kind", "idempotency_key", name="uq_input_upload_session_key"),
        Index("ix_input_upload_session_expiry", "status", "expires_at"),
    )
    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    session_uid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    workflow_run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    input_batch_id: Mapped[int] = mapped_column(ForeignKey("workflow_input_batches.id"), nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("sys_users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="dwg_folder")
    idempotency_key: Mapped[str] = mapped_column(String(96), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    root_name: Mapped[str] = mapped_column(String(255), nullable=False)
    expected_batch_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

item 包含 ordinal/path/name/expected size/last modified/status/attempt/file_id/transfer_uid/actual size/SHA/error；唯一约束 `(session_id, ordinal)` 和 `(session_id, relative_path)`。

- [ ] **Step 4: 实现 Pydantic 合同和服务端摘要**

`UploadSessionCreate.items` 使用 `Field(min_length=1, max_length=5000)`；规范化后以 `json.dumps(..., sort_keys=True, separators=(",", ":"))` 计算 SHA-256。相同 idempotency key 只在摘要相同时复用，否则 `409 IDEMPOTENCY_KEY_REUSED`。

- [ ] **Step 5: 运行测试并提交**

Run: `cd backend && uv run pytest -q tests/workflows/test_input_upload_sessions.py -k manifest`

Expected: PASS。

```bash
git add backend/app/modules/workflows/models backend/app/modules/workflows/schemas backend/app/modules/workflows/intake/upload_sessions.py backend/tests/workflows/test_input_upload_sessions.py
git commit -m "feat: model resumable input sessions"
```

### Task 2: 实现单文件 attempt 上传与成功重放

**Files:**
- Modify: `backend/app/modules/workflows/intake/upload_sessions.py`
- Modify: `backend/app/modules/files/storage_transactions.py`
- Test: `backend/tests/workflows/test_input_upload_sessions.py`

- [ ] **Step 1: 写成功重放和失败续传测试**

```python
def test_uploaded_item_replay_returns_same_file(db, session, upload_file):
    first = save_upload_session_item(db, session, 0, upload_file, actor_user_id=session.created_by, request_id="r1")
    db.commit()
    second = save_upload_session_item(db, session, 0, upload_file, actor_user_id=session.created_by, request_id="r2")
    assert second.file_id == first.file_id
    assert second.attempt == first.attempt


def test_failed_item_uses_new_transfer_attempt(db, session, upload_file, failing_storage):
    with pytest.raises(StorageError):
        save_upload_session_item(db, session, 0, upload_file, actor_user_id=session.created_by, request_id="r1")
    retry = save_upload_session_item(db, session, 0, upload_file, actor_user_id=session.created_by, request_id="r2")
    assert retry.attempt == 2
```

- [ ] **Step 2: 运行并确认失败**

Run: `cd backend && uv run pytest -q tests/workflows/test_input_upload_sessions.py -k 'replay or transfer_attempt'`

Expected: FAIL，helper 不存在或重复创建 StoredFile。

- [ ] **Step 3: 实现项目行锁和 transfer key**

`save_upload_session_item()` 用 `SELECT ... FOR UPDATE` 获取 item，校验 session 未终止、actor 仍有项目写权限、文件名和实际大小匹配。已 uploaded 时重新验证 StoredFile 状态/大小/SHA 后返回。新 attempt 的 transfer key 为 `input-session:{session_uid}:item:{ordinal}:attempt:{attempt}`。

存储阶段不得持有上传 session 行锁跨完整文件流；先短事务把 item 标记 uploading/attempt 并创建 transfer，提交后写对象，再以 transfer_uid 和 attempt 条件结算。旧 attempt 完成不得覆盖新 attempt。

- [ ] **Step 4: 运行存储补偿测试**

Run: `cd backend && uv run pytest -q tests/workflows/test_input_upload_sessions.py tests/files/test_file_transfer_service.py`

Expected: PASS；数据库失败时对象补偿和 transfer 状态仍符合原合同。

- [ ] **Step 5: 提交**

```bash
git add backend/app/modules/workflows/intake/upload_sessions.py backend/app/modules/files/storage_transactions.py backend/tests/workflows/test_input_upload_sessions.py
git commit -m "feat: resume individual workflow uploads"
```

### Task 3: 实现整批完成、版本冲突和取消清理

**Files:**
- Modify: `backend/app/modules/workflows/intake/upload_sessions.py`
- Modify: `backend/app/modules/workflows/intake/registration.py`
- Modify: `backend/app/modules/workflows/models/intake.py`
- Test: `backend/tests/workflows/test_input_upload_sessions.py`

- [ ] **Step 1: 写原子完成和竞争测试**

```python
def test_completion_registers_all_items_atomically(db, ready_session):
    batch = complete_upload_session(db, ready_session, actor_user_id=ready_session.created_by)
    assert [item.original_name for item in batch.items] == ["A.dwg", "B.dwg"]
    assert batch.version == ready_session.expected_batch_version + 1
    assert ready_session.status == "completed"


def test_different_session_cannot_overwrite_changed_batch(db, ready_session, competing_session):
    complete_upload_session(db, ready_session, actor_user_id=ready_session.created_by)
    with pytest.raises(AppHTTPException) as error:
        complete_upload_session(db, competing_session, actor_user_id=competing_session.created_by)
    assert error.value.detail["code"] == "INPUT_BATCH_VERSION_CONFLICT"
```

- [ ] **Step 2: 实现版本递增**

每个成功改变输入内容的路径在输入批次行锁内执行 `batch.version += 1`：upload session completion、Excel 上传成功、整批清空和恢复。纯状态同步与转换 Job 进度不改变输入内容版本。

- [ ] **Step 3: 实现 completion**

按 session item ordinal 顺序重新读取并验证 StoredFile 和对象 SHA/大小，再调用现有 `register_input_file`。任一项目失败时事务回滚，不留下部分 WorkflowInputItem。相同 completed session 重放直接返回当前批次；不同 session 或版本不匹配返回带 `current_version`/counts 的 409。

- [ ] **Step 4: 实现取消/过期**

取消把 session 标记 cancelled；只对仍未被 WorkflowInputItem、Job、Artifact 或 Result 引用的 session file 执行现有 soft delete。maintenance 清理函数选择 `expires_at < business_now()` 的 open/uploading/ready session，复用相同引用检查。

- [ ] **Step 5: 运行测试并提交**

Run: `cd backend && uv run pytest -q tests/workflows/test_input_upload_sessions.py tests/workflows/test_workflow_input_service.py tests/files`

Expected: PASS。

```bash
git add backend/app/modules/workflows/intake backend/app/modules/workflows/models/intake.py backend/tests/workflows/test_input_upload_sessions.py
git commit -m "feat: finalize resumable input atomically"
```

### Task 4: 暴露会话 API 并保持旧入口兼容

**Files:**
- Create: `backend/app/modules/workflows/routes/upload_sessions.py`
- Modify: `backend/app/modules/workflows/routes/router.py`
- Modify: `backend/app/modules/workflows/routes/intake.py`
- Test: `backend/tests/workflows/test_workflow_input_api.py`
- Test: `backend/tests/architecture/test_workflow_boundaries.py`

- [ ] **Step 1: 写 OpenAPI 和权限失败测试**

```python
EXPECTED_UPLOAD_PATHS = {
    "/api/v1/workflows/{workflow_id}/input-upload-sessions": {"post"},
    "/api/v1/workflows/{workflow_id}/input-upload-sessions/{session_uid}": {"get", "delete"},
    "/api/v1/workflows/{workflow_id}/input-upload-sessions/{session_uid}/items/{ordinal}": {"post"},
    "/api/v1/workflows/{workflow_id}/input-upload-sessions/{session_uid}/completion": {"post"},
}
```

测试非成员 403、viewer 403、其他项目 session 404、阶段变化 409、同键 replay 200/201 语义。

- [ ] **Step 2: 实现路由**

每条路由先通过 workflow 加载和 `require_project_role(..., WORKFLOW_WRITE_ROLES)`；上传单项继续使用 `UploadFile`，不把文件内容读入 manifest 请求。GET 返回 5000 项时只给 ordinal/status/size/error 等必要字段。

- [ ] **Step 3: 给 Excel 上传增加稳定键**

`input-excel` 接受 `Idempotency-Key` header；成功重放按 batch、actor、键和文件摘要返回原 StoredFile/批次。文件内容变化使用相同键返回 `409 IDEMPOTENCY_KEY_REUSED`。

- [ ] **Step 4: 运行 API tests 并提交**

Run: `cd backend && uv run pytest -q tests/workflows/test_workflow_input_api.py tests/workflows/test_input_upload_sessions.py tests/architecture/test_workflow_boundaries.py`

Expected: PASS；旧 `/input-dwg-folder` 测试仍通过。

```bash
git add backend/app/modules/workflows/routes backend/app/modules/workflows/schemas backend/tests/workflows backend/tests/architecture/test_workflow_boundaries.py
git commit -m "feat: expose resumable workflow uploads"
```

### Task 5: 增加前端三路上传调度器

**Files:**
- Modify: `frontend/src/features/workflows/workflow-inputs.api.ts`
- Modify: `frontend/src/features/workflows/workflow-input.ts`
- Create: `frontend/src/features/workflows/useResumableInputUpload.ts`
- Test: `backend/tests/contracts/test_frontend_contract.py`

- [ ] **Step 1: 写前端源合同失败测试**

断言新 hook 常量 `UPLOAD_CONCURRENCY = 3`，API 包含 session create/get/item/complete/cancel，item multipart 请求没有 Axios `retry`，旧 `uploadWorkflowInputDwgFolder` 不再被 `ProductionInputPanel` 引用。

- [ ] **Step 2: 实现 API 类型**

```typescript
export interface InputUploadSession {
  session_uid: string;
  status: 'open' | 'uploading' | 'ready' | 'completed' | 'cancelled' | 'expired' | 'failed';
  manifest_sha256: string;
  expected_file_count: number;
  expected_total_bytes: number;
  uploaded_file_count: number;
  uploaded_bytes: number;
  items: InputUploadItem[];
}
```

item 上传接受 `AbortSignal` 和进度 callback，不配置自动重试。

- [ ] **Step 3: 实现调度状态机**

hook 输入 workflowId/files，先创建/恢复 session，再用最多 3 个 Promise worker 消费 pending ordinals。offline 时不领取新项；online 后 GET session，重建缺失队列。所有 items uploaded 后调用 completion。聚合 loaded bytes 只对同一 item 采用 `max(serverCompleted, currentProgress)`，避免回退。

- [ ] **Step 4: 构建验证并提交**

Run: `cd backend && uv run pytest -q tests/contracts/test_frontend_contract.py -k upload`

Run: `cd frontend && npm run build`

Expected: PASS。

```bash
git add frontend/src/features/workflows backend/tests/contracts/test_frontend_contract.py
git commit -m "feat: coordinate resumable folder uploads"
```

### Task 6: 接入 ProductionInputPanel 与弱网恢复 UX

**Files:**
- Modify: `frontend/src/features/workflows/ProductionInputPanel.tsx`
- Modify: `frontend/src/features/workflows/styles.css`
- Modify: `frontend/tests/e2e/workflows/workflow-input.spec.ts`

- [ ] **Step 1: 写 Playwright 失败场景**

路由 mock 第 2 个 item 首次 abort、其余成功；断言 UI 保留第 1 个完成状态，显示失败文件名，点击“仅重试失败项”后只再次请求第 2 项，最后 completion 一次。增加 offline/online 和不同 batch version 409 场景。

- [ ] **Step 2: 替换旧单请求调用**

面板继续执行根目录、非 DWG 忽略和 5000 上限检查；确认后调用 hook。上传期间禁用 Excel/清空/转换/冻结，但允许刷新和离开。显示总进度、已完成/总数、当前失败文件列表和页面内 `aria-live` 状态。

- [ ] **Step 3: 实现刷新后重选恢复**

session storage 保存 `{workflowId, sessionUid, manifestFingerprint}`，不保存 File 内容。刷新后显示“重新选择同一文件夹以继续”；重选后清单不同则阻止静默复用并要求新会话。

- [ ] **Step 4: 运行浏览器测试并提交**

Run: `cd frontend && npx playwright test tests/e2e/workflows/workflow-input.spec.ts`

Expected: PASS。

```bash
git add frontend/src/features/workflows frontend/tests/e2e/workflows/workflow-input.spec.ts
git commit -m "feat: resume production input in the UI"
```

### Task 7: 迁移和真实 MySQL 完成竞争

**Files:**
- Create: `backend/migrations/versions/c2f7a9d4e610_add_input_upload_sessions.py`
- Create: `backend/tests/workflows/test_input_upload_sessions_mysql.py`
- Modify: `backend/tests/infrastructure/test_migrations.py`

- [ ] **Step 1: 写迁移失败测试**

断言 revision/down_revision 为 `c2f7a9d4e610`/`b6d2c8f4e910`，两张表、外键、唯一约束和 expiry/status 索引存在，迁移不触碰已有 WorkflowInputItem/StoredFile 行。

- [ ] **Step 2: 实现迁移**

使用 Alembic portable 类型；MySQL 字符串排序规则沿用 schema 默认。downgrade 先删 item 再删 session，不删除 files/file_transfers。

- [ ] **Step 3: 真实并发测试**

两个账号分别建立相同 workflow 的 ready session，用 Barrier 同时 completion；断言一方成功、一方 `INPUT_BATCH_VERSION_CONFLICT`，WorkflowInputItem 数量等于一个 manifest，batch.version 只增加一次。

- [ ] **Step 4: 运行门禁并提交**

Run: `cd backend && uv run pytest -q tests/workflows/test_input_upload_sessions.py tests/infrastructure/test_migrations.py`

Run: `cd backend && uv run pytest -q tests/workflows/test_input_upload_sessions_mysql.py`

Run: `bash scripts/db.sh migration-test`

Expected: 单元、真实 MySQL 和空库迁移 PASS。

```bash
git add backend/migrations backend/tests/workflows/test_input_upload_sessions_mysql.py backend/tests/infrastructure/test_migrations.py
git commit -m "feat: migrate resumable input sessions"
```

### Task 8: 上传专项全量验证

**Files:**
- Modify: `backend/app/modules/workflows/intake/README.md`
- Modify: `frontend/src/features/workflows/README.md`

- [ ] **Step 1: 更新事实文档**

记录 session/item/finalization、三路并发、刷新后重选、旧入口兼容和孤立文件软删除边界。

- [ ] **Step 2: 运行专项套件**

Run: `cd backend && uv run ruff check app/modules/workflows app/modules/files tests/workflows tests/files`

Run: `cd backend && uv run pytest -q tests/workflows tests/files`

Run: `cd frontend && npm run build && npx playwright test tests/e2e/workflows/workflow-input.spec.ts`

Expected: 全部 PASS。

- [ ] **Step 3: 做并发 1/3/4 的受控负载对比**

用同一组可清理 DWG 样本分别以 1、3、4 路上传，记录总耗时、失败/重试数、API RSS、MinIO RSS、MySQL Threads_connected 和最终对象/文件计数。默认 3 必须在无 OOM、无连接池超时、无数据不一致条件下通过；4 只有明显更快且资源仍有安全余量时才能替换默认值，否则保持 3。

- [ ] **Step 4: 提交**

```bash
git add backend/app/modules/workflows frontend/src/features/workflows
git commit -m "docs: document resumable production input"
```
