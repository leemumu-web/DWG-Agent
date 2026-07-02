from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.constants import JOB_QUEUED, JOB_RUNNING, JOB_SUCCEEDED, PIPELINE_STUB
from backend.app.db.session import SessionLocal
from backend.app.models.file import StoredFile
from backend.app.models.job import Job, JobStep
from backend.app.models.result import AnalysisResult
from backend.app.schemas.job_schema import JobCreate


def create_job(db: Session, payload: JobCreate, created_by: int | None) -> Job:
    job = Job(
        project_id=payload.project_id,
        drawing_id=payload.drawing_id,
        created_by=created_by,
        task_type=payload.task_type,
        precision_level=payload.precision_level,
        pipeline=PIPELINE_STUB,
        status=JOB_QUEUED,
        progress=0,
        params_json=payload.params,
    )
    db.add(job)
    db.flush()
    return job


def run_local_stub_job(job_id: int) -> None:
    """本阶段不接入 Celery/DXF/CAD，只用本地占位任务验证状态链路。"""
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if not job:
            return
        started_at = datetime.now(UTC)
        job.status = JOB_RUNNING
        job.progress = 20
        job.started_at = started_at
        db.add(
            JobStep(
                job_id=job.id,
                step_name="dispatch_stub_worker",
                worker_name="local_stub",
                status="succeeded",
                input_json={"pipeline": PIPELINE_STUB},
                output_json={"message": "Local no-Docker framework stub accepted the job."},
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
        )
        db.flush()

        result_payload = {
            "source": "local_stub",
            "job_id": job.id,
            "task_type": job.task_type,
            "precision_level": job.precision_level,
            "message": "Agent、DWG/DXF 与 CAD Worker 尚未接入；当前结果用于验证任务、结果、下载、审计链路。",
        }
        bucket = "dwg-derived"
        storage_key = f"local/job-{job.id}/{uuid4().hex}.json"
        path = (settings.local_storage_root / bucket / storage_key).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        import hashlib

        raw = path.read_bytes()
        result_file = StoredFile(
            bucket=bucket,
            storage_key=storage_key,
            original_name=f"job-{job.id}-result.json",
            file_ext=".json",
            content_type="application/json",
            size_bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
            md5=hashlib.md5(raw).hexdigest(),
            uploaded_by=job.created_by,
            status="available",
        )
        db.add(result_file)
        db.flush()

        result = AnalysisResult(
            job_id=job.id,
            drawing_id=job.drawing_id,
            result_type=job.task_type,
            result_json=result_payload,
            confidence=Decimal("1.0000"),
            result_file_id=result_file.id,
            algorithm_version="framework-stub-v0.1",
            tool_version="local-stub",
            status="succeeded",
        )
        db.add(result)
        db.add(
            JobStep(
                job_id=job.id,
                step_name="write_stub_result",
                worker_name="local_stub",
                status="succeeded",
                input_json={"result_file_id": result_file.id},
                output_json={"analysis_result": "created"},
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )
        )
        job.status = JOB_SUCCEEDED
        job.progress = 100
        job.finished_at = datetime.now(UTC)
        db.commit()
    finally:
        db.close()
