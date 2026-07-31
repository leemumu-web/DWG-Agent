# Production Command Delivery Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让生产命令在多账号并发、HTTP 响应丢失和进程崩溃时收敛到同一个 Job，并通过 MySQL 事务发件箱最终稳定投递 Celery。

**Architecture:** `jobs.operation_key` 提供跨账号资源级去重，`job_dispatches` 在 Job attempt 同一事务中记录投递意图，独立 dispatcher 以租约发布并允许安全重复消息。`api_command_receipts` 为明确列出的 JSON 命令保存同事务成功回执；普通 mutation 和 multipart 不获得自动重试。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、MySQL 8、Celery SQLAlchemy transport、Docker Compose、pytest、React Query/TypeScript。

---

## 文件结构

- Modify: `backend/app/modules/jobs/models.py` — Job 资源级键与投递行模型。
- Modify: `backend/app/modules/jobs/creation.py` — 用户请求键和资源操作键的双重复用算法。
- Create: `backend/app/modules/jobs/outbox.py` — 同事务 stage、租约、成功/失败结算和一次 drain。
- Create: `backend/app/modules/jobs/dispatcher.py` — 单用途常驻 dispatcher 入口。
- Modify: `backend/app/modules/jobs/dispatch.py` — 只保留消息编码/发布，不再承担路由提交后补偿。
- Modify: `backend/app/modules/jobs/interface.py` — 只导出稳定的 Job/outbox 应用接口。
- Create: `backend/app/platform/http/idempotency.py` — `ApiCommandReceipt` 和请求摘要/回执 helper。
- Modify: `backend/app/platform/database/base.py` — 注册新增模型时保持现有 Base 边界。
- Modify: `backend/app/modules/jobs/routes/commands.py`, `backend/app/modules/workflows/routes/intake.py`, `backend/app/modules/workflows/routes/execution.py`, `backend/app/modules/excel_processing/routes/processing.py` — 提交前 stage dispatch/receipt。
- Modify: enqueue interfaces under `backend/app/modules/cad_processing/interface.py`, `dxf_classification/interface.py`, `dxf_splitting/interface.py`, `excel_processing/interface.py` — 接受稳定 `task_id`。
- Modify: `backend/app/modules/operations/control_plane/service.py` — outbox 可观测数据。
- Create: `backend/migrations/versions/b6d2c8f4e910_add_reliable_job_dispatch.py` — 新列、新表、索引和 queued Job 回填。
- Modify: `compose.yaml`, `scripts/release/server-deploy.sh`, `backend/tests/infrastructure/test_server_release.py` — 第 16 个 dispatcher 服务和启动门禁。
- Create: `backend/tests/jobs/test_job_outbox.py`, `backend/tests/jobs/test_job_outbox_mysql.py`, `backend/tests/infrastructure/test_dispatcher_runtime.py` — 单元、真实 MySQL 和运行时契约。

### Task 1: 增加跨账号 Job 资源级去重

**Files:**
- Modify: `backend/app/modules/jobs/models.py`
- Modify: `backend/app/modules/jobs/creation.py`
- Modify: `backend/app/modules/jobs/interface.py`
- Modify: `backend/app/modules/workflows/intake/conversion.py`
- Test: `backend/tests/excel_processing/test_excel_final_idempotency.py`
- Test: `backend/tests/workflows/test_workflow_input_service.py`

- [ ] **Step 1: 写跨账号失败测试**

```python
def test_input_conversion_operation_key_is_shared_across_actors(db, input_batch, two_users):
    first = prepare_input_conversions(db, input_batch, created_by=two_users[0].id)
    db.commit()
    second = prepare_input_conversions(db, input_batch, created_by=two_users[1].id)
    assert [job.id for job in second.jobs] == [job.id for job in first.jobs]
    assert second.dispatch == []
```

- [ ] **Step 2: 运行并确认当前按 `created_by` 分叉**

Run: `cd backend && uv run pytest -q tests/workflows/test_workflow_input_service.py -k operation_key`

Expected: FAIL，两个账号得到不同 Job ID 或第二次仍需要 dispatch。

- [ ] **Step 3: 增加模型字段和创建接口**

