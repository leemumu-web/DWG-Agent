"""FastAPI routes for plate classification."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.modules.plate_classification import models as plate_models
from app.modules.plate_classification.schemas import (
    PlateClassificationItemRead,
    PlateClassificationRunPage,
    PlateClassificationRunRead,
    PlateClassificationTriggerRequest,
)
from app.platform.database.session import get_db

router = APIRouter()


@router.post("/plate-classification/runs", response_model=dict)
def trigger_plate_classification(
    body: PlateClassificationTriggerRequest,
    db=Depends(get_db),
):
    """触发板件分类任务。创建运行记录并入队 Celery 任务。"""
    from app.modules.jobs.interface import Job

    # Create job
    job = Job(
        task_type="plate_classification",
        precision_level="normal",
        status="queued",
        attempt=1,
    )
    db.add(job)
    db.flush()

    # Create run
    run = plate_models.PlateClassificationRun(
        workflow_run_id=body.workflow_run_id,
        project_id=body.project_id,
        job_id=job.id,
        job_attempt=1,
        status="pending",
        project_name=body.project_name,
        input_directory=body.input_directory,
    )
    db.add(run)
    db.commit()

    # Enqueue Celery task
    from app.modules.plate_classification.tasks import classify_plates_task

    classify_plates_task.apply_async(
        kwargs={"job_id": job.id, "attempt": 1},
        queue="dxf_classification",
    )

    return {"run_id": run.id, "job_id": job.id, "status": "pending"}


@router.get(
    "/plate-classification/runs",
    response_model=PlateClassificationRunPage,
)
def list_plate_classification_runs(
    page: int = 1,
    page_size: int = 20,
    db=Depends(get_db),
):
    """列出分类运行历史。"""
    query = db.query(plate_models.PlateClassificationRun).order_by(
        plate_models.PlateClassificationRun.id.desc()
    )
    total = query.count()
    runs = query.offset((page - 1) * page_size).limit(page_size).all()

    items = []
    for run in runs:
        items.append(
            PlateClassificationRunRead(
                id=run.id,
                workflow_run_id=run.workflow_run_id,
                status=run.status,
                classifier_version=run.classifier_version,
                project_name=run.project_name,
                input_directory=run.input_directory,
                input_count=run.input_count,
                classified_count=run.classified_count,
                category_counts=run.category_counts_json,
                error_code=run.error_code,
                error_message=run.error_message,
                started_at=run.started_at,
                finished_at=run.finished_at,
                created_at=run.created_at,
                updated_at=run.updated_at,
                items=[
                    PlateClassificationItemRead(
                        id=item.id,
                        part_name=item.part_name,
                        dxf_file=item.dxf_file,
                        category=item.category,
                        shape=item.shape,
                        hole=item.hole,
                        bend=item.bend,
                    )
                    for item in run.items
                ],
            )
        )

    return PlateClassificationRunPage(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get(
    "/plate-classification/runs/{run_id}",
    response_model=PlateClassificationRunRead,
)
def get_plate_classification_run(
    run_id: int,
    db=Depends(get_db),
):
    """获取单次分类运行详情。"""
    run = db.query(plate_models.PlateClassificationRun).filter_by(id=run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    return PlateClassificationRunRead(
        id=run.id,
        workflow_run_id=run.workflow_run_id,
        status=run.status,
        classifier_version=run.classifier_version,
        project_name=run.project_name,
        input_directory=run.input_directory,
        input_count=run.input_count,
        classified_count=run.classified_count,
        category_counts=run.category_counts_json,
        error_code=run.error_code,
        error_message=run.error_message,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
        items=[
            PlateClassificationItemRead(
                id=item.id,
                part_name=item.part_name,
                dxf_file=item.dxf_file,
                category=item.category,
                shape=item.shape,
                hole=item.hole,
                bend=item.bend,
            )
            for item in run.items
        ],
    )
