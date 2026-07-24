"""Build the public classification ledger without leaking ORM rows to HTTP."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.dxf_classification.models import DxfClassificationRun
from app.modules.dxf_classification.schemas import (
    DxfClassificationGroupItemRead,
    DxfClassificationGroupPage,
    DxfClassificationGroupRead,
    DxfClassificationItemRead,
    DxfClassificationRunRead,
)
from app.modules.files.interface import FileRead, StoredFile
from app.modules.jobs.interface import Job, JobRead
from app.platform.http.exceptions import AppHTTPException, not_found


def _group_label(group_key: str, part_type: str | None) -> str:
    if group_key == "status:review_required":
        return "待确认"
    if group_key == "status:unreadable":
        return "无法读取"
    if group_key.startswith("type:"):
        return part_type or group_key.removeprefix("type:")
    return group_key


def _group_sort_key(group: DxfClassificationGroupRead) -> tuple[int, str]:
    warning_order = {
        "status:review_required": 0,
        "status:unreadable": 1,
    }
    return warning_order.get(group.group_key, 2), group.label.casefold()


def build_classification_groups(
    db: Session,
    run: DxfClassificationRun,
) -> list[DxfClassificationGroupRead]:
    grouped: dict[str, list[tuple[object, StoredFile]]] = {}
    for item in run.items:
        output_file = db.get(StoredFile, item.output_file_id)
        if output_file is None or output_file.status == "deleted":
            raise AppHTTPException(
                409,
                "CLASSIFICATION_LEDGER_INCOMPLETE",
                "A classification item references a missing output file.",
                {"item_id": item.id},
            )
        grouped.setdefault(item.group_key, []).append((item, output_file))

    groups: list[DxfClassificationGroupRead] = []
    for group_key, rows in grouped.items():
        first = rows[0][0]
        type_sources = {item.type_source for item, _ in rows if item.type_source}
        if "auto_discovered" in type_sources:
            type_source = "auto_discovered"
        elif "catalog" in type_sources:
            type_source = "catalog"
        elif "legacy" in type_sources:
            type_source = "legacy"
        else:
            type_source = None
        groups.append(
            DxfClassificationGroupRead(
                group_key=group_key,
                label=_group_label(group_key, first.part_type),
                part_type=first.part_type,
                type_source=type_source,
                disposition=first.disposition,
                count=len(rows),
                warning_count=sum(
                    item.disposition != "classified" for item, _ in rows
                ),
                total_size_bytes=sum(stored.size_bytes for _, stored in rows),
            )
        )
    return sorted(groups, key=_group_sort_key)


def build_classification_group_page(
    db: Session,
    run: DxfClassificationRun,
    *,
    group_key: str,
    page: int,
    page_size: int,
) -> DxfClassificationGroupPage:
    matching = [item for item in run.items if item.group_key == group_key]
    if not matching:
        raise AppHTTPException(
            404,
            "CLASSIFICATION_GROUP_NOT_FOUND",
            "The DXF classification group was not found.",
            {"group_key": group_key},
        )
    total = len(matching)
    start = (page - 1) * page_size
    page_items: list[DxfClassificationGroupItemRead] = []
    for item in matching[start : start + page_size]:
        output_file = db.get(StoredFile, item.output_file_id)
        if output_file is None or output_file.status == "deleted":
            raise AppHTTPException(
                409,
                "CLASSIFICATION_OUTPUT_MISSING",
                "A classified DXF output is unavailable.",
                {"group_key": group_key},
            )
        page_items.append(
            DxfClassificationGroupItemRead(
                output_name=item.output_name,
                part_type=item.part_type,
                profile_raw=item.profile_raw,
                profile_normalized=item.profile_normalized,
                type_source=item.type_source,
                disposition=item.disposition,
                diagnostics=item.diagnostics_json or [],
                size_bytes=output_file.size_bytes,
            )
        )
    return DxfClassificationGroupPage(
        items=page_items,
        total=total,
        page=page,
        page_size=page_size,
    )


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
        groups=build_classification_groups(db, run),
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