```python
class Job(TimestampMixin, Base):
    __table_args__ = (
        UniqueConstraint("created_by", "task_type", "request_key", name="uq_jobs_actor_task_request_key"),
        UniqueConstraint("task_type", "operation_key", name="uq_jobs_task_operation_key"),
    )
    operation_key: Mapped[str | None] = mapped_column(String(191))


def create_or_reuse_job(
    db: Session,
    payload: JobCreate,
    *,
    created_by: int,
    request_key: str | None,
    operation_key: str | None = None,
) -> tuple[Job, bool]:
    if operation_key is not None:
        conditions = (Job.task_type == payload.task_type, Job.operation_key == operation_key)
    elif request_key is not None:
        conditions = (
            Job.created_by == created_by,
            Job.task_type == payload.task_type,
            Job.request_key == request_key,
        )
    else:
        return create_job(db, payload, created_by), False
    existing = db.scalar(select(Job).where(*conditions))
    if existing is not None:
        _require_matching_idempotent_job(existing, payload)
        return existing, True
    try:
        with db.begin_nested():
            job = create_job(
                db,
                payload,
                created_by,
                request_key=request_key,
                operation_key=operation_key,
            )
    except IntegrityError:
        existing = db.scalar(select(Job).where(*conditions).with_for_update())
        if existing is None:
            raise
        _require_matching_idempotent_job(existing, payload)
        return existing, True
    return job, False
```

同时把 `create_job(..., request_key: str | None = None, operation_key: str | None = None)` 的两个值原样写入 Job。`operation_key` 非空优先按 `(task_type, operation_key)` 查询；冲突后的 locking current read 使用完全相同条件。复用时必须继续调用 `_require_matching_idempotent_job` 检查参数。

- [ ] **Step 4: 让输入转换使用资源键**

```python
job, reused = create_or_reuse_job(
    db,
    payload,
    created_by=created_by,
    request_key=None,
    operation_key=f"workflow-input:{batch.id}:item:{item.id}",
)
```

- [ ] **Step 5: 运行 focused tests**

Run: `cd backend && uv run pytest -q tests/workflows/test_workflow_input_service.py tests/excel_processing/test_excel_final_idempotency.py`

Expected: PASS；原有同账号请求键测试保持不变。

- [ ] **Step 6: 提交**

```bash
git add backend/app/modules/jobs backend/app/modules/workflows/intake/conversion.py backend/tests/workflows/test_workflow_input_service.py backend/tests/excel_processing/test_excel_final_idempotency.py
git commit -m "feat: deduplicate shared workflow jobs"
```

### Task 2: 建立 Job attempt 发件箱模型与同事务 stage

**Files:**
- Modify: `backend/app/modules/jobs/models.py`
- Create: `backend/app/modules/jobs/outbox.py`
- Modify: `backend/app/modules/jobs/interface.py`
- Create: `backend/tests/jobs/test_job_outbox.py`

- [ ] **Step 1: 写事务与唯一性失败测试**

```python
def test_stage_conversion_dispatch_is_atomic_and_unique(db, queued_jobs):
    first = stage_conversion_dispatch(db, task_type="convert_dwg_to_dxf", jobs=queued_jobs)
    second = stage_conversion_dispatch(db, task_type="convert_dwg_to_dxf", jobs=queued_jobs)
    assert [row.id for row in second] == [row.id for row in first]
    assert len({row.dispatch_uid for row in first}) == 1


def test_rollback_removes_job_and_dispatch(db, queued_job):
    stage_job_dispatch(db, queued_job)
    db.rollback()
    assert db.scalar(select(func.count()).select_from(JobDispatch)) == 0
```

- [ ] **Step 2: 运行并确认缺少模型/helper**

Run: `cd backend && uv run pytest -q tests/jobs/test_job_outbox.py`

Expected: FAIL，缺少 `JobDispatch` 或 `stage_job_dispatch`。

- [ ] **Step 3: 实现模型**

```python
class JobDispatch(TimestampMixin, Base):
    __tablename__ = "job_dispatches"
    __table_args__ = (
        UniqueConstraint("job_id", "job_attempt", name="uq_job_dispatch_attempt"),
        Index("ix_job_dispatch_pending", "status", "available_at"),
        Index("ix_job_dispatch_lease", "lease_expires_at"),
        Index("ix_job_dispatch_uid", "dispatch_uid"),
    )
    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    dispatch_uid: Mapped[str] = mapped_column(String(36), nullable=False)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    job_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    pipeline: Mapped[str] = mapped_column(String(64), nullable=False)
    dispatch_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    celery_task_id: Mapped[str | None] = mapped_column(String(64))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(String(500))
```

