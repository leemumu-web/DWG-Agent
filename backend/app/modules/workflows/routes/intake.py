"""Production input-batch HTTP operations."""

import json

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.modules.files.interface import (
    TransferSnapshot,
    TransferSpec,
    complete_transfer_in_transaction,
    prepare_transfer_in_transaction,
    sanitize_filename,
    save_upload_file,
    session_factory_for,
    settle_transfer,
)
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
    lock_input_batch,
    raise_excel_failure,
    register_input_file,
    remove_input_item,
    validate_input_dwg_folder_manifest,
    validate_input_excel_name,
)
from app.modules.workflows.lifecycle import get_workflow_or_404
from app.modules.workflows.schemas import (
    WorkflowInputBatchEnvelope,
    WorkflowInputConversionEnvelope,
    WorkflowInputConversionRead,
)
from app.platform.config.constants import TASK_DWG_TO_DXF
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.exceptions import AppHTTPException

router = APIRouter()


def _prepare_input_transfers(
    db: Session,
    uploads: list[UploadFile],
    *,
    actor_user_id: int,
    request_id: str,
    batch_id: int,
    operation: str,
) -> list[TransferSnapshot]:
    transfers = [
        prepare_transfer_in_transaction(
            db,
            TransferSpec(
                direction="inbound",
                operation=operation,
                actor_user_id=actor_user_id,
                request_id=request_id,
                idempotency_key=f"{request_id}:{index}",
                batch_ref=f"workflow-input-{batch_id}",
                original_name=sanitize_filename(upload.filename or "unnamed"),
            ),
        )
        for index, upload in enumerate(uploads)
    ]
    db.commit()
    return transfers


def _settle_failed_input_transfers(
    db: Session,
    transfers: list[TransferSnapshot],
    exc: Exception,
) -> None:
    db.rollback()
    detail = exc.detail if isinstance(exc, AppHTTPException) else None
    code = detail.get("code") if isinstance(detail, dict) else "INPUT_UPLOAD_FAILED"
    message = (
        detail.get("message")
        if isinstance(detail, dict)
        else "Production input upload failed before commit."
    )
    factory = session_factory_for(db)
    for transfer in transfers:
        settle_transfer(
            factory,
            transfer.transfer_uid,
            status="failed",
            transferred_bytes=0,
            error_code=str(code),
            error_message=str(message),
        )


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
    "/{workflow_id}/input-excel",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkflowInputBatchEnvelope,
    summary="上传生产 Excel",
    description="单独接收一个 .xls 或 .xlsx；DWG 必须通过文件夹入口另行上传。",
)
async def import_input_excel_api(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    upload: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    workflow = get_workflow_or_404(db, workflow_id)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    validate_input_excel_name(upload.filename or "")
    batch = lock_input_batch(db, get_input_batch(db, workflow_id))
    if any(item.role == "source_excel" for item in batch.items):
        raise AppHTTPException(
            409,
            "INPUT_EXCEL_ALREADY_IMPORTED",
            "Remove the current production input before uploading another Excel file.",
        )
    transfers = _prepare_input_transfers(
        db,
        [upload],
        actor_user_id=current_user.id,
        request_id=request.state.request_id,
        batch_id=batch.id,
        operation="workflow_input_excel",
    )
    try:
        stored = await save_upload_file(
            db,
            upload,
            uploaded_by=current_user.id,
            batch_name=f"workflow-input-{batch.id}",
            transfer_uid=transfers[0].transfer_uid,
            request_id=request.state.request_id,
            advance_transfer_intent=False,
        )
        complete_transfer_in_transaction(
            db,
            transfers[0].transfer_uid,
            file_id=stored.id,
            bucket=stored.bucket,
            storage_key=stored.storage_key,
            original_name=stored.original_name,
            transferred_bytes=stored.size_bytes,
        )
        outcome = register_input_file(db, batch, stored)
        if outcome.failure is not None:
            raise_excel_failure(outcome.failure)
        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action="workflow_input_excel.import",
            resource_type="workflow_input_batch",
            resource_id=batch.id,
            after_json={"workflow_id": workflow.id, "file_id": stored.id},
            request=request,
        )
        db.commit()
    except Exception as exc:
        _settle_failed_input_transfers(db, transfers, exc)
        raise
    return ok(describe_input_batch(db, batch).model_dump(), request.state.request_id)


