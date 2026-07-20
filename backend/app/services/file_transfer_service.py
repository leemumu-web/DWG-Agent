from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models.file_transfer import FileTransfer
from app.platform.database.mixins import utcnow
from app.platform.http.exceptions import AppHTTPException, not_found

ACTIVE_TRANSFER_STATUSES = {"prepared", "in_progress"}
TERMINAL_TRANSFER_STATUSES = {
    "succeeded",
    "failed",
    "cancelled",
    "compensation_required",
}


@dataclass(frozen=True)
class TransferSpec:
    direction: str
    operation: str
    actor_user_id: int | None
    request_id: str
    idempotency_key: str | None = None
    file_id: int | None = None
    batch_ref: str | None = None
    bucket: str | None = None
    storage_key: str | None = None
    original_name: str | None = None
    expected_bytes: int | None = None


@dataclass(frozen=True)
class TransferSnapshot:
    transfer_uid: str
    direction: str
    operation: str
    status: str
    file_id: int | None
    bucket: str | None
    storage_key: str | None
    original_name: str | None
    expected_bytes: int | None
    transferred_bytes: int
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_model(cls, row: FileTransfer) -> "TransferSnapshot":
        return cls(
            transfer_uid=row.transfer_uid,
            direction=row.direction,
            operation=row.operation,
            status=row.status,
            file_id=row.file_id,
            bucket=row.bucket,
            storage_key=row.storage_key,
            original_name=row.original_name,
            expected_bytes=row.expected_bytes,
            transferred_bytes=row.transferred_bytes,
            error_code=row.error_code,
            error_message=row.error_message,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )


def _idempotent_transfer(db: Session, spec: TransferSpec) -> FileTransfer | None:
    if not spec.idempotency_key:
        return None
    actor_condition = (
        FileTransfer.actor_user_id.is_(None)
        if spec.actor_user_id is None
        else FileTransfer.actor_user_id == spec.actor_user_id
    )
    return db.scalar(
        select(FileTransfer).where(
            actor_condition,
            FileTransfer.operation == spec.operation,
            FileTransfer.idempotency_key == spec.idempotency_key,
        )
    )


def begin_transfer(
    factory: sessionmaker[Session],
    spec: TransferSpec,
) -> TransferSnapshot:
    with factory.begin() as db:
        return prepare_transfer_in_transaction(db, spec)


def prepare_transfer_in_transaction(db: Session, spec: TransferSpec) -> TransferSnapshot:
    from uuid import uuid4

    existing = _idempotent_transfer(db, spec)
    if existing is not None:
        if existing.status in ACTIVE_TRANSFER_STATUSES:
            raise AppHTTPException(
                409,
                "TRANSFER_IN_PROGRESS",
                "An operation with this idempotency key is already in progress.",
                {"transfer_uid": existing.transfer_uid},
            )
        return TransferSnapshot.from_model(existing)

    row = FileTransfer(
        transfer_uid=str(uuid4()),
        direction=spec.direction,
        operation=spec.operation,
        status="prepared",
        file_id=spec.file_id,
        batch_ref=spec.batch_ref,
        actor_user_id=spec.actor_user_id,
        request_id=spec.request_id,
        idempotency_key=spec.idempotency_key,
        bucket=spec.bucket,
        storage_key=spec.storage_key,
        original_name=spec.original_name,
        expected_bytes=spec.expected_bytes,
    )
    db.add(row)
    db.flush()
    return TransferSnapshot.from_model(row)


