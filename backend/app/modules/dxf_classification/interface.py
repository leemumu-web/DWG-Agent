"""Public Steel DXF classification boundary.

Calling contract (CONTEXT.md: Interface must document invariants, error
modes, ordering and configuration):

- ``run_dxf_classification(job_id)`` is the worker-side execution entry:
  it invokes the Steel DXF Classifier CLI through the adapter, enforces the
  immutable-source invariant (classified output must be byte-identical to
  the frozen input DXF), and records run/items in the classification
  ledger. Enqueue first (``enqueue_dxf_classification_job``), then execute.
- Batch handoff: ``load_bh_stage2_classification_batch`` /
  ``load_box_stage2_classification_batch`` return immutable stage-2 input
  batches from the classification ledger; they raise ``ClassificationError``
  when ``expected_run_id`` does not match the current run or the run is not
  completed. The returned manifest carries ``bh_manifest_version`` and a
  SHA-256 over the canonical manifest lines — consumers must verify it
  before use and must not mutate the batch.
- Reconcilers: ``reconcile_dxf_classification_run_for_terminal_job`` closes
  the projection only for the exact (job_id, attempt) generation;
  ``reconcile_orphan_dxf_classification_runs`` closes projections whose Job
  is no longer active. Both are read-repair boundaries, not job writers.
- Versioning: ``CLASSIFIER_VERSION`` is the authoritative Stage version
  written into every run; keep model defaults and README in sync with it.
"""

from app.modules.dxf_classification.adapter import (
    CLASSIFIER_VERSION,
    CLI_SCHEMA,
    REPORT_SCHEMA,
    ClassificationError,
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
    """Execute one classification Job (worker side, fenced by status + attempt)."""
    from app.modules.dxf_classification.execution import run_dxf_classification as run

    run(job_id, **kwargs)


def latest_classification_run(db, workflow_id: int) -> DxfClassificationRun | None:
    """Return the most recent classification run for a workflow, if any."""
    from app.modules.dxf_classification.persistence import latest_classification_run as latest

    return latest(db, workflow_id)


def list_next_stage_inputs(db, workflow_id: int) -> list[DxfNextStageInput]:
    """List frozen DXF inputs routed to the next stage for a workflow."""
    from app.modules.dxf_classification.persistence import (
        list_next_stage_inputs as list_inputs,
    )

    return list_inputs(db, workflow_id)


def list_split_candidate_inputs(db, workflow_id: int) -> list[DxfSplitCandidateInput]:
    """List frozen DXF inputs routed to the split stage for a workflow."""
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
    """Load the immutable BH stage-2 input batch for a workflow.

    Raises ClassificationError when the current run id does not match
    ``expected_run_id`` or the run is not completed; the batch manifest
    must be verified by its SHA-256 before use.
    """
    from app.modules.dxf_classification.persistence import (
        load_bh_stage2_classification_batch as load_batch,
    )

    return load_batch(db, workflow_id, expected_run_id=expected_run_id)


def load_box_stage2_classification_batch(
    db,
    workflow_id: int,
    *,
    expected_run_id: int | None = None,
) -> DxfBhStage2ClassificationBatch:
    """Load the immutable BOX stage-2 input batch for a workflow.

    Same contract as ``load_bh_stage2_classification_batch``; the manifest
    is verified by its SHA-256 before use.
    """
    from app.modules.dxf_classification.persistence import (
        load_box_stage2_classification_batch as load_batch,
    )

    return load_batch(db, workflow_id, expected_run_id=expected_run_id)


def enqueue_dxf_classification_job(
    job_id: int, attempt: int, *, task_id: str | None = None
) -> str:
    """Dispatch one classification Job to Celery; returns the task id."""
    from app.modules.dxf_classification.tasks import classify_steel_dxf_task

    return str(
        classify_steel_dxf_task.apply_async(
            args=[job_id, attempt], task_id=task_id
        ).id
    )


def reconcile_dxf_classification_run_for_terminal_job(
    db, *, job_id: int, attempt: int
) -> bool:
    """Close the classification projection for one exact Job attempt."""
    from app.modules.dxf_classification.persistence import (
        reconcile_classification_run_for_terminal_job,
    )

    return reconcile_classification_run_for_terminal_job(
        db, job_id=job_id, attempt=attempt
    )


def reconcile_orphan_dxf_classification_runs(db) -> int:
    """Close classification projections whose Job is no longer active.

    Read-repair boundary; returns the number of runs closed.
    """
    from app.modules.dxf_classification.persistence import (
        reconcile_orphan_classification_runs,
    )

    return reconcile_orphan_classification_runs(db)


__all__ = [
    "CLASSIFIER_VERSION",
    "CLI_SCHEMA",
    "ClassificationError",
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