- [ ] **Step 4: 实现 stage API**

```python
def stage_conversion_dispatch(db: Session, *, task_type: str, jobs: list[Job]) -> list[JobDispatch]:
    attempts = {(job.id, job.attempt) for job in jobs}
    existing = list(db.scalars(select(JobDispatch).where(tuple_(JobDispatch.job_id, JobDispatch.job_attempt).in_(attempts))))
    if existing:
        if {(row.job_id, row.job_attempt) for row in existing} != attempts:
            raise AppHTTPException(409, "JOB_DISPATCH_SET_CONFLICT", "Only part of the job batch already has a dispatch intent.")
        return sorted(existing, key=lambda row: row.job_id)
    dispatch_uid = str(uuid4())
    rows = [
        JobDispatch(
            dispatch_uid=dispatch_uid,
            job_id=job.id,
            job_attempt=job.attempt,
            task_type=task_type,
            pipeline=job.pipeline or PIPELINE_STUB,
            dispatch_mode="conversion_batch",
            status="pending",
            available_at=business_now(),
        )
        for job in jobs
    ]
    db.add_all(rows)
    db.flush()
    return rows
```

`stage_job_dispatch` 使用相同逻辑但 `dispatch_mode="single"`。调用者只对新 Job 或新 retry attempt stage；复用 active/succeeded Job 不重复插入。

- [ ] **Step 5: 运行测试并提交**

Run: `cd backend && uv run pytest -q tests/jobs/test_job_outbox.py`

Expected: PASS。

```bash
git add backend/app/modules/jobs backend/tests/jobs/test_job_outbox.py
git commit -m "feat: stage durable job dispatch intents"
```

### Task 3: 实现租约 dispatcher 与安全重复投递

**Files:**
- Create: `backend/app/modules/jobs/dispatcher.py`
- Modify: `backend/app/modules/jobs/outbox.py`
- Modify: `backend/app/modules/jobs/dispatch.py`
- Modify: enqueue interface files listed in File Structure
- Test: `backend/tests/jobs/test_job_outbox.py`
- Test: `backend/tests/infrastructure/test_dispatcher_runtime.py`

- [ ] **Step 1: 写租约和模糊发布失败测试**

```python
def test_expired_lease_is_reclaimed(factory, pending_dispatch):
    first = lease_next_dispatch(factory, lease_seconds=30)
    expire_lease(factory, first.dispatch_uid)
    second = lease_next_dispatch(factory, lease_seconds=30)
    assert second.dispatch_uid == first.dispatch_uid
    assert second.lease_token != first.lease_token


def test_publish_response_loss_retries_without_second_job_claim(factory, pending_dispatch, monkeypatch):
    monkeypatch.setattr(outbox, "publish_dispatch", publish_then_raise)
    assert drain_once(factory) is True
    assert drain_once(factory) is True
    assert count_successful_claims(factory, pending_dispatch.job_id) == 1
```

- [ ] **Step 2: 实现短事务租约**

`lease_next_dispatch()` 先把过期 `leased` 行恢复为 `pending`，再以 `with_for_update(skip_locked=True)` 选择最早 `available_at <= now` 的一行，锁定相同 `dispatch_uid` 全组，写入同一 `lease_token` 和 `lease_expires_at` 后提交。broker I/O 必须发生在该事务结束之后。

```python
@dataclass(frozen=True)
class DispatchLease:
    dispatch_uid: str
    lease_token: str
    mode: str
    task_type: str
    jobs: tuple[tuple[int, int], ...]


def retry_delay(attempt: int) -> float:
    return min(30.0, 0.5 * (2 ** min(attempt, 6)))
```

- [ ] **Step 3: 让发布函数接受稳定 task ID**

所有 enqueue helper 改用：

```python
result = task.apply_async(args=[job_id, attempt], task_id=task_id)
return str(result.id)
```

批量任务使用 `args=[jobs]`。`publish_dispatch(lease)` 对 single 和 conversion_batch 分支分别调用现有任务函数，`task_id=lease.dispatch_uid`。

- [ ] **Step 4: 实现结算与主循环**

