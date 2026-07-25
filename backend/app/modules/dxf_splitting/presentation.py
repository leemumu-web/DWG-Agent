"""Build the public split ledger without exposing ORM rows."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.dxf_splitting.models import DxfSplitRun
from app.modules.dxf_splitting.schemas import DxfSplitItemRead, DxfSplitRunRead
from app.modules.files.interface import FileRead, StoredFile
from app.modules.jobs.interface import Job, JobRead
from app.platform.http.exceptions import AppHTTPException, not_found


def _optional_file(db: Session, file_id: int | None) -> StoredFile | None:
    if file_id is None:
        return None
    stored = db.get(StoredFile, file_id)
    if stored is None or stored.status == "deleted":
        raise AppHTTPException(
            409,
            "DXF_SPLIT_LEDGER_INCOMPLETE",
            "拆板批次引用的已登记文件不可用。",
            {"file_id": file_id},
        )
    return stored


def build_dxf_split_run_read(
    db: Session,
    run: DxfSplitRun,
) -> DxfSplitRunRead:
    job = db.get(Job, run.job_id)
    if job is None:
        raise not_found("DXF split job")
    ledger = _optional_file(db, run.bh_split_ledger_file_id)
    manifest = _optional_file(db, run.split_manifest_file_id)
    validation = _optional_file(db, run.validation_report_file_id)
    return DxfSplitRunRead(
        id=run.id,
        workflow_run_id=run.workflow_run_id,
        status=run.status,
        splitter_version=run.splitter_version,
        cli_schema=run.cli_schema,
        validation_schema=run.validation_schema,
        input_manifest_sha256=run.input_manifest_sha256,
        input_count=run.input_count,
        auto_accepted_count=run.auto_accepted_count,
        manual_review_count=run.manual_review_count,
        source_contracts=run.source_contracts_json or {},
        bh_split_ledger_file=FileRead.model_validate(ledger) if ledger else None,
        split_manifest_file=FileRead.model_validate(manifest) if manifest else None,
        validation_report_file=(FileRead.model_validate(validation) if validation else None),
        job=JobRead.model_validate(job),
        items=[
            DxfSplitItemRead(
                id=item.id,
                drawing_id=item.drawing_id,
                classification_item_id=item.classification_item_id,
                source_file_id=item.source_file_id,
                source_name=item.source_name,
                part_type=item.part_type,
                profile_normalized=item.profile_normalized,
                family=item.family,
                source_contract_id=item.source_contract_id,
                automation_route=item.automation_route,
                disposition=item.disposition,
                normal_dxf_file_id=item.normal_dxf_file_id,
                weld_allowance_dxf_file_id=item.weld_allowance_dxf_file_id,
                split_report_file_id=item.split_report_file_id,
                weld_allowance_report_file_id=item.weld_allowance_report_file_id,
                diagnostics=item.diagnostics_json or [],
                validation=item.validation_json or {},
            )
            for item in run.items
        ],
        error_code=run.error_code,
        error_message=run.error_message,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )
