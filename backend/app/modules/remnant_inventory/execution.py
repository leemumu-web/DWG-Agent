from __future__ import annotations

import logging
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.cad_processing.interface import convert_dwg_directory
from app.modules.files.interface import (
    StoredFile,
    get_storage_backend,
    save_path_as_file,
)
from app.modules.jobs.interface import (
    Job,
    JobCreate,
    claim_queued_job,
    complete_job_attempt,
    create_job,
    fail_job_attempt,
    make_event,
)
from app.modules.remnant_inventory.models import RemnantImportBatch, RemnantImportItem
from app.modules.remnant_inventory.stage_adapter import parse_staged_dxf
from app.platform.config.settings import settings
from app.platform.database.session import SessionLocal
from app.platform.http.exceptions import AppHTTPException

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionDispatch:
    batch_id: int
    convert_attempts: dict[int, int]
    parse_attempts: dict[int, int]


def _candidate_payload(result, name: str):
    return result.to_dict().get(name, [])


def store_parse_result(db: Session, item_id: int, *, expected_attempt: int, result) -> bool:
    statement = (
        update(RemnantImportItem)
        .where(
            RemnantImportItem.id == item_id,
            RemnantImportItem.status == "parsing",
            RemnantImportItem.attempt == expected_attempt,
        )
        .values(
            status="pending_confirmation",
            parser_version=result.parser_version,
            schema_version=result.schema_version,
            material_candidates_json=_candidate_payload(result, "material_candidates"),
            project_candidates_json=_candidate_payload(result, "project_candidates"),
            part_candidates_json=_candidate_payload(result, "part_candidates"),
            warnings_json=_candidate_payload(result, "warnings"),
            error_code=None,
            error_message=None,
        )
        .execution_options(synchronize_session=False)
    )
    return db.execute(statement).rowcount == 1


def recalculate_batch_counters(db: Session, batch_id: int) -> RemnantImportBatch:
    batch = db.get(RemnantImportBatch, batch_id)
    if batch is None:
        raise AppHTTPException(404, "REMNANT_IMPORT_BATCH_NOT_FOUND", "Import batch not found.")
    counts = Counter(
        db.scalars(
            select(RemnantImportItem.status).where(RemnantImportItem.batch_id == batch_id)
        ).all()
    )
    batch.total_count = sum(counts.values())
    batch.converting_count = counts["converting"]
    batch.parsing_count = counts["parsing"]
    batch.pending_count = counts["pending_confirmation"]
    batch.confirmed_count = counts["confirmed"]
    batch.failed_count = counts["failed"]
    batch.cancelled_count = counts["cancelled"]
    if batch.cancelled_count == batch.total_count:
        batch.status = "cancelled"
    elif batch.confirmed_count == batch.total_count:
        batch.status = "confirmed"
    elif batch.failed_count + batch.pending_count + batch.confirmed_count == batch.total_count:
        batch.status = "awaiting_confirmation"
    else:
        batch.status = "processing"
    db.flush()
    return batch


def prepare_import_execution(db: Session, batch_id: int, *, actor_id: int) -> ExecutionDispatch:
    items = list(
        db.scalars(
            select(RemnantImportItem)
            .where(RemnantImportItem.batch_id == batch_id)
            .order_by(RemnantImportItem.id)
        ).all()
    )
    if not items:
        raise AppHTTPException(404, "REMNANT_IMPORT_BATCH_NOT_FOUND", "Import batch not found.")
    converts: dict[int, int] = {}
    parses: dict[int, int] = {}
    for item in items:
        if item.status != "uploaded":
            continue
        parse_job = create_job(
            db,
            JobCreate(task_type="parse_remnant_drawing", params={"item_id": item.id}),
            created_by=actor_id,
        )
        item.parse_job_id = parse_job.id
        if item.source_ext == ".dxf":
            item.dxf_file_id = item.source_file_id
            item.status = "parsing"
            parses[item.id] = item.attempt
        else:
            convert_job = create_job(
                db,
                JobCreate(task_type="convert_remnant_dwg", params={"item_id": item.id}),
                created_by=actor_id,
            )
            item.conversion_job_id = convert_job.id
            item.status = "converting"
            converts[item.id] = item.attempt
    recalculate_batch_counters(db, batch_id)
    return ExecutionDispatch(batch_id, converts, parses)


def dispatch_import_execution(dispatch: ExecutionDispatch) -> None:
    from app.modules.remnant_inventory.tasks import convert_batch_task, parse_item_task

    if dispatch.convert_attempts:
        convert_batch_task.delay(dispatch.batch_id, dispatch.convert_attempts)
    for item_id, attempt in dispatch.parse_attempts.items():
        parse_item_task.delay(item_id, attempt)


def _stage_file(file: StoredFile, target: Path) -> None:
    storage = get_storage_backend()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as output:
        for chunk in storage.iter_file(file.bucket, file.storage_key):
            output.write(chunk)


