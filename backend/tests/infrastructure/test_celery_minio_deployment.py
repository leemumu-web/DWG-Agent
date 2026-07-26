from __future__ import annotations

import tomllib
from io import BytesIO
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

import app.modules.jobs.dispatch as job_dispatch
import app.modules.jobs.stub_execution as job_stub
from app.bootstrap.seed import init_db
from app.main import app
from app.modules.jobs.interface import AnalysisResult, Job, run_local_stub_job
from app.platform.config.constants import JOB_CANCELLED, JOB_FAILED, JOB_QUEUED
from app.platform.http.exceptions import AppHTTPException
from tests.support.paths import REPO_ROOT


def test_runtime_dependencies_include_celery_and_minio_without_flower():
    pyproject = tomllib.loads((REPO_ROOT / "backend/pyproject.toml").read_text())
    deps = "\n".join(pyproject["project"]["dependencies"])

    assert "celery" in deps
    assert "minio" in deps
    assert "flower" not in deps


def test_minio_storage_backend_creates_bucket_and_streams_objects():
    from app.platform.storage.minio import MinioStorage

    class FakeObject:
        def __init__(self, payload: bytes):
            self.payload = payload
            self.closed = False
            self.released = False

        def stream(self, amt: int):
            for i in range(0, len(self.payload), amt):
                yield self.payload[i : i + amt]

        def close(self):
            self.closed = True

        def release_conn(self):
            self.released = True

    class FakeMinioClient:
        def __init__(self):
            self.buckets: set[str] = set()
            self.objects: dict[tuple[str, str], bytes] = {}
            self.put_calls: list[tuple[str, str, int, str | None]] = []

        def bucket_exists(self, bucket_name: str) -> bool:
            return bucket_name in self.buckets

        def make_bucket(self, bucket_name: str) -> None:
            self.buckets.add(bucket_name)

        def put_object(self, bucket_name, object_name, data, length, content_type=None):
            self.put_calls.append((bucket_name, object_name, length, content_type))
            self.objects[(bucket_name, object_name)] = data.read()

        def get_object(self, bucket_name: str, object_name: str):
            return FakeObject(self.objects[(bucket_name, object_name)])

    fake_client = FakeMinioClient()
    storage = MinioStorage(
        endpoint="http://minio:9000",
        access_key="minio",
        secret_key="secret",
        client=fake_client,
    )

    storage.put_fileobj(
        "dwg-original",
        "uploads/example.dwg",
        BytesIO(b"AC1027" + b"X" * 1024),
        length=1030,
        content_type="application/acad",
    )

    assert "dwg-original" in fake_client.buckets
    assert fake_client.put_calls == [
        ("dwg-original", "uploads/example.dwg", 1030, "application/acad")
    ]
    assert b"".join(storage.iter_file("dwg-original", "uploads/example.dwg")) == (
        b"AC1027" + b"X" * 1024
    )


def test_files_api_uses_storage_backend_instead_of_local_path_only():
    content = (
        REPO_ROOT / "backend/app/modules/files/routes/downloads.py"
    ).read_text()

    assert "get_local_file_path" not in content
    assert "StreamingResponse" in content
    assert "storage_factory.get_storage_backend" in content


def test_result_download_url_is_signed_and_downloads_generated_file():
    init_db()
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert login.status_code == 201, login.text
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    project = client.post(
        "/api/v1/workflows/projects",
        headers=headers,
        json={"code": "RESULT-DOWNLOAD", "name": "Result Download"},
    )
    assert project.status_code == 201, project.text

    job = client.post(
        "/api/v1/workflows/jobs",
        headers=headers,
        json={
            "project_id": project.json()["data"]["id"],
            "task_type": "framework_smoke_test",
        },
    )
    assert job.status_code == 202, job.text
    job_id = job.json()["data"]["id"]

    run_local_stub_job(job_id)

    results = client.get(f"/api/v1/workflows/jobs/{job_id}/results", headers=headers)
    assert results.status_code == 200, results.text
    result_id = results.json()["data"][0]["id"]

    download_url = client.get(f"/api/v1/workflows/results/{result_id}/download-url", headers=headers)
    assert download_url.status_code == 200, download_url.text
    url = download_url.json()["data"]["url"]
    assert "expires=" in url
    assert "signature=" in url

    download = client.get(url, headers=headers)
    assert download.status_code == 200, download.text
    assert b"local_stub" in download.content


def test_celery_app_registers_stage1_stub_task():
    from app.platform.config.settings import settings
    from app.platform.messaging.celery_app import celery_app

    assert celery_app.conf.broker_url == settings.celery_broker_url
    assert celery_app.conf.result_backend == settings.celery_result_backend
    assert "app.workers.tasks_report.run_stub_job" in celery_app.tasks


