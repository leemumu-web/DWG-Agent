from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.files.models import FileTransfer
from app.platform.database.mixins import utcnow
from app.platform.http.exceptions import AppHTTPException, not_found
from app.platform.storage.base import AbstractStorageBackend, StorageError

logger = logging.getLogger(__name__)
_PENDING_STORAGE_OBJECTS = "pending_storage_objects"
_PENDING_DESTRUCTIVE_TRANSFERS = "pending_destructive_transfers"

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


def _prepare_storage_transfer(
    db: Session,
    *,
    direction: str,
    operation: str,
    actor_user_id: int | None,
    request_id: str,
    batch_ref: str | None,
    bucket: str,
    storage_key: str,
    original_name: str,
    expected_bytes: int,
) -> tuple[str, bool]:
    """Create an in-progress transfer intent before an object write.

    MySQL uses an independent committed intent so a later metadata rollback can
    still settle compensation. SQLite's in-memory StaticPool cannot host an
    independent concurrent transaction, so tests prepare the row in the caller
    transaction while preserving the same state machine fields.
    """
    spec = TransferSpec(
        direction=direction,
        operation=operation,
        actor_user_id=actor_user_id,
        request_id=request_id,
        batch_ref=batch_ref,
        bucket=bucket,
        storage_key=storage_key,
        original_name=original_name,
        expected_bytes=expected_bytes,
    )
    if db.get_bind().dialect.name == "sqlite":
        snapshot = prepare_transfer_in_transaction(db, spec)
        row = db.scalar(
            select(FileTransfer).where(FileTransfer.transfer_uid == snapshot.transfer_uid)
        )
        assert row is not None
        row.status = "in_progress"
        row.started_at = row.started_at or utcnow()
        return row.transfer_uid, False

    factory = session_factory_for(db)
    snapshot = begin_transfer(factory, spec)
    mark_transfer_in_progress(
        factory,
        snapshot.transfer_uid,
        bucket=bucket,
        storage_key=storage_key,
        expected_bytes=expected_bytes,
    )
    return snapshot.transfer_uid, True


def prepare_generated_file_transfer(
    db: Session,
    *,
    actor_user_id: int | None,
    request_id: str,
    batch_ref: str | None,
    bucket: str,
    storage_key: str,
    original_name: str,
    expected_bytes: int,
) -> str:
    """Commit a generated-file transfer intent before its metadata transaction.

    This explicit boundary prevents a MySQL REPEATABLE READ transaction from
    observing a transfer row before an independent writer advances it, which
    otherwise raises error 1020 when the caller later locks that row.
    """
    snapshot = prepare_transfer_in_transaction(
        db,
        TransferSpec(
            direction="internal",
            operation="generated",
            actor_user_id=actor_user_id,
            request_id=request_id,
            batch_ref=batch_ref,
            bucket=bucket,
            storage_key=storage_key,
            original_name=original_name,
            expected_bytes=expected_bytes,
        ),
    )
    factory = session_factory_for(db)
    db.commit()
    try:
        mark_transfer_in_progress(
            factory,
            snapshot.transfer_uid,
            bucket=bucket,
            storage_key=storage_key,
            expected_bytes=expected_bytes,
        )
    except Exception:
        settle_transfer(
            factory,
            snapshot.transfer_uid,
            status="failed",
            transferred_bytes=0,
            error_code="TRANSFER_START_FAILED",
            error_message="Generated file transfer could not start.",
        )
        raise
    return snapshot.transfer_uid


def _settle_storage_write_failure(
    db: Session,
    transfer_uid: str,
    *,
    durable_intent: bool,
) -> None:
    if durable_intent:
        settle_transfer(
            session_factory_for(db),
            transfer_uid,
            status="failed",
            transferred_bytes=0,
            error_code="STORAGE_WRITE_FAILED",
            error_message="Object storage rejected the write before metadata commit.",
        )
        return
    row = db.scalar(
        select(FileTransfer).where(FileTransfer.transfer_uid == transfer_uid)
    )
    if row is not None:
        row.status = "failed"
        row.error_code = "STORAGE_WRITE_FAILED"
        row.error_message = "Object storage rejected the write before metadata commit."
        row.started_at = row.started_at or utcnow()
        row.finished_at = utcnow()


def _register_pending_storage_object(
    db: Session,
    storage: AbstractStorageBackend,
    bucket: str,
    storage_key: str,
    *,
    size_bytes: int,
    transfer_uid: str | None = None,
) -> None:
    pending = db.info.setdefault(_PENDING_STORAGE_OBJECTS, [])
    pending.append((storage, bucket, storage_key, size_bytes, transfer_uid))


def _discard_pending_storage_objects(db: Session) -> None:
    db.info.pop(_PENDING_STORAGE_OBJECTS, None)


def register_pending_destructive_transfer(
    db: Session,
    transfer_uid: str,
    *,
    transferred_bytes: int,
) -> None:
    """Settle an irreversible storage action only after the DB outcome is known."""
    pending = db.info.setdefault(_PENDING_DESTRUCTIVE_TRANSFERS, [])
    pending.append((transfer_uid, transferred_bytes))


def _settle_pending_destructive_transfers(db: Session, *, committed: bool) -> None:
    pending = db.info.pop(_PENDING_DESTRUCTIVE_TRANSFERS, [])
    for transfer_uid, transferred_bytes in pending:
        try:
            settle_transfer(
                session_factory_for(db),
                transfer_uid,
                status="succeeded" if committed else "compensation_required",
                transferred_bytes=transferred_bytes,
                error_code=None if committed else "PURGE_METADATA_COMMIT_FAILED",
                error_message=(
                    None
                    if committed
                    else "Objects were permanently removed but metadata did not commit."
                ),
            )
        except Exception:
            logger.exception(
                "Failed to settle destructive storage transfer %s after transaction end",
                transfer_uid,
            )


def _delete_pending_storage_objects(db: Session) -> None:
    pending = db.info.pop(_PENDING_STORAGE_OBJECTS, [])
    for storage, bucket, storage_key, size_bytes, transfer_uid in reversed(pending):
        status = "failed"
        error_code = "METADATA_TRANSACTION_ROLLED_BACK"
        error_message = "Stored object was removed after metadata transaction rollback."
        try:
            storage.delete_object(bucket, storage_key)
        except StorageError:
            status = "compensation_required"
            error_code = "STORAGE_COMPENSATION_REQUIRED"
            error_message = "Stored object could not be removed after metadata rollback."
            logger.exception(
                "Failed to compensate storage object after DB rollback: %s/%s",
                bucket,
                storage_key,
            )
        if transfer_uid:
            try:
                settle_transfer(
                    session_factory_for(db),
                    transfer_uid,
                    status=status,
                    transferred_bytes=size_bytes,
                    error_code=error_code,
                    error_message=error_message,
                )
            except Exception:
                logger.exception("Failed to settle rolled-back transfer %s", transfer_uid)


@event.listens_for(Session, "after_commit")
def _storage_after_commit(db: Session) -> None:
    _discard_pending_storage_objects(db)
    _settle_pending_destructive_transfers(db, committed=True)


@event.listens_for(Session, "after_rollback")
def _storage_after_rollback(db: Session) -> None:
    _delete_pending_storage_objects(db)
    _settle_pending_destructive_transfers(db, committed=False)


@event.listens_for(Session, "after_transaction_end")
def _storage_after_transaction_end(db: Session, transaction) -> None:
    if transaction.parent is None and not db.in_transaction():
        _delete_pending_storage_objects(db)
        _settle_pending_destructive_transfers(db, committed=False)
