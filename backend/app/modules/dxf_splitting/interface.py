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
from app.modules.dxf_splitting.models import (
    DxfSplitItem,
    DxfSplitReviewDecision,
    DxfSplitRun,
)
from app.modules.dxf_splitting.presentation import build_dxf_split_run_read
from app.modules.dxf_splitting.schemas import (
    DxfSplitExcelHandoff,
    DxfSplitHandoffDrawing,
    DxfSplitItemRead,
    DxfSplitReviewDecisionRead,
    DxfSplitReviewDecisionWrite,
    DxfSplitReviewPage,
    DxfSplitRunRead,
)


def list_split_review_items(db, **kwargs) -> DxfSplitReviewPage:
    from app.modules.dxf_splitting.review import list_split_review_items as list_items

    return list_items(db, **kwargs)


def decide_split_item(db, **kwargs) -> DxfSplitReviewDecision:
    from app.modules.dxf_splitting.review import decide_split_item as decide

    return decide(db, **kwargs)


def complete_split_review(db, **kwargs) -> DxfSplitRun:
    from app.modules.dxf_splitting.review import complete_split_review as complete

    return complete(db, **kwargs)


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


def review_candidate_archive_members(db, run: DxfSplitRun) -> list[tuple[int, str]]:
    from app.modules.dxf_splitting.persistence import (
        review_candidate_archive_members as members,
    )

    return members(db, run)


def split_results_archive_members(db, run: DxfSplitRun) -> list[tuple[int, str]]:
    from app.modules.dxf_splitting.persistence import (
        split_results_archive_members as members,
    )

    return members(db, run)


def split_candidate_available(db, item: DxfSplitItem) -> bool:
    from app.modules.dxf_splitting.persistence import split_candidate_files

    return split_candidate_files(db, item) is not None


def get_excel_split_handoff(db, workflow_id: int) -> DxfSplitExcelHandoff:
    from app.modules.dxf_splitting.persistence import get_excel_split_handoff as handoff

    return handoff(db, workflow_id)


def find_dxf_split_file_workflow_id(db, file_id: int) -> int | None:
    from app.modules.dxf_splitting.persistence import (
        find_split_file_workflow_id as find_workflow_id,
    )

    return find_workflow_id(db, file_id)


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
    "DxfSplitReviewDecision",
    "DxfSplitReviewDecisionRead",
    "DxfSplitReviewDecisionWrite",
    "DxfSplitReviewPage",
    "DxfSplitRun",
    "DxfSplitRunRead",
    "build_dxf_split_run_read",
    "complete_split_review",
    "decide_split_item",
    "enqueue_dxf_splitting_job",
    "find_dxf_split_file_workflow_id",
    "get_dxf_split_outcome",
    "get_excel_split_handoff",
    "latest_dxf_split_run",
    "list_split_review_items",
    "manual_review_archive_members",
    "review_candidate_archive_members",
    "split_results_archive_members",
    "split_candidate_available",
    "run_dxf_splitting",
]
