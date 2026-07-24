"""Production input-batch HTTP operations."""

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.modules.files.interface import StoredFile, require_file_read_access
from app.modules.identity.interface import CurrentUser
from app.modules.jobs.interface import JobRead, dispatch_committed_conversion_batch
from app.modules.operations.audit.interface import write_audit_log
from app.modules.projects.interface import require_project_member, require_project_role
from app.modules.workflows.access import WORKFLOW_WRITE_ROLES
from app.modules.workflows.intake.conversion import prepare_input_conversions
from app.modules.workflows.intake.freeze import freeze_input_batch
from app.modules.workflows.intake.presentation import describe_input_batch
from app.modules.workflows.intake.registration import (
    create_input_batch,
    get_input_batch,
    register_input_file,
    remove_input_item,
)
from app.modules.workflows.lifecycle import get_workflow_or_404
from app.modules.workflows.schemas import (
    WorkflowInputBatchEnvelope,
    WorkflowInputConversionEnvelope,
    WorkflowInputConversionRead,
    WorkflowInputFileCreate,
    WorkflowInputRegistrationEnvelope,
)
from app.platform.config.constants import TASK_DWG_TO_DXF
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.exceptions import AppHTTPException, not_found

router = APIRouter()


@router.post(
    "/{workflow_id}/input-batch",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkflowInputBatchEnvelope,
    summary="创建生产输入批次",
    description="为 Linux 生产工作流幂等创建一个多 DWG 加单 Excel 的输入批次。",
)
def create_batch_api(
    workflow_id: int,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = get_workflow_or_404(db, workflow_id)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    reused = workflow.input_batch is not None
    batch = create_input_batch(db, workflow, created_by=current_user.id)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action=("workflow_input_batches.reuse" if reused else "workflow_input_batches.create"),
        resource_type="workflow_input_batch",
        resource_id=batch.id,
        request=request,
    )
    db.commit()
    if reused:
        response.status_code = status.HTTP_200_OK
    return ok(describe_input_batch(db, batch).model_dump(), request.state.request_id)


@router.get(
    "/{workflow_id}/input-batch",
    response_model=WorkflowInputBatchEnvelope,
    summary="读取生产输入批次",
    description="同步当前 DWG 转换 Job 与派生 DXF，并返回逐文件问题和冻结条件。",
)
def get_batch_api(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = get_workflow_or_404(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    batch = get_input_batch(db, workflow_id)
    result = describe_input_batch(db, batch)
    db.commit()
    return ok(result.model_dump(), request.state.request_id)


@router.post(
    "/{workflow_id}/input-batch/files",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkflowInputRegistrationEnvelope,
    summary="登记生产输入文件",
    description="登记由 /files 上传的真实 DWG 或唯一 Excel；人工 DXF 被拒绝。",
)
def register_file_api(
    workflow_id: int,
    payload: WorkflowInputFileCreate,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = get_workflow_or_404(db, workflow_id)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    batch = get_input_batch(db, workflow_id)
    stored = db.get(StoredFile, payload.file_id)
    if stored is None or stored.status == "deleted":
        raise not_found("File")
    require_file_read_access(db, current_user, stored)
    known_ids = {item.file_id for item in batch.items}
    outcome = register_input_file(db, batch, stored)
    item = outcome.item
    reused = stored.id in known_ids
    if outcome.failure is not None:
        audit_action = "workflow_input_files.reject"
    elif reused:
        audit_action = "workflow_input_files.reuse"
    else:
        audit_action = "workflow_input_files.register"
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action=audit_action,
        resource_type="workflow_input_item",
        resource_id=item.id,
        after_json={
            "workflow_id": workflow.id,
            "file_id": stored.id,
            "role": item.role,
            "failure": outcome.failure,
        },
        request=request,
    )
    db.commit()
    if outcome.failure is not None:
        failure_code = str(outcome.failure.get("code") or "EXCEL_INPUT_INVALID")
        failure_message = str(
            outcome.failure.get("message") or "Excel 输入未通过检查。"
        )
        raise AppHTTPException(
            422,
            failure_code,
            failure_message,
            {"failure": outcome.failure},
        )
    if reused:
        response.status_code = status.HTTP_200_OK
    return ok(
        {
            "batch": describe_input_batch(db, batch).model_dump(),
            "item_id": item.id,
            "reused": reused,
        },
        request.state.request_id,
    )


@router.delete(
    "/{workflow_id}/input-batch/files/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="移除生产输入文件",
)
def remove_file_api(
    workflow_id: int,
    item_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = get_workflow_or_404(db, workflow_id)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    batch = get_input_batch(db, workflow_id)
    remove_input_item(db, batch, item_id)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="workflow_input_files.remove",
        resource_type="workflow_input_item",
        resource_id=item_id,
        request=request,
    )
    db.commit()
    return None


@router.post(
    "/{workflow_id}/input-batch/conversion-requests",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WorkflowInputConversionEnvelope,
    summary="提交 DWG 到 DXF 转换",
    description="复用现有 ODA 批量 Job；只投递新增或递增 attempt 的任务。",
)
def convert_batch_api(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = get_workflow_or_404(db, workflow_id)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    batch = get_input_batch(db, workflow_id)
    plan = prepare_input_conversions(db, batch, created_by=current_user.id)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="workflow_input_batches.convert",
        resource_type="workflow_input_batch",
        resource_id=batch.id,
        after_json={
            "job_ids": [job.id for job in plan.jobs],
            "dispatch": plan.dispatch,
        },
        request=request,
    )
    db.commit()
    if plan.dispatch:
        dispatch_committed_conversion_batch(task_type=TASK_DWG_TO_DXF, jobs=plan.dispatch)
    result = WorkflowInputConversionRead(
        batch=describe_input_batch(db, batch),
        jobs=[JobRead.model_validate(job) for job in plan.jobs],
        dispatched_count=len(plan.dispatch),
    )
    return ok(result.model_dump(), request.state.request_id)


@router.post(
    "/{workflow_id}/input-batch/freeze",
    response_model=WorkflowInputBatchEnvelope,
    summary="冻结生产输入批次",
    description="重新校验全部对象和配对，创建 Drawing 与不可变清单并完成 source_intake。",
)
def freeze_batch_api(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = get_workflow_or_404(db, workflow_id)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    batch = freeze_input_batch(db, get_input_batch(db, workflow_id))
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="workflow_input_batches.freeze",
        resource_type="workflow_input_batch",
        resource_id=batch.id,
        after_json={"manifest_sha256": batch.manifest_sha256},
        request=request,
    )
    db.commit()
    return ok(describe_input_batch(db, batch).model_dump(), request.state.request_id)