```python
def run_forever(factory: sessionmaker[Session] = SessionLocal) -> None:
    while True:
        worked = drain_once(factory)
        if not worked:
            time.sleep(0.5)
```

发布成功按 token 标记整组 `delivered`；瞬时异常把整组恢复 `pending`、增加次数、计算 `available_at`；非法模式/任务类型标记永久 `failed` 并条件终止仍是相同 queued attempt 的 Job。错误消息经白名单分类并截断 500 字符。

- [ ] **Step 5: 运行 dispatcher tests**

Run: `cd backend && uv run pytest -q tests/jobs/test_job_outbox.py tests/infrastructure/test_dispatcher_runtime.py`

Expected: PASS，且测试不包含真实 sleep。

- [ ] **Step 6: 提交**

```bash
git add backend/app/modules/jobs backend/app/modules/cad_processing/interface.py backend/app/modules/dxf_classification/interface.py backend/app/modules/dxf_splitting/interface.py backend/app/modules/excel_processing/interface.py backend/tests/jobs/test_job_outbox.py backend/tests/infrastructure/test_dispatcher_runtime.py
git commit -m "feat: deliver jobs through leased outbox"
```

### Task 4: 把现有 8 个 Job 发布入口改为事务内 outbox

**Files:**
- Modify: `backend/app/modules/jobs/routes/commands.py`
- Modify: `backend/app/modules/workflows/routes/intake.py`
- Modify: `backend/app/modules/workflows/routes/execution.py`
- Modify: `backend/app/modules/excel_processing/routes/processing.py`
- Modify: `backend/app/modules/workflows/stage_execution.py`
- Test: `backend/tests/infrastructure/test_celery_minio_deployment.py`
- Test: `backend/tests/workflows/test_workflow_input_api.py`
- Test: `backend/tests/workflows/test_workflow_production.py`

- [ ] **Step 1: 写静态与行为失败测试**

```python
def test_http_routes_never_dispatch_after_commit():
    sources = "\n".join(path.read_text() for path in JOB_CREATING_ROUTES)
    assert "dispatch_committed_job" not in sources
    assert "dispatch_committed_conversion_batch" not in sources
    assert "stage_job_dispatch" in sources
    assert "stage_conversion_dispatch" in sources
```

输入转换 API 测试断言 response 为 202、Job 为 queued、数据库存在当前 attempt dispatch；第二次请求 `dispatched_count == 0`。

- [ ] **Step 2: 在每个提交前 stage**

```python
if not reused:
    stage_job_dispatch(db, job)
db.commit()
```

批量入口在同一事务调用 `stage_conversion_dispatch`。retry route 在 `retry_job()` 增加 attempt 后立即 stage。删除路由层所有 commit 后 direct dispatch。

- [ ] **Step 3: 保持 eager 测试可控**

测试需要立即执行时显式调用 `drain_once(get_test_session_factory())`；生产路由不读取 `CELERY_TASK_ALWAYS_EAGER` 来改变可靠性流程。

- [ ] **Step 4: 运行回归并提交**

Run: `cd backend && uv run pytest -q tests/jobs tests/workflows/test_workflow_input_api.py tests/workflows/test_workflow_production.py tests/infrastructure/test_celery_minio_deployment.py`

Expected: PASS。

```bash
git add backend/app/modules/jobs/routes backend/app/modules/workflows backend/app/modules/excel_processing/routes backend/tests
git commit -m "refactor: route all jobs through durable outbox"
```

### Task 5: 增加 JSON 命令成功回执

**Files:**
- Create: `backend/app/platform/http/idempotency.py`
- Modify: production project/workflow command routes
- Test: `backend/tests/workflows/test_production_project_api.py`
- Test: `backend/tests/workflows/test_workflow_api.py`

- [ ] **Step 1: 写相同键重放与摘要冲突测试**

```python
def test_production_project_same_key_returns_original_result(client, headers):
    h = {**headers, "Idempotency-Key": "project-command-1"}
    payload = {"code": "P-REPLAY-001", "name": "可靠创建"}
    first = client.post("/api/v1/workflows/production-projects", headers=h, json=payload)
    second = client.post("/api/v1/workflows/production-projects", headers=h, json=payload)
    assert second.status_code == 201
    assert second.json()["data"] == first.json()["data"]


def test_command_key_cannot_change_payload(client, headers):
    h = {**headers, "Idempotency-Key": "project-command-conflict"}
    client.post(
        "/api/v1/workflows/production-projects",
        headers=h,
        json={"code": "P-REPLAY-002", "name": "原请求"},
    )
    response = client.post(
        "/api/v1/workflows/production-projects",
        headers=h,
        json={"code": "P-REPLAY-003", "name": "变化请求"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
```

