"""Workflow automated and placeholder stage execution endpoint."""

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.modules.identity.interface import CurrentUser
from app.modules.jobs.interface import JobRead, drain_eager_dispatches, stage_job_dispatch
from app.modules.operations.audit.interface import write_audit_log
from app.modules.projects.interface import require_project_role
from app.modules.workflows.access import WORKFLOW_WRITE_ROLES, load_workflow_detail
from app.modules.workflows.schemas import WorkflowDetail, WorkflowStageExecutionCreate
from app.modules.workflows.stage_execution import (
    preflight_excel_stage1,
    preflight_excel_stage2,
    prepare_stage_execution,
)
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok

router = APIRouter()


@router.get(
    "/{workflow_id}/stages/excel_stage1/preflight",
    summary="预检 Excel 第一阶段输入",
    description="使用与正式执行完全相同的冻结输入、Excel 结构和拆板交接校验，但不创建任务。",
)
def preflight_excel_stage1_api(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    result = preflight_excel_stage1(
        db,
        workflow,
        current_user=current_user,
    )
    return ok(result, request.state.request_id)


@router.get(
    "/{workflow_id}/stages/excel_stage2/preflight",
    summary="预检 Excel 第二阶段输入",
    description=(
        "核对当前第一阶段正式 Excel、分类 Job attempt 与拆板前 BH 图纸账本，"
        "但不创建任务。"
    ),
)
def preflight_excel_stage2_api(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    result = preflight_excel_stage2(
        db,
        workflow,
        current_user=current_user,
    )
    return ok(result, request.state.request_id)


@router.post(
    "/{workflow_id}/stages/{stage_code}/executions",
    status_code=status.HTTP_202_ACCEPTED,
    summary="执行工作流自动或外部阶段",
    description=(
        "按后端模板能力调用已实现的 Linux Job；未实现阶段保留同一路径并返回稳定能力边界。"
    ),
)
def execute_workflow_stage(
    workflow_id: int,
    stage_code: str,
    payload: WorkflowStageExecutionCreate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    # Serialize the first Job binding so concurrent project members cannot
    # create separate logical executions for the same workflow stage.
    workflow = load_workflow_detail(db, workflow_id, for_update=True)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    plan = prepare_stage_execution(
        db,
        workflow,
        stage_code=stage_code,
        payload=payload,
        current_user=current_user,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action=(
            "workflow_stages.retry"
            if plan.retried
            else "workflow_stages.execution_reused"
            if plan.reused
            else "workflow_stages.execute"
        ),
        resource_type="workflow",
        resource_id=workflow.id,
        after_json={
            "stage_code": stage_code,
            "execution_kind": payload.execution_kind,
            "job_id": plan.job.id,
        },
        request=request,
    )
    if plan.should_dispatch:
        stage_job_dispatch(db, plan.job)
    db.commit()
    if plan.should_dispatch:
        drain_eager_dispatches(db)
    return ok(
        {
            "workflow": WorkflowDetail.model_validate(load_workflow_detail(db, workflow.id)),
            "job": JobRead.model_validate(plan.job),
            "reused": plan.reused,
            "retried": plan.retried,
        },
        request.state.request_id,
    )