def test_mysql_result_rows_have_bounded_retention():
    from app.platform.messaging.celery_app import celery_app

    assert celery_app.conf.result_expires == 24 * 60 * 60


def test_celery_mysql_engines_use_bounded_pools():
    from app.platform.messaging.celery_app import celery_app

    expected = {
        "pool_pre_ping": True,
        "pool_recycle": 3600,
        "pool_size": 1,
        "max_overflow": 1,
        "pool_timeout": 30,
        "pool_use_lifo": True,
        "isolation_level": "READ COMMITTED",
    }
    for key, value in expected.items():
        assert celery_app.conf.broker_transport_options[key] == value
        assert celery_app.conf.database_engine_options[key] == value
    assert celery_app.conf.database_short_lived_sessions is True
    assert "task_default_expires" not in celery_app.conf


def test_sql_broker_maintenance_adds_queue_ordering_index():
    from app.platform.messaging.celery_app import (
        SQL_BROKER_MESSAGE_INDEX,
        ensure_sql_broker_message_index,
    )

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE kombu_message ("
                "id INTEGER PRIMARY KEY, visible BOOLEAN, timestamp DATETIME, queue_id INTEGER)"
            )
        )

    assert ensure_sql_broker_message_index(engine) is True
    indexes = {item["name"]: item["column_names"] for item in inspect(engine).get_indexes("kombu_message")}
    assert indexes[SQL_BROKER_MESSAGE_INDEX] == [
        "queue_id",
        "timestamp",
        "id",
        "visible",
    ]
    assert ensure_sql_broker_message_index(engine) is False


def test_sql_broker_schema_is_opened_and_closed_before_index_maintenance():
    from app.platform.messaging.celery_app import (
        SQL_BROKER_MESSAGE_INDEX,
        prepare_sql_broker_schema,
    )

    engine = create_engine("sqlite://")
    lifecycle: list[str] = []

    class FakeSession:
        def commit(self):
            lifecycle.append("commit")

        def close(self):
            lifecycle.append("session_close")

    class FakeChannel:
        closed = False
        session = FakeSession()

        def queue_declare(self, *, queue, durable):
            assert queue == "default"
            assert durable is True
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "CREATE TABLE kombu_message ("
                        "id INTEGER PRIMARY KEY, visible BOOLEAN, "
                        "timestamp DATETIME, queue_id INTEGER)"
                    )
                )

        def close(self):
            lifecycle.append("channel_close")
            self.closed = True

    class FakeConnection:
        def __init__(self):
            self.channel_instance = FakeChannel()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def channel(self):
            return self.channel_instance

    connection = FakeConnection()

    class FakeApp:
        def connection_for_write(self):
            return connection

    assert prepare_sql_broker_schema(FakeApp(), engine) is True
    assert connection.channel_instance.closed is True
    assert lifecycle == ["commit", "session_close", "channel_close"]
    assert SQL_BROKER_MESSAGE_INDEX in {
        item["name"] for item in inspect(engine).get_indexes("kombu_message")
    }


def test_mysql_sql_broker_schema_uses_advisory_lock(monkeypatch):
    from app.platform.messaging import celery_app as celery_module

    lifecycle: list[str] = []

    class FakeDialect:
        name = "mysql"

    class FakeLockConnection:
        def __enter__(self):
            lifecycle.append("lock_connection_open")
            return self

        def __exit__(self, *_args):
            lifecycle.append("lock_connection_close")

        def scalar(self, statement, parameters):
            sql = str(statement)
            if "GET_LOCK" in sql:
                lifecycle.append(f"get_lock:{parameters['lock_name']}")
                return 1
            if "RELEASE_LOCK" in sql:
                lifecycle.append(f"release_lock:{parameters['lock_name']}")
                return 1
            raise AssertionError(sql)

    class FakeEngine:
        dialect = FakeDialect()

        def connect(self):
            return FakeLockConnection()

    class FakeSession:
        def commit(self):
            lifecycle.append("commit")

        def close(self):
            lifecycle.append("session_close")

    class FakeChannel:
        session = FakeSession()

        def queue_declare(self, *, queue, durable):
            assert queue == "default"
            assert durable is True
            lifecycle.append("queue_declare")

        def close(self):
            lifecycle.append("channel_close")

    class FakeConnection:
        def __enter__(self):
            lifecycle.append("broker_connection_open")
            return self

        def __exit__(self, *_args):
            lifecycle.append("broker_connection_close")

        def channel(self):
            return FakeChannel()

    class FakeApp:
        def connection_for_write(self):
            return FakeConnection()

    monkeypatch.setattr(
        celery_module,
        "_sql_broker_schema_is_ready",
        lambda _engine: False,
    )
    monkeypatch.setattr(
        celery_module,
        "ensure_sql_broker_message_index",
        lambda _engine: lifecycle.append("ensure_index") or False,
    )

    assert celery_module.prepare_sql_broker_schema(FakeApp(), FakeEngine()) is False
    assert lifecycle == [
        "lock_connection_open",
        f"get_lock:{celery_module.SQL_BROKER_SCHEMA_LOCK}",
        "broker_connection_open",
        "queue_declare",
        "commit",
        "session_close",
        "channel_close",
        "broker_connection_close",
        "ensure_index",
        f"release_lock:{celery_module.SQL_BROKER_SCHEMA_LOCK}",
        "lock_connection_close",
    ]


