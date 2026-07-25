"""Current-attempt human review decisions for DXF split results."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.dxf_splitting.models import (
    DxfSplitItem,
    DxfSplitReviewDecision,
    DxfSplitRun,
)
from app.modules.dxf_splitting.schemas import DxfSplitReviewDecisionWrite
from app.modules.files.interface import StoredFile
from app.modules.workflows.interface import WorkflowRun
from app.platform.http.exceptions import AppHTTPException


def _current_review_run(
    db: Session,
    *,
    workflow: WorkflowRun,
    run_id: int,
) -> DxfSplitRun:
    run = db.scalar(
        select(DxfSplitRun).where(DxfSplitRun.id == run_id).with_for_update()
    )
    stage = next(
        (
            item
            for item in workflow.stages
            if item.stage_code == "drawing_processing"
        ),
        None,
    )
    if (
        run is None
        or run.workflow_run_id != workflow.id
        or workflow.current_stage != "drawing_processing"
        or stage is None
        or stage.job_id != run.job_id
        or stage.job_attempt != run.job_attempt
    ):
        raise AppHTTPException(
            409,
            "DXF_SPLIT_RUN_NOT_CURRENT",
            "请求的拆板批次不是工作流当前 attempt。",
        )
    if run.status != "completed_with_review":
        raise AppHTTPException(
            409,
            "DXF_SPLIT_REVIEW_NOT_ACTIVE",
            "当前拆板批次不处于人工复核状态。",
            {"split_run_id": run.id, "status": run.status},
        )
    return run


def _available_dxf(db: Session, file_id: int | None) -> StoredFile | None:
    stored = db.get(StoredFile, file_id) if file_id is not None else None
    if (
        stored is None
        or stored.status == "deleted"
        or stored.file_ext.casefold() != ".dxf"
    ):
        return None
    return stored


def decide_split_item(
    db: Session,
    *,
    workflow: WorkflowRun,
    run_id: int,
    item_id: int,
    actor_id: int,
    payload: DxfSplitReviewDecisionWrite,
) -> DxfSplitReviewDecision:
    run = _current_review_run(db, workflow=workflow, run_id=run_id)
    item = db.scalar(
        select(DxfSplitItem)
        .where(DxfSplitItem.id == item_id, DxfSplitItem.run_id == run.id)
        .with_for_update()
    )
    if item is None:
        raise AppHTTPException(
            404,
            "DXF_SPLIT_REVIEW_ITEM_NOT_FOUND",
            "当前拆板批次不存在该复核条目。",
        )
    if item.automation_route != "manual_review":
        raise AppHTTPException(
            409,
            "DXF_SPLIT_REVIEW_ITEM_NOT_REQUIRED",
            "自动通过的拆板条目不允许登记人工决定。",
            {"split_item_id": item.id},
        )

    normal_file_id = None
    allowance_file_id = None
    if payload.decision == "accept_candidate":
        normal = _available_dxf(db, item.candidate_normal_dxf_file_id)
        allowance = _available_dxf(db, item.candidate_weld_allowance_dxf_file_id)
        if normal is None or allowance is None or normal.id == allowance.id:
            raise AppHTTPException(
                409,
                "DXF_SPLIT_CANDIDATE_UNAVAILABLE",
                "该条目没有可供人工采用的成对候选 DXF。",
                {"split_item_id": item.id},
            )
        normal_file_id = normal.id
        allowance_file_id = allowance.id

    decision = db.scalar(
        select(DxfSplitReviewDecision)
        .where(DxfSplitReviewDecision.split_item_id == item.id)
        .with_for_update()
    )
    if (
        decision is not None
        and decision.decision == payload.decision
        and decision.comment == payload.comment
        and decision.final_normal_dxf_file_id == normal_file_id
        and decision.final_weld_allowance_dxf_file_id == allowance_file_id
    ):
        return decision
    current_version = decision.version if decision is not None else 0
    if payload.expected_version != current_version:
        raise AppHTTPException(
            409,
            "DXF_SPLIT_REVIEW_VERSION_CONFLICT",
            "复核决定已被其他操作更新，请刷新后重试。",
            {
                "split_item_id": item.id,
                "expected_version": payload.expected_version,
                "current_version": current_version,
            },
        )
    decided_at = datetime.now(UTC)
    if decision is None:
        decision = DxfSplitReviewDecision(
            split_item_id=item.id,
            decision=payload.decision,
            final_normal_dxf_file_id=normal_file_id,
            final_weld_allowance_dxf_file_id=allowance_file_id,
            comment=payload.comment,
            decided_by=actor_id,
            decided_at=decided_at,
            version=1,
        )
        db.add(decision)
    else:
        decision.decision = payload.decision
        decision.final_normal_dxf_file_id = normal_file_id
        decision.final_weld_allowance_dxf_file_id = allowance_file_id
        decision.comment = payload.comment
        decision.decided_by = actor_id
        decision.decided_at = decided_at
        decision.version += 1
    db.flush()
    return decision


__all__ = ["decide_split_item"]
