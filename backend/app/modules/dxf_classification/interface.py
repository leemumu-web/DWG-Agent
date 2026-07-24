"""Public Steel DXF classification boundary."""

from app.modules.dxf_classification.adapter import (
    CLASSIFIER_VERSION,
    CLI_SCHEMA,
    REPORT_SCHEMA,
    classifier_project_name,
)
from app.modules.dxf_classification.models import DxfClassificationItem, DxfClassificationRun
from app.modules.dxf_classification.presentation import (
    build_classification_group_page,
    build_classification_run_read,
)
from app.modules.dxf_classification.schemas import (
    DxfClassificationGroupItemRead,
    DxfClassificationGroupPage,
    DxfClassificationGroupRead,
    DxfClassificationItemRead,
    DxfClassificationRunRead,
    DxfNextStageInput,
)


def run_dxf_classification(job_id: int, **kwargs) -> None:
    from app.modules.dxf_classification.execution import run_dxf_classification as run

    run(job_id, **kwargs)


def latest_classification_run(db, workflow_id: int) -> DxfClassificationRun | None:
    from app.modules.dxf_classification.persistence import latest_classification_run as latest

    return latest(db, workflow_id)


def list_next_stage_inputs(db, workflow_id: int) -> list[DxfNextStageInput]:
    from app.modules.dxf_classification.persistence import (
        list_next_stage_inputs as list_inputs,
    )

    return list_inputs(db, workflow_id)


def enqueue_dxf_classification_job(job_id: int, attempt: int) -> str:
    from app.modules.dxf_classification.tasks import classify_steel_dxf_task

    return str(classify_steel_dxf_task.delay(job_id, attempt).id)


__all__ = [
    "CLASSIFIER_VERSION",
    "CLI_SCHEMA",
    "REPORT_SCHEMA",
    "DxfClassificationItem",
    "DxfClassificationItemRead",
    "DxfClassificationGroupItemRead",
    "DxfClassificationGroupPage",
    "DxfClassificationGroupRead",
    "DxfNextStageInput",
    "DxfClassificationRun",
    "DxfClassificationRunRead",
    "build_classification_group_page",
    "build_classification_run_read",
    "classifier_project_name",
    "enqueue_dxf_classification_job",
    "latest_classification_run",
    "list_next_stage_inputs",
    "run_dxf_classification",
]