def test_ready_sql_broker_schema_skips_lock_and_broker_connection(monkeypatch):
    from app.platform.messaging import celery_app as celery_module

    class NeverEngine:
        @property
        def dialect(self):
            raise AssertionError("ready schema must not acquire a lock")

    class NeverApp:
        def connection_for_write(self):
            raise AssertionError("ready schema must not open a broker connection")

    monkeypatch.setattr(
        celery_module,
        "_sql_broker_schema_is_ready",
        lambda _engine: True,
    )

    assert celery_module.prepare_sql_broker_schema(NeverApp(), NeverEngine()) is False


def test_worker_startup_cleans_expired_mysql_result_rows():
    from app.platform.messaging import celery_app as celery_module

    calls: list[str] = []

    class FakeBackend:
        def cleanup(self) -> None:
            calls.append("cleanup")

    class FakeApp:
        backend = FakeBackend()

    celery_module.cleanup_expired_task_results(FakeApp())

    assert calls == ["cleanup"]


def test_jobs_api_enqueues_celery_task_not_fastapi_background_task():
    api_sources = "\n".join(
        (REPO_ROOT / path).read_text()
        for path in (
            "backend/app/modules/jobs/routes/commands.py",
            "backend/app/modules/excel_processing/routes/processing.py",
        )
    )

    assert "BackgroundTasks" not in api_sources
    assert "background_tasks.add_task" not in api_sources
    assert "dispatch_committed_job" in api_sources
    assert "enqueue_job" not in api_sources


def test_job_create_marks_job_failed_when_celery_dispatch_fails(monkeypatch):
    init_db()
    client = TestClient(app, raise_server_exceptions=False)
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert login.status_code == 201, login.text
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    project = client.post(
        "/api/v1/workflows/projects",
        headers=headers,
        json={"code": "CELERY-DOWN", "name": "Celery Down"},
    )
    assert project.status_code == 201, project.text

    def fail_enqueue(job_id: int, pipeline: str, attempt: int) -> str:
        raise RuntimeError("mysql broker unavailable")

    monkeypatch.setattr(job_dispatch, "enqueue_job", fail_enqueue)

    response = client.post(
        "/api/v1/workflows/jobs",
        headers=headers,
        json={
            "project_id": project.json()["data"]["id"],
            "task_type": "framework_smoke_test",
            "precision_level": "normal",
        },
    )

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "JOB_ENQUEUE_FAILED"

    db = job_dispatch.SessionLocal()
    try:
        job = db.query(Job).order_by(Job.id.desc()).first()
        assert job is not None
        assert job.status == JOB_FAILED
        assert job.error_code == "JOB_ENQUEUE_FAILED"
        assert job.error_message == "The task could not be dispatched to the queue."
        assert "mysql broker unavailable" not in job.error_message
    finally:
        db.close()


def test_job_retry_does_not_leave_queued_row_when_dispatch_fails(monkeypatch):
    init_db()
    client = TestClient(app, raise_server_exceptions=False)
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    db = job_dispatch.SessionLocal()
    try:
        job = Job(
            created_by=1,
            task_type="framework_smoke_test",
            precision_level="normal",
            pipeline="local_stub",
            status=JOB_FAILED,
            priority=0,
            progress=20,
            params_json={},
        )
        db.add(job)
        db.commit()
        job_id = job.id
    finally:
        db.close()

    def fail_enqueue(job_id: int, pipeline: str, attempt: int) -> str:
        raise RuntimeError("mysql://user:secret@broker/internal")

    monkeypatch.setattr(job_dispatch, "enqueue_job", fail_enqueue)

    response = client.post(f"/api/v1/workflows/jobs/{job_id}/retry-requests", headers=headers)

    assert response.status_code == 503, response.text
    db = job_dispatch.SessionLocal()
    try:
        retried = db.get(Job, job_id)
        assert retried.status == JOB_FAILED
        assert retried.error_code == "JOB_ENQUEUE_FAILED"
        assert "secret" not in retried.error_message
    finally:
        db.close()


