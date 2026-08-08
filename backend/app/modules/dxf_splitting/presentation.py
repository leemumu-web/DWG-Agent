"""Build the public split ledger without exposing ORM rows."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.modules.dxf_classification.models import DxfClassificationItem, DxfClassificationRun
from app.modules.dxf_splitting.models import DxfSplitRun
from app.modules.dxf_splitting.schemas import DxfSplitItemRead, DxfSplitRunRead
from app.modules.files.interface import FileRead, StoredFile
from app.modules.jobs.interface import Job, JobRead
from app.platform.http.exceptions import AppHTTPException, not_found
from app.platform.time import as_business_time, business_now


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
    *,
    now: datetime | None = None,
) -> DxfSplitRunRead:
    job = db.get(Job, run.job_id)
    if job is None:
        raise not_found("DXF split job")
    ledger = _optional_file(db, run.bh_split_ledger_file_id)
    manifest = _optional_file(db, run.split_manifest_file_id)
    current_time = as_business_time(now or business_now())
    end_time = as_business_time(run.finished_at) if run.finished_at is not None else current_time
    elapsed_seconds = (
        max(0, int((end_time - as_business_time(run.started_at)).total_seconds()))
        if run.started_at is not None
        else 0
    )
    throughput_per_minute = (
        run.processed_count / (elapsed_seconds / 60)
        if run.processed_count > 0 and elapsed_seconds > 0
        else None
    )
    remaining_count = max(run.input_count - run.processed_count, 0)
    estimated_remaining_seconds = (
        round((remaining_count / throughput_per_minute) * 60)
        if throughput_per_minute and run.status == "running"
        else 0
        if remaining_count == 0
        else None
    )
    manual_items = [
        item for item in run.items if item.automation_route == "manual_review"
    ]
    classification = db.get(DxfClassificationRun, run.classification_run_id)
    if classification is None:
        raise AppHTTPException(
            409,
            "DXF_SPLIT_LEDGER_INCOMPLETE",
            "拆板批次引用的分类运行不可用。",
            {"classification_run_id": run.classification_run_id},
        )
    # Aggregate classification-only types in the database instead of loading
    # and iterating every classification item on each poll. `notin_` yields
    # NULL for a NULL part_type, which would drop the row in a WHERE clause;
    # coalesce it to True so NULL part types are kept, matching the previous
    # Python `part_type in {"BH", "BOX"}` check.
    kept = or_(
        DxfClassificationItem.disposition != "classified",
        func.coalesce(DxfClassificationItem.part_type.notin_(("BH", "BOX")), True),
        DxfClassificationItem.next_stage_eligible.is_(False),
    )
    label = func.coalesce(
        func.nullif(DxfClassificationItem.part_type, ""),
        DxfClassificationItem.disposition,
        "unclassified",
    )
    classification_only_type_counts = {
        name: int(count)
        for name, count in db.execute(
            select(label, func.count(DxfClassificationItem.id))
            .where(
                and_(
                    DxfClassificationItem.run_id == classification.id,
                    kept,
                )
            )
            .group_by(label)
        ).all()
    }
    return DxfSplitRunRead(
        id=run.id,
        workflow_run_id=run.workflow_run_id,
        status=run.status,
        splitter_version=run.splitter_version,
        cli_schema=run.cli_schema,
        validation_schema=run.validation_schema,
        input_manifest_sha256=run.input_manifest_sha256,
        input_count=run.input_count,
        processed_count=run.processed_count,
        failed_count=run.failed_count,
        reviewed_count=sum(item.review_decision is not None for item in manual_items),
        elapsed_seconds=elapsed_seconds,
        throughput_per_minute=throughput_per_minute,
        estimated_remaining_seconds=estimated_remaining_seconds,
        auto_accepted_count=run.auto_accepted_count,
        manual_review_count=run.manual_review_count,
        classifier_confirmed_count=sum(
            item.type_resolution == "classifier_confirmed" for item in run.items
        ),
        splitter_detected_count=sum(
            item.type_resolution == "splitter_detected" for item in run.items
        ),
        unresolved_count=sum(
            item.type_resolution == "unresolved" for item in run.items
        ),
        classification_input_count=classification.input_count,
        classification_only_count=max(
            classification.input_count - run.input_count,
            0,
        ),
        classification_only_type_counts=classification_only_type_counts,
        source_contracts=run.source_contracts_json or {},
        bh_split_ledger_file=FileRead.model_validate(ledger) if ledger else None,
        split_manifest_file=FileRead.model_validate(manifest) if manifest else None,
        job=JobRead.model_validate(job),
        items=[
            DxfSplitItemRead(
                id=item.id,
                drawing_id=item.drawing_id,
                classification_item_id=item.classification_item_id,
                source_file_id=item.source_file_id,
                source_name=item.source_name,
                classification_disposition=item.classification_disposition,
                classification_part_type=item.classification_part_type,
                type_resolution=item.type_resolution,
                part_type=item.part_type,
                profile_normalized=item.profile_normalized,
                family=item.family,
                source_contract_id=item.source_contract_id,
                automation_route=item.automation_route,
                disposition=item.disposition,
                normal_dxf_file_id=item.normal_dxf_file_id,
                weld_allowance_dxf_file_id=item.weld_allowance_dxf_file_id,
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
