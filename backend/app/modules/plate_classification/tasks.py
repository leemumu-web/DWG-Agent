"""Celery task for plate classification (yikongzhe stage)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.modules.jobs.interface import Job, summarize_job_execution
from app.modules.plate_classification.execution import run_plate_classification
from app.modules.plate_classification.models import (
    PlateClassificationRun,
)
from app.platform.database.session import SessionLocal
from app.platform.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.workers.tasks_plate_classification.classify_plates",
    bind=True,
)
def classify_plates_task(self, job_id: int, attempt: int = 1) -> dict[str, int | str]:
    """对拆板后 DXF 目录执行异孔折判断分类。"""
    worker_name = self.request.hostname or "celery_plate_classification"

    _run_plate_classification_job(job_id, attempt, worker_name)
    return summarize_job_execution(job_id, "steel_plate_classifier")


def _run_plate_classification_job(
    job_id: int, attempt: int, worker_name: str
) -> None:
    db = SessionLocal()
    try:
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

        logger.info(
            "[%s] Starting plate classification for job=%d, dir=%s",
            worker_name,
            job_id,
            run.input_directory,
        )

        result = run_plate_classification(
            input_dir=run.input_directory,
            output_path=f"/tmp/plate_classification_{job_id}_{attempt}.xlsx",
        )

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
