"""Workflow state-machine transitions independent from HTTP transport."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.projects.interface import Project
from app.modules.workflows.contracts import (
    require_stage_inputs,
    require_stage_outputs,
    verify_required_dxf_objects,
)
from app.modules.workflows.models import WorkflowRun, WorkflowStageRun
from app.modules.workflows.schemas import WorkflowCreate
from app.modules.workflows.templates import WORKFLOW_DEFINITIONS, get_stage_capability
from app.platform.http.exceptions import AppHTTPException, not_found
from app.platform.time import business_now

WORKFLOW_TERMINAL = {"succeeded", "failed", "cancelled"}
STAGE_TERMINAL = {"succeeded", "failed", "cancelled", "skipped"}
STAGE_ACTIVE = {"queued", "running"}


def create_workflow(db: Session, payload: WorkflowCreate, *, created_by: int) -> WorkflowRun:
    config = dict(payload.config)
    if payload.workflow_type == "linux_production":
        project = db.scalar(
            select(Project).where(Project.id == payload.project_id).with_for_update()
        )
        if project is None or project.status == "deleted":
            raise not_found("Project")
        existing = db.scalar(
            select(WorkflowRun)
            .where(
                WorkflowRun.project_id == payload.project_id,
                WorkflowRun.workflow_type == "linux_production",
            )
            .order_by(WorkflowRun.id)
            .limit(1)
            .with_for_update()
        )
        if existing is not None:
            raise AppHTTPException(
                409,
                "PRODUCTION_WORKFLOW_ALREADY_EXISTS",
                "This project already has its complete production workflow.",
                {"project_id": project.id, "workflow_id": existing.id},
            )
        config["definition_revision"] = 4
    workflow = WorkflowRun(
        project_id=payload.project_id,
        created_by=created_by,
        name=payload.name,
        workflow_type=payload.workflow_type,
        status="draft",
        progress=0,
        config_json=config,
    )
    db.add(workflow)
    db.flush()
    for sequence, (stage_code, name) in enumerate(
        WORKFLOW_DEFINITIONS[payload.workflow_type], start=1
    ):
        db.add(
            WorkflowStageRun(
                workflow_run_id=workflow.id,
                stage_code=stage_code,
                name=name,
                sequence=sequence,
                status="ready" if sequence == 1 else "pending",
                progress=0,
            )
        )
    db.flush()
    return workflow


def get_workflow_or_404(db: Session, workflow_id: int) -> WorkflowRun:
    workflow = db.scalar(select(WorkflowRun).where(WorkflowRun.id == workflow_id))
    if workflow is None:
        raise not_found("Workflow")
    return workflow


def start_workflow(db: Session, workflow: WorkflowRun) -> WorkflowRun:
    if workflow.status != "draft":
        raise AppHTTPException(409, "WORKFLOW_NOT_DRAFT", "Only a draft workflow can start.")
    first = min(workflow.stages, key=lambda stage: stage.sequence)
    now = business_now()
    workflow.status = "waiting_input"
    workflow.current_stage = first.stage_code
    workflow.started_at = now
    first.status = "waiting_input"
    first.started_at = now
    return workflow


def complete_manual_stage(
    db: Session,
    workflow: WorkflowRun,
    stage_code: str,
) -> WorkflowRun:
    stage = next((item for item in workflow.stages if item.stage_code == stage_code), None)
    if stage is None:
        raise AppHTTPException(422, "WORKFLOW_STAGE_UNKNOWN", "Unknown workflow stage.")
    if stage.status not in {"ready", "waiting_input", "waiting_review"}:
        raise AppHTTPException(
            409,
            "WORKFLOW_STAGE_NOT_ACTIONABLE",
            "This workflow stage is not awaiting input.",
        )
    capability = get_stage_capability(workflow, stage_code)
    if capability.execution_mode == "automated":
        raise AppHTTPException(
            409,
            "WORKFLOW_STAGE_REQUIRES_EXECUTION",
            "This automated stage must use its execution endpoint.",
        )
    if (
        workflow.workflow_type == "linux_production"
        and stage_code == "source_intake"
        and (workflow.input_batch is None or workflow.input_batch.status != "frozen")
    ):
        raise AppHTTPException(
            409,
            "WORKFLOW_INPUT_BATCH_NOT_FROZEN",
            "The production input batch must be validated and frozen through its dedicated endpoint.",
        )
    require_stage_inputs(workflow, stage_code)
    require_stage_outputs(workflow, stage_code)
    if workflow.workflow_type == "linux_production" and stage_code != "source_intake":
        verify_required_dxf_objects(db, workflow, stage_code)
    now = business_now()
    stage.status = "succeeded"
    stage.progress = 100
    stage.finished_at = now
    next_stage = _next_stage(workflow, stage.sequence)
    if next_stage is not None and next_stage.status == "pending":
        next_stage.status = "waiting_input"
        next_stage.started_at = now
    recompute_workflow(workflow)
    return workflow


def cancel_workflow(workflow: WorkflowRun) -> WorkflowRun:
    if workflow.status in WORKFLOW_TERMINAL:
        raise AppHTTPException(409, "WORKFLOW_TERMINAL", "Workflow is already terminal.")
    now = business_now()
    workflow.status = "cancelled"
    workflow.finished_at = now
    for stage in workflow.stages:
        if stage.status not in STAGE_TERMINAL:
            stage.status = "cancelled"
            stage.finished_at = now
    return workflow


def recompute_workflow(workflow: WorkflowRun) -> None:
    """根据各阶段状态重算运行状态与进度（调用方持有事务边界）。

    本函数**不提交**——事务边界由调用方负责。整体 ``cancelled`` 会被
    保留；否则失败阶段优先，其次是被取消阶段（见下方注释），最后才是
    成功/进行中的回退判定。
    """
    stages = sorted(workflow.stages, key=lambda item: item.sequence)
    if not stages:
        workflow.progress = 0
        return
    workflow.progress = round(sum(stage.progress for stage in stages) / len(stages))
    if workflow.status == "cancelled":
        return
    failed = next((stage for stage in stages if stage.status == "failed"), None)
    if failed is not None:
        workflow.status = "failed"
        workflow.current_stage = failed.stage_code
        workflow.error_code = failed.error_code
        workflow.error_message = failed.error_message
        workflow.finished_at = failed.finished_at or business_now()
        return
    # 阶段被取消的语义：单个阶段被取消时，运行被重算为 status="failed"
    # 且 error_code=WORKFLOW_STAGE_CANCELLED——含义是「该阶段可重试、运行
    # 尚未终态」。只有 cancel_workflow 才产生整体 cancelled。两者必须区分：
    # 调用方重试的是阶段，而不是整个运行。
    cancelled = next((stage for stage in stages if stage.status == "cancelled"), None)
    if cancelled is not None:
        workflow.status = "failed"
        workflow.current_stage = cancelled.stage_code
        workflow.error_code = "WORKFLOW_STAGE_CANCELLED"
        workflow.error_message = "The current stage job was cancelled and can be retried."
        workflow.finished_at = cancelled.finished_at or business_now()
        return
    if all(stage.status in {"succeeded", "skipped"} for stage in stages):
        workflow.status = "succeeded"
        workflow.progress = 100
        workflow.current_stage = stages[-1].stage_code
        workflow.finished_at = business_now()
        return
    current = next((stage for stage in stages if stage.status not in STAGE_TERMINAL), stages[-1])
    workflow.current_stage = current.stage_code
    if current.status in STAGE_ACTIVE:
        workflow.status = "running"
    elif current.status == "waiting_review":
        workflow.status = "waiting_review"
    elif current.status in {"ready", "waiting_input"}:
        workflow.status = "waiting_input"


def _next_stage(workflow: WorkflowRun, sequence: int) -> WorkflowStageRun | None:
    return next((stage for stage in workflow.stages if stage.sequence == sequence + 1), None)
