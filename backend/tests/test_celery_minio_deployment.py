from __future__ import annotations

import tomllib
from io import BytesIO
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

import app.services.job_service as job_service
from app.core.constants import JOB_CANCELLED, JOB_FAILED, JOB_QUEUED
from app.core.exceptions import AppHTTPException
from app.db.init_db import init_db
from app.main import app
from app.models.job import Job
from app.models.result import AnalysisResult
from app.services.job_service import run_local_stub_job

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_dependencies_include_celery_and_minio_without_flower():
    pyproject = tomllib.loads((REPO_ROOT / "backend/pyproject.toml").read_text())
    deps = "\n".join(pyproject["project"]["dependencies"])

    assert "celery" in deps
    assert "minio" in deps
    assert "flower" not in deps


def test_minio_storage_backend_creates_bucket_and_streams_objects():
    from app.storage.minio_storage import MinioStorage

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
    content = (REPO_ROOT / "backend/app/api/v1/files_api.py").read_text()

    assert "get_local_file_path" not in content
    assert "StreamingResponse" in content
    assert "get_storage_backend" in content


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
        "/api/v1/projects",
        headers=headers,
        json={"code": "RESULT-DOWNLOAD", "name": "Result Download"},
    )
    assert project.status_code == 201, project.text

    job = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={
            "project_id": project.json()["data"]["id"],
            "task_type": "framework_smoke_test",
        },
    )
    assert job.status_code == 202, job.text
    job_id = job.json()["data"]["id"]

    run_local_stub_job(job_id)

    results = client.get(f"/api/v1/jobs/{job_id}/results", headers=headers)
    assert results.status_code == 200, results.text
    result_id = results.json()["data"][0]["id"]

    download_url = client.get(f"/api/v1/results/{result_id}/download-url", headers=headers)
    assert download_url.status_code == 200, download_url.text
    url = download_url.json()["data"]["url"]
    assert "expires=" in url
    assert "signature=" in url

    download = client.get(url, headers=headers)
    assert download.status_code == 200, download.text
    assert b"local_stub" in download.content


def test_celery_app_registers_stage1_stub_task():
    from app.core.config import settings
    from app.workers.celery_app import celery_app

    assert celery_app.conf.broker_url == settings.celery_broker_url
    assert celery_app.conf.result_backend == settings.celery_result_backend
    assert "app.workers.tasks_report.run_stub_job" in celery_app.tasks


def test_mysql_result_rows_have_bounded_retention():
    from app.workers.celery_app import celery_app

    assert celery_app.conf.result_expires == 24 * 60 * 60


def test_worker_startup_cleans_expired_mysql_result_rows():
    from app.workers import celery_app as celery_module

    calls: list[str] = []

    class FakeBackend:
        def cleanup(self) -> None:
            calls.append("cleanup")

    class FakeApp:
        backend = FakeBackend()

    celery_module.cleanup_expired_task_results(FakeApp())

    assert calls == ["cleanup"]


def test_jobs_api_enqueues_celery_task_not_fastapi_background_task():
    content = (REPO_ROOT / "backend/app/api/v1/jobs_api.py").read_text()

    assert "BackgroundTasks" not in content
    assert "background_tasks.add_task" not in content
    assert "enqueue_job" in content


def test_job_create_marks_job_failed_when_celery_dispatch_fails(monkeypatch):
    from app.api.v1 import jobs_api

    init_db()
    client = TestClient(app, raise_server_exceptions=False)
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert login.status_code == 201, login.text
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"code": "CELERY-DOWN", "name": "Celery Down"},
    )
    assert project.status_code == 201, project.text

    def fail_enqueue(job_id: int, pipeline: str) -> str:
        raise RuntimeError("mysql broker unavailable")

    monkeypatch.setattr(jobs_api, "enqueue_job", fail_enqueue)

    response = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={
            "project_id": project.json()["data"]["id"],
            "task_type": "framework_smoke_test",
            "precision_level": "normal",
        },
    )

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "JOB_ENQUEUE_FAILED"

    db = job_service.SessionLocal()
    try:
        job = db.query(Job).order_by(Job.id.desc()).first()
        assert job is not None
        assert job.status == JOB_FAILED
        assert job.error_code == "JOB_ENQUEUE_FAILED"
        assert job.error_message == "mysql broker unavailable"
    finally:
        db.close()


def test_compose_workers_use_runtime_celery_command_and_report_worker_is_default():
    data = yaml.safe_load((REPO_ROOT / "compose.yaml").read_text())

    for service_name in (
        "worker-agent",
        "worker-dxf",
        "worker-dxf2dwg",
        "worker-dxf2excel",
        "worker-report",
    ):
        command = data["services"][service_name]["command"]
        assert "uv run celery" not in command
        assert "app.workers.celery_app:celery_app" in command

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
    content = (REPO_ROOT / "docs/deployment.md").read_text()

    assert "direct " + chr(96) + "os.environ" not in content
    assert "derived from the effective MySQL DSN" in content
    assert "sqla+mysql+pymysql://" in content
    assert "db+mysql+pymysql://" in content


def test_infra_docs_show_worker_report_as_default_and_profile_workers_as_deferred():
    infra = (REPO_ROOT / "infra/README.md").read_text()
    nginx = (REPO_ROOT / "infra/nginx/README.md").read_text()

    assert "Celery worker-report" in infra
    assert "docker compose up -d worker-report" in infra
    assert "Agent/DXF workers" in infra
    assert "docker compose --profile workers up -d" in infra
    assert "核心服务 + worker-report" in nginx
    assert "Agent/DXF placeholder workers" in nginx


def test_stub_worker_does_not_overwrite_cancelled_job():
    init_db()
    db = job_service.SessionLocal()
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

    db = job_service.SessionLocal()
    try:
        job = db.get(Job, job_id)
        assert job is not None
        assert job.status == JOB_CANCELLED
        assert db.query(AnalysisResult).filter(AnalysisResult.job_id == job_id).count() == 0
    finally:
        db.close()


def test_stub_worker_marks_job_failed_when_result_storage_fails(monkeypatch):
    init_db()
    db = job_service.SessionLocal()
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

    monkeypatch.setattr(job_service, "save_bytes_as_file", fail_storage)

    try:
        run_local_stub_job(job_id)
    except AppHTTPException:
        pass

    db = job_service.SessionLocal()
    try:
        job = db.get(Job, job_id)
        assert job is not None
        assert job.status == JOB_FAILED
        assert job.error_code == "STUB_WORKER_FAILED"
        assert job.error_message == "storage unavailable"
        assert db.query(AnalysisResult).filter(AnalysisResult.job_id == job_id).count() == 0
    finally:
        db.close()