@router.post(
    "/{workflow_id}/input-dwg-folder",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkflowInputBatchEnvelope,
    summary="导入生产 DWG 文件夹",
    description="只接收一个文件夹中的 DWG；其他文件必须在浏览器确认后排除，不得上传。",
)
async def import_input_dwg_folder_api(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    uploads: list[UploadFile] = File(...),
    relative_paths: str = Form(...),
    db: Session = Depends(get_db),
):
    workflow = get_workflow_or_404(db, workflow_id)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    try:
        parsed_paths = json.loads(relative_paths)
    except json.JSONDecodeError as exc:
        raise AppHTTPException(
            422,
            "INPUT_FOLDER_MANIFEST_INVALID",
            "The folder manifest is not valid JSON.",
        ) from exc
    if not isinstance(parsed_paths, list) or not all(
        isinstance(value, str) for value in parsed_paths
    ):
        raise AppHTTPException(
            422,
            "INPUT_FOLDER_MANIFEST_INVALID",
            "The folder manifest must be a JSON string array.",
        )
    upload_names = [upload.filename or "" for upload in uploads]
    folder_name = validate_input_dwg_folder_manifest(upload_names, parsed_paths)

    batch = lock_input_batch(db, get_input_batch(db, workflow_id))
    if any(item.role == "source_dwg" for item in batch.items):
        raise AppHTTPException(
            409,
            "INPUT_DWG_FOLDER_ALREADY_IMPORTED",
            "Remove the current production input before uploading another DWG folder.",
        )

    transfers = _prepare_input_transfers(
        db,
        uploads,
        actor_user_id=current_user.id,
        request_id=request.state.request_id,
        batch_id=batch.id,
        operation="workflow_input_dwg_folder",
    )
    try:
        imported_file_ids: list[int] = []
        for upload, transfer in zip(uploads, transfers, strict=True):
            stored = await save_upload_file(
                db,
                upload,
                uploaded_by=current_user.id,
                batch_name=f"workflow-input-{batch.id}",
                transfer_uid=transfer.transfer_uid,
                request_id=request.state.request_id,
                advance_transfer_intent=False,
            )
            complete_transfer_in_transaction(
                db,
                transfer.transfer_uid,
                file_id=stored.id,
                bucket=stored.bucket,
                storage_key=stored.storage_key,
                original_name=stored.original_name,
                transferred_bytes=stored.size_bytes,
            )
            outcome = register_input_file(db, batch, stored)
            if outcome.failure is not None:
                raise_excel_failure(outcome.failure)
            imported_file_ids.append(stored.id)

        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action="workflow_input_dwg_folders.import",
            resource_type="workflow_input_batch",
            resource_id=batch.id,
            after_json={
                "workflow_id": workflow.id,
                "folder_name": folder_name,
                "file_ids": imported_file_ids,
                "relative_paths": parsed_paths,
            },
            request=request,
        )
        db.commit()
    except Exception as exc:
        _settle_failed_input_transfers(db, transfers, exc)
        raise
    return ok(describe_input_batch(db, batch).model_dump(), request.state.request_id)


@router.delete(
    "/{workflow_id}/input-folder",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="清空生产输入文件夹",
)
def clear_input_folder_api(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = get_workflow_or_404(db, workflow_id)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    batch = get_input_batch(db, workflow_id)
    item_ids = [item.id for item in batch.items]
    for item_id in item_ids:
        remove_input_item(db, batch, item_id)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="workflow_input_folders.clear",
        resource_type="workflow_input_batch",
        resource_id=batch.id,
        after_json={"removed_item_ids": item_ids},
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
