"""Public DXF split boundary for workflows, HTTP and Excel processing."""

from app.modules.dxf_splitting.adapter import (
    BH_SOURCE_CONTRACT,
    BOX_SOURCE_CONTRACT,
    CLI_SCHEMA,
    MANIFEST_SCHEMA,
    MAX_AUTOMATIC_ATTEMPTS,
    SPLITTER_VERSION,
    VALIDATION_SCHEMA,
    DxfSplitError,
)
from app.modules.dxf_splitting.models import DxfSplitItem, DxfSplitRun
from app.modules.dxf_splitting.presentation import build_dxf_split_run_read
from app.modules.dxf_splitting.schemas import (
    DxfSplitExcelHandoff,
    DxfSplitHandoffDrawing,
    DxfSplitItemRead,
    DxfSplitRunRead,
)


def run_dxf_splitting(job_id: int, **kwargs) -> None:
    from app.modules.dxf_splitting.execution import run_dxf_splitting as run

    run(job_id, **kwargs)


def enqueue_dxf_splitting_job(job_id: int, attempt: int) -> str:
    from app.modules.dxf_splitting.tasks import split_steel_dxf_task

    return str(split_steel_dxf_task.delay(job_id, attempt).id)


def latest_dxf_split_run(db, workflow_id: int) -> DxfSplitRun | None:
    from app.modules.dxf_splitting.persistence import latest_split_run

    return latest_split_run(db, workflow_id)


def get_dxf_split_outcome(db, *, job_id: int, attempt: int) -> str | None:
    from app.modules.dxf_splitting.persistence import get_split_outcome

    return get_split_outcome(db, job_id=job_id, attempt=attempt)


def manual_review_archive_members(db, run: DxfSplitRun) -> list[tuple[int, str]]:
    from app.modules.dxf_splitting.persistence import manual_review_archive_members as members

    return members(db, run)


def get_excel_split_handoff(db, workflow_id: int) -> DxfSplitExcelHandoff:
    from app.modules.dxf_splitting.persistence import get_excel_split_handoff as handoff

    return handoff(db, workflow_id)


__all__ = [
    "BH_SOURCE_CONTRACT",
    "BOX_SOURCE_CONTRACT",
    "CLI_SCHEMA",
    "MANIFEST_SCHEMA",
    "MAX_AUTOMATIC_ATTEMPTS",
    "SPLITTER_VERSION",
    "VALIDATION_SCHEMA",
    "DxfSplitError",
    "DxfSplitExcelHandoff",
    "DxfSplitHandoffDrawing",
    "DxfSplitItem",
    "DxfSplitItemRead",
    "DxfSplitRun",
    "DxfSplitRunRead",
    "build_dxf_split_run_read",
    "enqueue_dxf_splitting_job",
    "get_dxf_split_outcome",
    "get_excel_split_handoff",
    "latest_dxf_split_run",
    "manual_review_archive_members",
    "run_dxf_splitting",
]
