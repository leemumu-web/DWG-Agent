from __future__ import annotations

import tomllib
from io import BytesIO
from pathlib import Path

import yaml

import app.services.job_service as job_service
from app.core.constants import JOB_CANCELLED
from app.db.init_db import init_db
from app.models.job import Job
from app.models.result import AnalysisResult
from app.services.job_service import run_local_stub_job

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_dependencies_include_celery_flower_and_minio():
    pyproject = tomllib.loads((REPO_ROOT / "backend/pyproject.toml").read_text())
    deps = "\n".join(pyproject["project"]["dependencies"])

    assert "celery" in deps
    assert "flower" in deps
    assert "minio" in deps


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


def test_celery_app_registers_stage1_stub_task():
    from app.core.config import settings
    from app.workers.celery_app import celery_app

    assert celery_app.conf.broker_url == settings.celery_broker_url
    assert celery_app.conf.result_backend == settings.celery_result_backend
    assert "app.workers.tasks_report.run_stub_job" in celery_app.tasks


def test_jobs_api_enqueues_celery_task_not_fastapi_background_task():
    content = (REPO_ROOT / "backend/app/api/v1/jobs_api.py").read_text()

    assert "BackgroundTasks" not in content
    assert "background_tasks.add_task" not in content
    assert "enqueue_stub_job" in content


def test_compose_workers_use_runtime_celery_command_and_report_worker_is_default():
    data = yaml.safe_load((REPO_ROOT / "compose.yaml").read_text())

    for service_name in ("worker-agent", "worker-dxf", "worker-report", "flower"):
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
