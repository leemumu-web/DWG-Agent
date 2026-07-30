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
    DxfBhStage2ClassificationBatch,
    DxfBhStage2Input,
    DxfClassificationGroupItemRead,
    DxfClassificationGroupPage,
    DxfClassificationGroupRead,
    DxfClassificationItemRead,
    DxfClassificationRunRead,
    DxfNextStageInput,
    DxfSplitCandidateInput,
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


def list_split_candidate_inputs(db, workflow_id: int) -> list[DxfSplitCandidateInput]:
    from app.modules.dxf_classification.persistence import (
        list_split_candidate_inputs as list_inputs,
    )

    return list_inputs(db, workflow_id)


def load_bh_stage2_classification_batch(
    db,
    workflow_id: int,
    *,
    expected_run_id: int | None = None,
) -> DxfBhStage2ClassificationBatch:
    from app.modules.dxf_classification.persistence import (
        load_bh_stage2_classification_batch as load_batch,
    )

    return load_batch(db, workflow_id, expected_run_id=expected_run_id)


def enqueue_dxf_classification_job(job_id: int, attempt: int) -> str:
    from app.modules.dxf_classification.tasks import classify_steel_dxf_task

    return str(classify_steel_dxf_task.delay(job_id, attempt).id)


def reconcile_dxf_classification_run_for_terminal_job(
    db, *, job_id: int, attempt: int
) -> bool:
    from app.modules.dxf_classification.persistence import (
        reconcile_classification_run_for_terminal_job,
    )

    return reconcile_classification_run_for_terminal_job(
        db, job_id=job_id, attempt=attempt
    )


def reconcile_orphan_dxf_classification_runs(db) -> int:
    from app.modules.dxf_classification.persistence import (
        reconcile_orphan_classification_runs,
    )

    return reconcile_orphan_classification_runs(db)


__all__ = [
    "CLASSIFIER_VERSION",
    "CLI_SCHEMA",
    "REPORT_SCHEMA",
    "DxfClassificationItem",
    "DxfBhStage2ClassificationBatch",
    "DxfBhStage2Input",
    "DxfClassificationItemRead",
    "DxfClassificationGroupItemRead",
    "DxfClassificationGroupPage",
    "DxfClassificationGroupRead",
    "DxfNextStageInput",
    "DxfSplitCandidateInput",
    "DxfClassificationRun",
    "DxfClassificationRunRead",
    "build_classification_group_page",
    "build_classification_run_read",
    "classifier_project_name",
    "enqueue_dxf_classification_job",
    "latest_classification_run",
    "load_bh_stage2_classification_batch",
    "list_next_stage_inputs",
    "list_split_candidate_inputs",
    "run_dxf_classification",
]
