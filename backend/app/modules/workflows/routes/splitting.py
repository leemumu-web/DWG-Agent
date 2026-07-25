"""DXF split run projection, human review and ZIP-only exports."""

import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.modules.dxf_splitting.interface import (
    DxfSplitReviewDecisionRead,
    DxfSplitReviewDecisionWrite,
    build_dxf_split_run_read,
    complete_split_review,
    decide_split_item,
    latest_dxf_split_run,
    list_split_review_items,
    manual_review_archive_members,
    review_candidate_archive_members,
    split_candidate_available,
    split_results_archive_members,
)
from app.modules.identity.interface import CurrentUser
from app.modules.operations.audit.interface import write_audit_log
from app.modules.projects.interface import require_project_member, require_project_role
from app.modules.workflows.access import WORKFLOW_WRITE_ROLES, load_workflow_detail
from app.modules.workflows.job_sync import sync_workflow_from_jobs
from app.modules.workflows.routes.archive import stream_registered_workflow_archive
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.exceptions import AppHTTPException

router = APIRouter()


def _is_current_drawing_attempt(workflow, run) -> bool:
    stage = next(
        (item for item in workflow.stages if item.stage_code == "drawing_processing"),
        None,
    )
    return bool(
        stage is not None
        and run is not None
        and stage.job_id == run.job_id
        and stage.job_attempt == run.job_attempt
    )


def _current_split_run_or_404(db: Session, workflow, run_id: int):
    run = latest_dxf_split_run(db, workflow.id)
    if not _is_current_drawing_attempt(workflow, run) or run is None or run.id != run_id:
        raise AppHTTPException(
            404,
            "DXF_SPLIT_RUN_NOT_CURRENT",
            "请求的拆板批次不是工作流当前 attempt。",
        )
    return run


