"""Celery task for plate classification (yikongzhe stage)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.modules.plate_classification.execution import run_plate_classification
from app.modules.plate_classification.models import (
    PlateClassificationItem,
    PlateClassificationRun,
)
from app.modules.jobs.interface import read_job, update_job_status
from app.platform.database.session import SessionLocal
from app.platform.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.workers.tasks_plate_classification.classify_plates",
    bind=True,
)
def classify_plates_task(self, job_id: int, attempt: int = 1) -> dict:
    """对拆板后 DXF 目录执行异孔折判断分类。

    Args:
        job_id: 任务 ID。
        attempt: 任务尝试次数。

    Returns:
        执行摘要。
    """
    db = SessionLocal()
    try:
        job = read_job(db, job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")

        # Get run record
        run = (
            db.query(PlateClassificationRun)
            .filter_by(job_id=job_id, job_attempt=attempt)
            .first()
        )
        if run is None:
            raise ValueError(
                f"PlateClassificationRun not found for job={job_id} attempt={attempt}"
            )

        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        db.commit()

        worker_name = self.request.hostname or "celery_plate_classification"
        logger.info(
            "[%s] Starting plate classification for job=%d, dir=%s",
            worker_name,
            job_id,
            run.input_directory,
        )

        # Execute classification
        result = run_plate_classification(
            input_dir=run.input_directory,
            output_path=f"/tmp/plate_classification_{job_id}_{attempt}.xlsx",
        )

        # Update run with results
        run.status = "completed"
        run.input_count = result.get("dxf_count", 0)
        run.classified_count = result.get("total_parts", 0)
        run.finished_at = datetime.now(timezone.utc)
        db.commit()

        logger.info(
            "[%s] Plate classification complete: %d DXF, %d parts",
            worker_name,
            run.input_count,
            run.classified_count,
        )

        return {
            "job_id": job_id,
            "status": "completed",
            "dxf_count": run.input_count,
            "total_parts": run.classified_count,
        }

    except Exception as exc:
        logger.exception("Plate classification job=%d failed: %s", job_id, exc)
        try:
            run = (
                db.query(PlateClassificationRun)
                .filter_by(job_id=job_id, job_attempt=attempt)
                .first()
            )
            if run:
                run.status = "failed"
                run.error_message = str(exc)
                run.finished_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            pass
        raise
    finally:
        db.close()