- [ ] **Step 2: 实现模型和摘要**

```python
def request_sha256(payload: BaseModel | dict[str, object]) -> str:
    data = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def record_success(
    db: Session,
    *,
    actor_user_id: int,
    endpoint_scope: str,
    idempotency_key: str,
    request_digest: str,
    response_status: int,
    response_json: dict[str, object],
    resource_type: str,
    resource_id: int,
) -> ApiCommandReceipt:
    encoded = json.dumps(response_json, ensure_ascii=False, sort_keys=True).encode()
    if len(encoded) > 64 * 1024:
        raise AppHTTPException(500, "COMMAND_RESPONSE_TOO_LARGE", "Command response is too large to persist safely.")
    row = ApiCommandReceipt(
        command_uid=str(uuid4()),
        actor_user_id=actor_user_id,
        endpoint_scope=endpoint_scope,
        idempotency_key=idempotency_key,
        request_sha256=request_digest,
        response_status=response_status,
        response_json=response_json,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    db.add(row)
    db.flush()
    return row
```

回执 JSON 编码后超过 64 KiB 时拒绝记录；不允许 token、cookie、签名 URL 和 bytes。

- [ ] **Step 3: 接入关键 JSON 命令**

项目创建、转换、冻结、阶段执行/确认、工作流取消和 Job 重试接受 `Idempotency-Key`。在业务动作前读取回执；在成功响应组成后、`db.commit()` 前写入回执。相同键竞争依靠唯一约束和 locking current read 返回胜者。

- [ ] **Step 4: 运行测试并提交**

Run: `cd backend && uv run pytest -q tests/workflows/test_production_project_api.py tests/workflows/test_workflow_api.py tests/jobs/test_job_lifecycle.py`

Expected: PASS。

```bash
git add backend/app/platform/http/idempotency.py backend/app/modules/workflows/routes backend/app/modules/jobs/routes backend/tests
git commit -m "feat: persist reliable command receipts"
```

### Task 6: 分类数据库瞬时故障并限制事务重放

**Files:**
- Create: `backend/app/platform/database/retry.py`
- Modify: `backend/app/platform/http/exceptions.py`
- Modify: reliable command service entry points only
- Create: `backend/tests/infrastructure/test_database_retry.py`

- [ ] **Step 1: 写 MySQL 错误分类失败测试**

```python
@pytest.mark.parametrize("code", [1205, 1213, 2006, 2013])
def test_mysql_transient_codes_are_classified(code):
    exc = OperationalError("statement", {}, FakeMySqlError(code))
    assert classify_database_error(exc).retryable is True


def test_constraint_and_programming_errors_are_not_retried():
    assert classify_database_error(IntegrityError("statement", {}, Exception())).retryable is False
```

- [ ] **Step 2: 实现分类与结构化异常**

```python
@dataclass(frozen=True)
class DatabaseFailure:
    code: str
    retryable: bool
    status_code: int


def classify_database_error(exc: BaseException) -> DatabaseFailure:
    mysql_code = getattr(getattr(exc, "orig", None), "args", (None,))[0]
    if mysql_code in {1205, 1213}:
        return DatabaseFailure("CONCURRENT_STATE_CHANGED", True, 409)
    if mysql_code in {2006, 2013} or isinstance(exc, DisconnectionError):
        return DatabaseFailure("DATABASE_TEMPORARILY_UNAVAILABLE", True, 503)
    return DatabaseFailure("DATABASE_OPERATION_FAILED", False, 500)
```

`AppHTTPException` 增加可选 `headers` 并传给 `HTTPException`；409/503 瞬时响应设置 `Retry-After: 1`，正文不暴露 SQL 或连接信息。

- [ ] **Step 3: 实现只接收纯事务 callback 的有界重放**