@router.get(
    "/{workflow_id}/drawing-processing",
    summary="读取当前拆板批次",
    description="只返回工作流当前 Job attempt 的拆板、独立校验和人工复核汇总。",
)
def get_drawing_processing(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    sync_workflow_from_jobs(db, workflow)
    run = latest_dxf_split_run(db, workflow.id)
    db.commit()
    if not _is_current_drawing_attempt(workflow, run):
        return ok(None, request.state.request_id)
    return ok(build_dxf_split_run_read(db, run), request.state.request_id)


@router.get(
    "/{workflow_id}/drawing-processing/runs/{run_id}/manual-review-archive",
    summary="下载本批次未通过原图",
    response_class=StreamingResponse,
    description=(
        "即时生成 ZIP，只包含当前拆板 attempt 中进入 manual_review 的分类原始 DXF；"
        "不包含候选图、报告、预览或 Excel。"
    ),
)
def download_manual_review_archive(
    workflow_id: int,
    run_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    run = _current_split_run_or_404(db, workflow, run_id)
    if run.status != "completed_with_review":
        raise AppHTTPException(
            409,
            "DXF_SPLIT_REVIEW_ARCHIVE_UNAVAILABLE",
            "当前拆板批次没有待人工处理图纸。",
            {"split_run_id": run.id, "status": run.status},
        )
    members = manual_review_archive_members(db, run)
    if not members:
        raise AppHTTPException(
            409,
            "DXF_SPLIT_REVIEW_ARCHIVE_EMPTY",
            "当前拆板批次没有可下载的未通过原图。",
        )
    # Project membership plus current run/attempt lineage is the authority here.
    # Classified outputs are server-generated and may have a different uploader.
    return stream_registered_workflow_archive(
        db,
        request,
        current_user,
        workflow,
        members,
        f"workflow-{workflow.id}-split-run-{run.id}-manual-review",
        operation="dxf_split_manual_review_zip",
        audit_action="dxf_split_manual_review_archives.download",
    )


@router.get(
    "/{workflow_id}/drawing-processing/runs/{run_id}/review-items",
    summary="分页读取拆板复核条目",
    description="只读取当前拆板 attempt 的人工复核条目、候选可用性和已登记决定。",
)
def get_split_review_items(
    workflow_id: int,
    run_id: int,
    request: Request,
    current_user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    result = list_split_review_items(
        db,
        workflow=workflow,
        run_id=run_id,
        page=page,
        page_size=page_size,
    )
    return ok(result, request.state.request_id)


@router.put(
    "/{workflow_id}/drawing-processing/runs/{run_id}/review-items/{item_id}/decision",
    summary="登记拆板人工复核决定",
    description="采用成对候选文件，或标记为仍需线下人工处理；使用版本号防止覆盖并发修改。",
)
def put_split_review_decision(
    workflow_id: int,
    run_id: int,
    item_id: int,
    payload: DxfSplitReviewDecisionWrite,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id, for_update=True)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    decision = decide_split_item(
        db,
        workflow=workflow,
        run_id=run_id,
        item_id=item_id,
        actor_id=current_user.id,
        payload=payload,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="dxf_split_review_decisions.upsert",
        resource_type="dxf_split_item",
        resource_id=item_id,
        after_json={
            "split_run_id": run_id,
            "decision": decision.decision,
            "version": decision.version,
        },
        request=request,
    )
    db.commit()
    db.refresh(decision)
    return ok(
        DxfSplitReviewDecisionRead.model_validate(decision),
        request.state.request_id,
    )


@router.post(
    "/{workflow_id}/drawing-processing/runs/{run_id}/review-completion",
    summary="完成拆板人工复核",
    description="仅当全部条目已决定且不存在待线下处理项时，固化正式 DXF 并进入 Excel 阶段。",
)
def complete_split_review_api(
    workflow_id: int,
    run_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id, for_update=True)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    run = complete_split_review(
        db,
        workflow=workflow,
        run_id=run_id,
        actor_id=current_user.id,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="dxf_split_reviews.complete",
        resource_type="dxf_split_run",
        resource_id=run.id,
        after_json={
            "status": run.status,
            "job_attempt": run.job_attempt,
            "workflow_stage": workflow.current_stage,
        },
        request=request,
    )
    db.commit()
    run = latest_dxf_split_run(db, workflow.id)
    return ok(build_dxf_split_run_read(db, run), request.state.request_id)


@router.get(
    "/{workflow_id}/drawing-processing/runs/{run_id}/review-candidates-archive",
    summary="下载拆板候选复核包",
    response_class=StreamingResponse,
    description="ZIP 包含原始 DXF、可用候选 DXF、候选报告和诊断清单，不提供单文件下载。",
)
def download_split_review_candidates_archive(
    workflow_id: int,
    run_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    run = _current_split_run_or_404(db, workflow, run_id)
    if run.status != "completed_with_review":
        raise AppHTTPException(
            409,
            "DXF_SPLIT_REVIEW_ARCHIVE_UNAVAILABLE",
            "当前拆板批次不处于人工复核状态。",
            {"split_run_id": run.id, "status": run.status},
        )
    members = review_candidate_archive_members(db, run)
    manifest = {
        "schema": "cad.dxf_split.review_candidates.v1",
        "workflow_id": workflow.id,
        "split_run_id": run.id,
        "job_attempt": run.job_attempt,
        "items": [
            {
                "split_item_id": item.id,
                "source_name": item.source_name,
                "part_type": item.part_type,
                "disposition": item.disposition,
                "diagnostics": item.diagnostics_json or [],
                "candidate_available": split_candidate_available(db, item),
            }
            for item in run.items
            if item.automation_route == "manual_review"
        ],
    }
    return stream_registered_workflow_archive(
        db,
        request,
        current_user,
        workflow,
        members,
        f"workflow-{workflow.id}-split-run-{run.id}-review-candidates",
        operation="dxf_split_review_candidates_zip",
        audit_action="dxf_split_review_candidate_archives.download",
        inline_members={
            "review-manifest.json": json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
        },
    )


@router.get(
    "/{workflow_id}/drawing-processing/runs/{run_id}/results-archive",
    summary="下载拆板正式结果包",
    response_class=StreamingResponse,
    description="只在拆板完成后生成正式 DXF、报告和批次账本 ZIP，不提供单文件下载。",
)
def download_split_results_archive(
    workflow_id: int,
    run_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    run = _current_split_run_or_404(db, workflow, run_id)
    members = split_results_archive_members(db, run)
    return stream_registered_workflow_archive(
        db,
        request,
        current_user,
        workflow,
        members,
        f"workflow-{workflow.id}-split-run-{run.id}-results",
        operation="dxf_split_results_zip",
        audit_action="dxf_split_result_archives.download",
    )
