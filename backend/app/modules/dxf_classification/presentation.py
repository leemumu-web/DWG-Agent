"""Build the public classification ledger without leaking ORM rows to HTTP."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.dxf_classification.models import DxfClassificationRun
from app.modules.dxf_classification.schemas import (
    DxfClassificationItemRead,
    DxfClassificationRunRead,
)
from app.modules.files.interface import FileRead, StoredFile
from app.modules.jobs.interface import Job, JobRead
from app.platform.http.exceptions import AppHTTPException, not_found


def build_classification_run_read(
    db: Session,
    run: DxfClassificationRun,
) -> DxfClassificationRunRead:
    job = db.get(Job, run.job_id)
    if job is None:
        raise not_found("Classification job")
    report_file = db.get(StoredFile, run.report_file_id) if run.report_file_id else None
    manifest_file = db.get(StoredFile, run.manifest_file_id) if run.manifest_file_id else None
    items: list[DxfClassificationItemRead] = []
    for item in run.items:
        source_file = db.get(StoredFile, item.source_file_id)
        output_file = db.get(StoredFile, item.output_file_id)
        if source_file is None or output_file is None:
            raise AppHTTPException(
                409,
                "CLASSIFICATION_LEDGER_INCOMPLETE",
                "A classification item references a missing file registration.",
                {"item_id": item.id},
            )
        items.append(
            DxfClassificationItemRead(
                id=item.id,
                drawing_id=item.drawing_id,
                source_file=FileRead.model_validate(source_file),
                output_file=FileRead.model_validate(output_file),
                source_name=item.source_name,
                output_name=item.output_name,
                output_directory=item.output_directory,
                disposition=item.disposition,
                part_type=item.part_type,
                diagnostics=item.diagnostics_json or [],
            )
        )
    return DxfClassificationRunRead(
        id=run.id,
        workflow_run_id=run.workflow_run_id,
        status=run.status,
        classifier_version=run.classifier_version,
        report_schema=run.report_schema,
        cli_schema=run.cli_schema,
        project_name=run.project_name,
        input_manifest_sha256=run.input_manifest_sha256,
        input_count=run.input_count,
        classified_count=run.classified_count,
        review_required_count=run.review_required_count,
        unreadable_count=run.unreadable_count,
        type_counts=run.type_counts_json or {},
        report_file=FileRead.model_validate(report_file) if report_file else None,
        manifest_file=FileRead.model_validate(manifest_file) if manifest_file else None,
        job=JobRead.model_validate(job),
        items=items,
        error_code=run.error_code,
        error_message=run.error_message,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )
