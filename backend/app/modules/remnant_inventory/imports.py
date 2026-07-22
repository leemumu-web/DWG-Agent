from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.files.interface import StoredFile
from app.modules.remnant_inventory.models import Remnant, RemnantImportBatch, RemnantImportItem
from app.platform.config.settings import settings
from app.platform.http.exceptions import AppHTTPException

REMNANT_SOURCE_EXTENSIONS = {".dwg", ".dxf"}


def register_import_batch(
    db: Session,
    *,
    actor_id: int,
    source_files: Sequence[StoredFile],
    max_files: int | None = None,
) -> RemnantImportBatch:
    limit = max_files if max_files is not None else settings.remnant_import_max_files
    if not source_files:
        raise AppHTTPException(422, "REMNANT_IMPORT_EMPTY", "At least one drawing is required.")
    if len(source_files) > limit:
        raise AppHTTPException(
            422,
            "REMNANT_IMPORT_TOO_MANY_FILES",
            "Import batch exceeds the configured file limit.",
            {"max_files": limit},
        )

    seen: dict[str, StoredFile] = {}
    for source in source_files:
        extension = source.file_ext.lower()
        if extension not in REMNANT_SOURCE_EXTENSIONS:
            raise AppHTTPException(
                415,
                "REMNANT_FILE_TYPE_NOT_ALLOWED",
                "Only DWG and DXF drawings can be imported.",
            )
        if source.sha256 in seen:
            raise AppHTTPException(
                409,
                "REMNANT_SOURCE_DUPLICATE_IN_BATCH",
                "The same source drawing appears more than once in this batch.",
                {"first_file_id": seen[source.sha256].id, "duplicate_file_id": source.id},
            )
        seen[source.sha256] = source

    existing = db.scalar(select(Remnant).where(Remnant.source_sha256.in_(list(seen))))
    if existing is not None:
        raise AppHTTPException(
            409,
            "REMNANT_SOURCE_DUPLICATE",
            "This source drawing already exists in the remnant inventory.",
            {"remnant_id": existing.id},
        )

    batch = RemnantImportBatch(
        created_by=actor_id,
        status="uploaded",
        total_count=len(source_files),
    )
    db.add(batch)
    db.flush()
    for source in source_files:
        db.add(
            RemnantImportItem(
                batch_id=batch.id,
                source_file_id=source.id,
                source_sha256=source.sha256,
                source_ext=source.file_ext.lower(),
                status="uploaded",
                attempt=1,
            )
        )
    db.flush()
    return batch
