"""Current-attempt human review decisions for DXF split results."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.dxf_splitting.models import (
    DxfSplitItem,
    DxfSplitReviewDecision,
    DxfSplitRun,
)
from app.modules.dxf_splitting.persistence import (
    persist_review_completion_manifest,
    split_candidate_files,
)
from app.modules.dxf_splitting.schemas import (
    DxfSplitReviewDecisionRead,
    DxfSplitReviewDecisionWrite,
    DxfSplitReviewItemRead,
    DxfSplitReviewPage,
)
from app.modules.files.interface import StoredFile
from app.modules.workflows.interface import (
    WorkflowRun,
    attach_artifact,
    sync_workflow_from_jobs,
)
from app.platform.http.exceptions import AppHTTPException
from app.platform.time import business_now


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


def _candidate_files_available(db: Session, item: DxfSplitItem) -> bool:
    return split_candidate_files(db, item) is not None


def list_split_review_items(
    db: Session,
    *,
    workflow: WorkflowRun,
    run_id: int,
    page: int,
    page_size: int,
) -> DxfSplitReviewPage:
    run = _current_review_run(db, workflow=workflow, run_id=run_id)
    items = list(
        db.scalars(
            select(DxfSplitItem)
            .where(
                DxfSplitItem.run_id == run.id,
                DxfSplitItem.automation_route == "manual_review",
            )
            .options(selectinload(DxfSplitItem.review_decision))
            .order_by(DxfSplitItem.id)
        ).all()
    )
    total = len(items)
    offset = (page - 1) * page_size
    selected = items[offset : offset + page_size]
    return DxfSplitReviewPage(
        items=[
            DxfSplitReviewItemRead(
                id=item.id,
                source_name=item.source_name,
                classification_disposition=item.classification_disposition,
                classification_part_type=item.classification_part_type,
                type_resolution=item.type_resolution,
                part_type=item.part_type,
                family=item.family,
                profile_normalized=item.profile_normalized,
                disposition=item.disposition,
                diagnostics=item.diagnostics_json or [],
                decision=(
                    DxfSplitReviewDecisionRead(
                        id=item.review_decision.id,
                        split_item_id=item.review_decision.split_item_id,
                        decision=item.review_decision.decision,
                        final_normal_dxf_file_id=(
                            item.review_decision.final_normal_dxf_file_id
                        ),
                        final_weld_allowance_dxf_file_id=(
                            item.review_decision.final_weld_allowance_dxf_file_id
                        ),
                        comment=item.review_decision.comment,
                        decided_by=item.review_decision.decided_by,
                        decided_at=item.review_decision.decided_at,
                        version=item.review_decision.version,
                    )
                    if item.review_decision is not None
                    else None
                ),
            )
            for item in selected
        ],
        total=total,
        page=page,
        page_size=page_size,
        pending_count=sum(item.review_decision is None for item in items),
        manual_processing_count=sum(
            item.review_decision is not None
            and item.review_decision.decision == "manual_processing"
            for item in items
        ),
    )


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
        if not _candidate_files_available(db, item):
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
    decided_at = business_now()
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


def complete_split_review(
    db: Session,
    *,
    workflow: WorkflowRun,
    run_id: int,
    actor_id: int,
) -> DxfSplitRun:
    run = _current_review_run(db, workflow=workflow, run_id=run_id)
    items = list(
        db.scalars(
            select(DxfSplitItem)
            .where(
                DxfSplitItem.run_id == run.id,
                DxfSplitItem.automation_route == "manual_review",
            )
            .options(selectinload(DxfSplitItem.review_decision))
            .with_for_update()
        ).all()
    )
    missing = [item.id for item in items if item.review_decision is None]
    if missing:
        raise AppHTTPException(
            409,
            "DXF_SPLIT_REVIEW_INCOMPLETE",
            "仍有拆板条目尚未登记人工决定。",
            {"split_item_ids": missing, "pending_count": len(missing)},
        )
    blocked = [
        item.id
        for item in items
        if item.review_decision is not None
        and item.review_decision.decision == "manual_processing"
    ]
    if blocked:
        raise AppHTTPException(
            409,
            "DXF_SPLIT_MANUAL_PROCESSING_REQUIRED",
            "仍有图纸需要线下人工处理，不能进入自动下游。",
            {"split_item_ids": blocked, "blocked_count": len(blocked)},
        )
    stage = next(
        item for item in workflow.stages if item.stage_code == "drawing_processing"
    )
    for item in items:
        decision = item.review_decision
        if decision is None or not _candidate_files_available(db, item):
            raise AppHTTPException(
                409,
                "DXF_SPLIT_CANDIDATE_UNAVAILABLE",
                "复核采用的候选文件已不可用。",
                {"split_item_id": item.id},
            )
        item.normal_dxf_file_id = decision.final_normal_dxf_file_id
        item.weld_allowance_dxf_file_id = decision.final_weld_allowance_dxf_file_id
        item.split_report_file_id = item.candidate_split_report_file_id
        item.weld_allowance_report_file_id = (
            item.candidate_weld_allowance_report_file_id
        )
        metadata = {
            "job_id": run.job_id,
            "job_attempt": run.job_attempt,
            "run_id": run.id,
            "split_item_id": item.id,
            "classification_item_id": item.classification_item_id,
            "human_reviewed": True,
            "review_decision_id": decision.id,
        }
        for artifact_type, file_id, role in (
            ("processed_dxf", item.normal_dxf_file_id, "normal_dxf"),
            (
                "weld_allowance_dxf",
                item.weld_allowance_dxf_file_id,
                "weld_allowance_dxf",
            ),
            ("split_report", item.split_report_file_id, "split_report"),
            (
                "weld_allowance_report",
                item.weld_allowance_report_file_id,
                "weld_allowance_report",
            ),
        ):
            attach_artifact(
                db,
                workflow,
                stage_code="drawing_processing",
                artifact_type=artifact_type,
                file_id=file_id,
                metadata={**metadata, "role": role},
            )
    final_manifest = persist_review_completion_manifest(
        db,
        run=run,
        actor_user_id=actor_id,
    )
    run.status = "completed"
    run.split_manifest_file_id = final_manifest.id
    manifest_artifact = attach_artifact(
        db,
        workflow,
        stage_code="drawing_processing",
        artifact_type="split_manifest",
        file_id=final_manifest.id,
        metadata={
            "job_id": run.job_id,
            "job_attempt": run.job_attempt,
            "run_id": run.id,
            "role": "final_split_manifest",
            "final_review": True,
        },
    )
    manifest_artifact.version = 2
    stage.output_json = {
        "split_status": run.status,
        "job_id": run.job_id,
        "job_attempt": run.job_attempt,
        "reviewed_count": len(items),
    }
    db.flush()
    sync_workflow_from_jobs(db, workflow)
    return run


__all__ = [
    "complete_split_review",
    "decide_split_item",
    "list_split_review_items",
]