```python
def run_replay_safe(
    factory: sessionmaker[Session],
    operation: Callable[[Session], T],
    *,
    max_attempts: int = 2,
) -> T:
    for attempt in range(max_attempts):
        with factory() as db:
            try:
                result = operation(db)
                db.commit()
                return result
            except DBAPIError as exc:
                db.rollback()
                failure = classify_database_error(exc)
                if not failure.retryable or attempt + 1 == max_attempts:
                    raise database_http_exception(failure) from exc
    raise AssertionError("bounded database retry loop exhausted without returning")
```

callback 只能用于已经有 command receipt/operation key 的 JSON 小事务；上传、对象存储、CAD 调用、邮件和其他外部副作用不得传入。

- [ ] **Step 4: 验证每次使用新 Session 且不重放未知写入**

测试第一次 1213、第二次成功时创建两个 Session 且只提交一次业务结果；IntegrityError 只调用一次；上传路由源码不得导入 `run_replay_safe`。

- [ ] **Step 5: 运行测试并提交**

Run: `cd backend && uv run pytest -q tests/infrastructure/test_database_retry.py tests/infrastructure/test_db_session.py`

Expected: PASS。

```bash
git add backend/app/platform/database/retry.py backend/app/platform/http/exceptions.py backend/tests/infrastructure/test_database_retry.py
git commit -m "feat: bound replay-safe database retries"
```

### Task 7: 迁移、真实 MySQL 并发和 dispatcher 服务

**Files:**
- Create: `backend/migrations/versions/b6d2c8f4e910_add_reliable_job_dispatch.py`
- Create: `backend/tests/jobs/test_job_outbox_mysql.py`
- Modify: `compose.yaml`
- Modify: `scripts/release/server-deploy.sh`
- Modify: `backend/tests/infrastructure/test_server_release.py`
- Modify: `backend/app/modules/operations/control_plane/service.py`

- [ ] **Step 1: 写迁移与 Compose 失败测试**

断言 revision/down_revision 为 `b6d2c8f4e910`/`a4c8e1f2b730`，唯一约束和 pending/lease 索引存在；server renderer 输出 16 个服务，`dispatcher` 使用 backend image、只读根文件系统、低资源限制和健康检查；恢复顺序明确等待 dispatcher 后才启动剩余服务。

- [ ] **Step 2: 实现迁移与 queued 回填**

迁移创建 `api_command_receipts`、`job_dispatches`，给 `jobs` 增加 `operation_key`。MySQL 升级以当前 `jobs.status='queued'` 和 `attempt` 回填 single dispatch；`INSERT ... SELECT` 使用 `(job_id, job_attempt)` 唯一键防重。SQLite 测试分支使用 Alembic portable 操作。

- [ ] **Step 3: 增加服务**

```yaml
dispatcher:
  <<: *app-service
  command: ["python", "-m", "app.modules.jobs.dispatcher"]
  depends_on:
    backend-api:
      condition: service_healthy
  cpus: "${DISPATCHER_CPU_LIMIT:-0.5}"
  mem_limit: "${DISPATCHER_MEMORY_LIMIT:-512m}"
  pids_limit: 64
```

健康检查读取 dispatcher 写入 `/tmp/dwg-dispatcher-ready` 的新鲜心跳；该文件只在最近一次数据库访问成功后更新。

- [ ] **Step 4: 写真实 MySQL 竞争测试**

使用两个 Session、Barrier 和两个不同 actor，同时准备同一输入转换；断言每个 item 一个 Job、每 attempt 一个 dispatch、一个调用者创建且另一个复用。两个 dispatcher 并发 lease 同一 pending group 时只有一个获得租约。

- [ ] **Step 5: 暴露 outbox 健康事实**

控制台查询返回 `pending_count`、`leased_count`、`retrying_count`、`failed_count` 和 `oldest_pending_age_seconds`。测试使用已知时间的 pending/leased 行验证计数；输出只含错误类别和计数，不返回 payload、DSN 或异常正文。

- [ ] **Step 6: 运行迁移和门禁**

Run: `cd backend && uv run pytest -q tests/jobs/test_job_outbox.py tests/infrastructure/test_dispatcher_runtime.py tests/infrastructure/test_server_release.py tests/infrastructure/test_migrations.py`

Run: `cd backend && MYSQL_INTEGRATION_DATABASE_URL="$MYSQL_INTEGRATION_DATABASE_URL" uv run pytest -q tests/jobs/test_job_outbox_mysql.py`