def test_compose_workers_use_runtime_celery_command_and_report_worker_is_default():
    data = yaml.safe_load((REPO_ROOT / "compose.yaml").read_text())

    for service_name in (
        "worker-dxf",
        "worker-dxf2dwg",
        "worker-dxf2excel",
        "worker-dxf-classification",
        "worker-dxf-split",
        "worker-excel-final",
        "worker-report",
    ):
        command = data["services"][service_name]["command"]
        assert "uv run celery" not in command
        assert command[0] == "/app/scripts/run-worker.sh"
        if service_name in {"worker-dxf", "worker-dxf2dwg"}:
            assert command[1] == "/app/scripts/run-cad-worker.sh"
        else:
            assert "app.platform.messaging.celery_app:celery_app" in command

    assert "profiles" not in data["services"]["worker-report"]


def test_env_examples_expose_celery_eager_flag_with_consistent_keys():
    def env_keys(path: Path) -> set[str]:
        return {
            line.split("=", 1)[0]
            for line in path.read_text().splitlines()
            if line and not line.startswith("#") and "=" in line
        }

    local_keys = env_keys(REPO_ROOT / ".env.example")
    docker_keys = env_keys(REPO_ROOT / ".env.docker.example")

    assert "CELERY_TASK_ALWAYS_EAGER" in local_keys
    assert local_keys == docker_keys


def test_deployment_docs_match_mysql_derived_celery_url_behavior():
    content = (REPO_ROOT / "docs/guides/deployment.md").read_text()

    assert "direct " + chr(96) + "os.environ" not in content
    assert "从有效 MySQL DSN 派生" in content
    assert "sqla+mysql+pymysql://" in content
    assert "db+mysql+pymysql://" in content


def test_infra_docs_match_current_core_and_profile_worker_topology():
    infra = (REPO_ROOT / "infra/README.md").read_text()
    nginx = (REPO_ROOT / "infra/gateway/nginx/README.md").read_text()

    assert "worker-report" in infra
    for worker in (
        "worker-dxf",
        "worker-dxf2dwg",
        "worker-dxf2excel",
        "worker-dxf-classification",
        "worker-dxf-split",
        "worker-excel-final",
    ):
        assert worker in infra
    assert "Agent 功能保持禁用" in infra
    assert "docker compose up -d" in infra
    assert "docker compose --profile workers up -d" in infra
    for path in ("/api/v1/*", "/health*", "/docs", "/redoc", "/openapi.json"):
        assert path in nginx


def test_stub_worker_does_not_overwrite_cancelled_job():
    init_db()
    db = job_dispatch.SessionLocal()
    try:
        job = Job(
            task_type="cancelled_before_worker",
            precision_level="normal",
            pipeline="local_stub",
            status=JOB_CANCELLED,
            progress=0,
        )
        db.add(job)
        db.commit()
        job_id = job.id
    finally:
        db.close()

    run_local_stub_job(job_id)

    db = job_dispatch.SessionLocal()
    try:
        job = db.get(Job, job_id)
        assert job is not None
        assert job.status == JOB_CANCELLED
        assert db.query(AnalysisResult).filter(AnalysisResult.job_id == job_id).count() == 0
    finally:
        db.close()


def test_stub_worker_marks_job_failed_when_result_storage_fails(monkeypatch):
    init_db()
    db = job_dispatch.SessionLocal()
    try:
        job = Job(
            task_type="storage_failure",
            precision_level="normal",
            pipeline="local_stub",
            status=JOB_QUEUED,
            progress=0,
        )
        db.add(job)
        db.commit()
        job_id = job.id
    finally:
        db.close()

    def fail_storage(*args, **kwargs):
        raise AppHTTPException(503, "STORAGE_WRITE_FAILED", "storage unavailable")

    monkeypatch.setattr(job_stub, "save_bytes_as_file", fail_storage)

    try:
        run_local_stub_job(job_id)
    except AppHTTPException:
        pass

    db = job_dispatch.SessionLocal()
    try:
        job = db.get(Job, job_id)
        assert job is not None
        assert job.status == JOB_FAILED
        assert job.error_code == "STUB_WORKER_FAILED"
        assert job.error_message == "storage unavailable"
        assert db.query(AnalysisResult).filter(AnalysisResult.job_id == job_id).count() == 0
    finally:
        db.close()