def _mark_item_failed(db: Session, item: RemnantImportItem, attempt: int, code: str) -> bool:
    result = db.execute(
        update(RemnantImportItem)
        .where(
            RemnantImportItem.id == item.id,
            RemnantImportItem.attempt == attempt,
            RemnantImportItem.status.in_(("converting", "parsing")),
        )
        .values(status="failed", error_code=code, error_message="Drawing processing failed.")
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def run_conversion_batch(batch_id: int, expected_attempts: dict[int | str, int]) -> None:
    expected = {int(item_id): int(attempt) for item_id, attempt in expected_attempts.items()}
    db = SessionLocal()
    parse_dispatch: list[tuple[int, int]] = []
    try:
        items = list(
            db.scalars(select(RemnantImportItem).where(RemnantImportItem.id.in_(expected))).all()
        )
        active: list[RemnantImportItem] = []
        for item in items:
            if (
                item.status != "converting"
                or item.attempt != expected[item.id]
                or item.conversion_job_id is None
            ):
                continue
            job = claim_queued_job(
                db,
                item.conversion_job_id,
                expected_attempt=1,
                pipeline="remnant_convert",
                progress=10,
                message="开始余料图纸转换",
            )
            if job is not None:
                active.append(item)
        with tempfile.TemporaryDirectory(prefix=f"remnant_convert_{batch_id}_") as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            output_dir = root / "output"
            staged: dict[int, Path] = {}
            for item in active:
                source = db.get(StoredFile, item.source_file_id)
                if source is None:
                    continue
                target = input_dir / f"{item.id}.dwg"
                _stage_file(source, target)
                staged[item.id] = target
            outputs = convert_dwg_directory(staged, output_dir)
            for item in active:
                attempt = expected[item.id]
                output = outputs.get(item.id)
                if output is None:
                    if (
                        _mark_item_failed(db, item, attempt, "REMNANT_CONVERSION_FAILED")
                        and item.conversion_job_id
                    ):
                        fail_job_attempt(
                            db,
                            item.conversion_job_id,
                            attempt=1,
                            error_code="REMNANT_CONVERSION_FAILED",
                            error_message="Drawing conversion failed.",
                        )
                    continue
                stored = save_path_as_file(
                    db,
                    bucket=settings.minio_bucket_dxf_derived,
                    storage_key=f"remnants/{uuid4().hex}.dxf",
                    original_name=f"{Path(db.get(StoredFile, item.source_file_id).original_name).stem}.dxf",
                    file_ext=".dxf",
                    content_type="application/dxf",
                    source_path=output,
                    uploaded_by=None,
                    batch_name=f"remnant-{batch_id}",
                )
                changed = db.execute(
                    update(RemnantImportItem)
                    .where(
                        RemnantImportItem.id == item.id,
                        RemnantImportItem.status == "converting",
                        RemnantImportItem.attempt == attempt,
                    )
                    .values(
                        dxf_file_id=stored.id, status="parsing", error_code=None, error_message=None
                    )
                    .execution_options(synchronize_session=False)
                ).rowcount
                if changed and item.conversion_job_id:
                    complete_job_attempt(
                        db,
                        item.conversion_job_id,
                        attempt=1,
                        event=make_event(type_="done", message="余料图纸转换完成"),
                    )
                    parse_dispatch.append((item.id, attempt))
        recalculate_batch_counters(db, batch_id)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Remnant conversion batch failed: %s", batch_id)
        raise
    finally:
        db.close()
    from app.modules.remnant_inventory.tasks import parse_item_task

    for item_id, attempt in parse_dispatch:
        parse_item_task.delay(item_id, attempt)


def run_parse_item(item_id: int, expected_attempt: int) -> None:
    db = SessionLocal()
    try:
        item = db.get(RemnantImportItem, item_id)
        if (
            item is None
            or item.status != "parsing"
            or item.attempt != expected_attempt
            or item.dxf_file_id is None
        ):
            return
        if item.parse_job_id is not None:
            claimed = claim_queued_job(
                db,
                item.parse_job_id,
                expected_attempt=1,
                pipeline="remnant_parse",
                progress=10,
                message="开始解析余料图纸",
            )
            if claimed is None:
                return
        source = db.get(StoredFile, item.dxf_file_id)
        if source is None:
            raise RuntimeError("registered DXF file is missing")
        with tempfile.TemporaryDirectory(prefix=f"remnant_parse_{item_id}_") as tmp:
            path = Path(tmp) / f"{item_id}.dxf"
            _stage_file(source, path)
            result = parse_staged_dxf(path)
        if not store_parse_result(db, item_id, expected_attempt=expected_attempt, result=result):
            db.rollback()
            return
        if item.parse_job_id is not None:
            complete_job_attempt(
                db,
                item.parse_job_id,
                attempt=1,
                event=make_event(type_="done", message="余料图纸解析完成"),
            )
        recalculate_batch_counters(db, item.batch_id)
        db.commit()
    except Exception:
        db.rollback()
        item = db.get(RemnantImportItem, item_id)
        if item is not None and _mark_item_failed(
            db, item, expected_attempt, "REMNANT_PARSE_FAILED"
        ):
            if item.parse_job_id is not None:
                job = db.get(Job, item.parse_job_id)
                if job is not None and job.status == "running":
                    fail_job_attempt(
                        db,
                        item.parse_job_id,
                        attempt=1,
                        error_code="REMNANT_PARSE_FAILED",
                        error_message="Drawing parsing failed.",
                    )
            recalculate_batch_counters(db, item.batch_id)
            db.commit()
        logger.exception("Remnant parse item failed: %s", item_id)
    finally:
        db.close()