def session_factory_for(db: Session) -> sessionmaker[Session]:
    return sessionmaker(
        bind=db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def _transfer_for_update(db: Session, transfer_uid: str) -> FileTransfer:
    row = db.scalar(
        select(FileTransfer)
        .where(FileTransfer.transfer_uid == transfer_uid)
        .with_for_update()
    )
    if row is None:
        raise not_found("FileTransfer")
    return row


def mark_transfer_in_progress(
    factory: sessionmaker[Session],
    transfer_uid: str,
    *,
    bucket: str,
    storage_key: str,
    expected_bytes: int,
) -> TransferSnapshot:
    with factory.begin() as db:
        row = _transfer_for_update(db, transfer_uid)
        if row.status not in ACTIVE_TRANSFER_STATUSES:
            return TransferSnapshot.from_model(row)
        row.status = "in_progress"
        row.bucket = bucket
        row.storage_key = storage_key
        row.expected_bytes = expected_bytes
        row.started_at = row.started_at or utcnow()
        db.flush()
        return TransferSnapshot.from_model(row)


def complete_transfer_in_transaction(
    db: Session,
    transfer_uid: str,
    *,
    file_id: int | None,
    bucket: str,
    storage_key: str,
    original_name: str,
    transferred_bytes: int,
) -> TransferSnapshot:
    row = _transfer_for_update(db, transfer_uid)
    if row.status == "succeeded":
        return TransferSnapshot.from_model(row)
    if row.status not in ACTIVE_TRANSFER_STATUSES:
        raise AppHTTPException(
            409,
            "TRANSFER_NOT_COMPLETABLE",
            f"Transfer cannot be completed from status {row.status}.",
        )
    row.status = "succeeded"
    row.file_id = file_id
    row.bucket = bucket
    row.storage_key = storage_key
    row.original_name = original_name
    row.expected_bytes = row.expected_bytes if row.expected_bytes is not None else transferred_bytes
    row.transferred_bytes = transferred_bytes
    row.started_at = row.started_at or utcnow()
    row.finished_at = utcnow()
    row.error_code = None
    row.error_message = None
    db.flush()
    return TransferSnapshot.from_model(row)


def complete_reused_transfer_in_transaction(
    db: Session,
    transfer_uid: str,
    *,
    operation: str,
    file_id: int,
    bucket: str,
    storage_key: str,
    original_name: str,
) -> TransferSnapshot:
    """Settle an active intent that reused an already registered object."""
    row = _transfer_for_update(db, transfer_uid)
    if row.status == "succeeded":
        return TransferSnapshot.from_model(row)
    if row.status not in ACTIVE_TRANSFER_STATUSES:
        raise AppHTTPException(
            409,
            "TRANSFER_NOT_COMPLETABLE",
            f"Transfer cannot be completed from status {row.status}.",
        )
    row.operation = operation
    return complete_transfer_in_transaction(
        db,
        transfer_uid,
        file_id=file_id,
        bucket=bucket,
        storage_key=storage_key,
        original_name=original_name,
        transferred_bytes=0,
    )


def settle_transfer(
    factory: sessionmaker[Session],
    transfer_uid: str,
    *,
    status: str,
    transferred_bytes: int,
    error_code: str | None = None,
    error_message: str | None = None,
) -> TransferSnapshot:
    if status not in TERMINAL_TRANSFER_STATUSES:
        raise ValueError(f"Unsupported terminal transfer status: {status}")
    with factory.begin() as db:
        row = _transfer_for_update(db, transfer_uid)
        if row.status in TERMINAL_TRANSFER_STATUSES:
            return TransferSnapshot.from_model(row)
        row.status = status
        row.transferred_bytes = transferred_bytes
        row.started_at = row.started_at or utcnow()
        row.finished_at = utcnow()
        row.error_code = error_code
        row.error_message = error_message[:1000] if error_message else None
        db.flush()
        return TransferSnapshot.from_model(row)


def settle_stream(
    factory: sessionmaker[Session],
    transfer_uid: str,
    chunks: Iterator[bytes],
) -> Iterator[bytes]:
    transferred_bytes = 0
    try:
        for chunk in chunks:
            transferred_bytes += len(chunk)
            yield chunk
    except GeneratorExit:
        settle_transfer(
            factory,
            transfer_uid,
            status="cancelled",
            transferred_bytes=transferred_bytes,
            error_code="DOWNLOAD_CANCELLED",
            error_message="Download stream was closed before completion.",
        )
        raise
    except Exception:
        settle_transfer(
            factory,
            transfer_uid,
            status="failed",
            transferred_bytes=transferred_bytes,
            error_code="STORAGE_READ_FAILED",
            error_message="Stored object could not be read during download.",
        )
        raise
    else:
        settle_transfer(
            factory,
            transfer_uid,
            status="succeeded",
            transferred_bytes=transferred_bytes,
        )