Run: `bash scripts/db.sh migration-test`

Expected: 全部 PASS；未配置集成 URL 时 MySQL 文件明确 skip，正式发布门禁必须提供 URL 并实际通过。

- [ ] **Step 7: 提交**

```bash
git add backend/migrations backend/app/modules/operations/control_plane/service.py compose.yaml scripts/release/server-deploy.sh backend/tests
git commit -m "feat: deploy reliable job dispatcher"
```

### Task 8: 将可靠性策略推广到所有可见 mutation 按钮

**Files:**
- Create: `frontend/src/shared/api/useAppMutation.ts`
- Modify: every component returned by `rg -l 'useMutation' frontend/src/features`
- Modify: frontend API modules for supported reliable commands
- Modify: corresponding backend command routes
- Modify: `frontend/scripts/check-architecture.mjs`
- Test: `backend/tests/contracts/test_frontend_contract.py`

- [ ] **Step 1: 建立按钮清单并写失败门禁**

Run: `rg -n 'useMutation' frontend/src/features`

把结果逐项记录到计划执行日志，分类为 `reliable_command`、`convergent_state`、`transfer_session`、`download` 或 `local_only`。架构测试要求 feature 组件不再从 `@tanstack/react-query` 直接导入 `useMutation`。

```python
def test_every_visible_mutation_declares_reliability_policy():
    offenders = find_feature_sources_importing_direct_use_mutation()
    assert offenders == []
```

- [ ] **Step 2: 实现统一 hook**

```typescript
export type MutationReliability =
  | 'reliable_command'
  | 'convergent_state'
  | 'transfer_session'
  | 'download'
  | 'local_only';

export function useAppMutation<TData, TError, TVariables>(
  policy: MutationReliability,
  options: UseMutationOptions<TData, TError, TVariables>,
) {
  return useMutation({ ...options, retry: false, meta: { ...options.meta, reliability: policy } });
}
```

网络有界重试只发生在 `reliableCommand()` API helper 内；hook 本身永远不全局重试。

- [ ] **Step 3: 迁移全部 feature mutation**

每个现有 `useMutation({ ... })` 改为 `useAppMutation('<policy>', { ... })`。Job 创建/重试、生产流程命令和已有幂等 Excel 命令优先升级为 `reliable_command`；文件/文件夹上传为 `transfer_session`；Blob/签名下载为 `download`；尚未具备服务端回执的设置型操作为 `convergent_state` 并在网络不确定时查询资源。

- [ ] **Step 4: 扩展后端可靠命令覆盖**

按按钮清单逐个为可安全重放的 JSON endpoint 接入 `ApiCommandReceipt`。创建动作校验请求摘要，设置/删除动作记录目标资源和成功响应；Job 类动作同时 stage outbox。每迁移一组端点，增加“响应丢失后同键重放只有一个业务结果”的 API 测试。

- [ ] **Step 5: 运行覆盖门禁并提交**

Run: `cd backend && uv run pytest -q tests/contracts/test_frontend_contract.py tests/workflows tests/jobs tests/identity tests/operations`

Run: `cd frontend && npm run check:architecture && npm run build`

Expected: PASS；feature 中无直接 `useMutation`，每个按钮策略可枚举。

```bash
git add frontend/src frontend/scripts backend/app backend/tests/contracts backend/tests
git commit -m "feat: classify reliability for every mutation"
```

### Task 9: 模块全量回归

**Files:**
- Modify: affected READMEs under `backend/app/modules/jobs`, `platform/messaging`, `workflows`
- Test: full backend gates

- [ ] **Step 1: 更新事实文档**

删除“transactional outbox 未实现”的旧表述，记录 at-least-once 发布、Job attempt 条件抢占、16 服务顺序和运维指标。不得宣称 RabbitMQ 或绝对 exactly-once。

- [ ] **Step 2: 运行完整后端**

Run: `cd backend && uv run ruff check app tests`

Run: `cd backend && uv run pytest -q`

Expected: PASS；任何环境 skip 数量记录到最终验收，不把未运行的 MySQL 竞争测试算作通过。

- [ ] **Step 3: 提交**

```bash
git add backend/app backend/tests docs compose.yaml scripts
git commit -m "docs: document durable command delivery"
```
