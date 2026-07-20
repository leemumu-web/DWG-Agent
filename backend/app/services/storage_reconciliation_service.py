from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import mimetypes
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.files.interface import FileTransfer, StorageScanFinding, StorageScanRun, StoredFile
from app.platform.config.settings import settings
from app.platform.database.mixins import utcnow
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage.base import (
    AbstractStorageBackend,
    ObjectInfo,
    StorageError,
    StorageObjectNotFound,
)
from app.schemas.data_admin_schema import RemediationPreview, RemediationResult

REMEDIATION_ACTION_FINDING_TYPES = {
    "restore": "retained_deleted",
    "register_existing": "untracked_object",
    "soft_delete_missing": "missing_object",
    "purge_untracked": "untracked_object",
}
REMEDIATION_MAX_TARGETS = 100
REMEDIATION_MAX_BYTES = 1024 * 1024 * 1024
REMEDIATION_PREVIEW_TTL = timedelta(minutes=5)


def _prepare_purge_transfer(
    db: Session,
    *,
    actor_user_id: int,
    request_id: str,
    idempotency_key: str,
    preview_token: str,
    findings: list[StorageScanFinding],
    total_bytes: int,
) -> tuple[str, bool]:
    from app.modules.files.interface import (
        TransferSpec,
        begin_transfer,
        mark_transfer_in_progress,
        prepare_transfer_in_transaction,
        session_factory_for,
    )

    location_bucket = findings[0].bucket if len(findings) == 1 else None
    location_key = findings[0].storage_key if len(findings) == 1 else None
    spec = TransferSpec(
        direction="internal",
        operation="purge_untracked",
        actor_user_id=actor_user_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
        batch_ref=f"remediation:{hashlib.sha256(preview_token.encode()).hexdigest()[:16]}",
        bucket=location_bucket,
        storage_key=location_key,
        expected_bytes=total_bytes,
    )
    if db.get_bind().dialect.name == "sqlite":
        snapshot = prepare_transfer_in_transaction(db, spec)
        row = db.scalar(
            select(FileTransfer).where(
                FileTransfer.transfer_uid == snapshot.transfer_uid
            )
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
        bucket=location_bucket or "",
        storage_key=location_key or "",
        expected_bytes=total_bytes,
    )
    return snapshot.transfer_uid, True


def _load_file_snapshot(
    factory: sessionmaker[Session],
    buckets: list[str],
) -> dict[tuple[str, str], tuple[int, str, int]]:
    with factory() as db:
        rows = db.execute(
            select(
                StoredFile.id,
                StoredFile.bucket,
                StoredFile.storage_key,
                StoredFile.status,
                StoredFile.size_bytes,
            ).where(StoredFile.bucket.in_(buckets))
        ).all()
    return {
        (row.bucket, row.storage_key): (row.id, row.status, row.size_bytes)
        for row in rows
    }


def _load_object_snapshot(
    storage: AbstractStorageBackend,
    buckets: list[str],
) -> dict[tuple[str, str], ObjectInfo]:
    objects: dict[tuple[str, str], ObjectInfo] = {}
    for bucket in buckets:
        cursor: str | None = None
        while True:
            page = storage.list_objects(
                bucket,
                prefix="",
                cursor=cursor,
                page_size=200,
            )
            for item in page.items:
                objects[(bucket, item.storage_key)] = item
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
    return objects


def execute_scan_run(
    scan_run_id: int,
    *,
    factory: sessionmaker[Session],
    storage: AbstractStorageBackend,
    buckets: list[str],
) -> None:
    scoped_buckets = buckets
    with factory.begin() as db:
        run = db.get(StorageScanRun, scan_run_id)
        if run is None or run.status in {"succeeded", "failed", "cancelled"}:
            return
        if run.scope_bucket is not None:
            scoped_buckets = [run.scope_bucket]
        run.status = "running"
        run.started_at = run.started_at or utcnow()
        run.error_code = None
        run.error_message = None

    try:
        files = _load_file_snapshot(factory, scoped_buckets)
        objects = _load_object_snapshot(storage, scoped_buckets)
        findings: list[StorageScanFinding] = []
        consistent_count = 0
        retained_deleted_count = 0
        missing_object_count = 0
        size_mismatch_count = 0

        for location, (file_id, file_status, database_size) in files.items():
            bucket, storage_key = location
            object_info = objects.get(location)
            if file_status == "deleted":
                if object_info is not None:
                    retained_deleted_count += 1
                    findings.append(
                        StorageScanFinding(
                            run_id=scan_run_id,
                            finding_type="retained_deleted",
                            bucket=bucket,
                            storage_key=storage_key,
                            file_id=file_id,
                            file_status=file_status,
                            database_size_bytes=database_size,
                            object_size_bytes=object_info.size_bytes,
                            object_modified_at=object_info.last_modified,
                        )
                    )
                continue
            if file_status != "available":
                continue
            if object_info is None:
                missing_object_count += 1
                findings.append(
                    StorageScanFinding(
                        run_id=scan_run_id,
                        finding_type="missing_object",
                        bucket=bucket,
                        storage_key=storage_key,
                        file_id=file_id,
                        file_status=file_status,
                        database_size_bytes=database_size,
                    )
                )
            elif object_info.size_bytes != database_size:
                size_mismatch_count += 1
                findings.append(
                    StorageScanFinding(
                        run_id=scan_run_id,
                        finding_type="size_mismatch",
                        bucket=bucket,
                        storage_key=storage_key,
                        file_id=file_id,
                        file_status=file_status,
                        database_size_bytes=database_size,
                        object_size_bytes=object_info.size_bytes,
                        object_modified_at=object_info.last_modified,
                    )
                )
            else:
                consistent_count += 1

        untracked_object_count = 0
        for location, object_info in objects.items():
            if location in files:
                continue
            untracked_object_count += 1
            findings.append(
                StorageScanFinding(
                    run_id=scan_run_id,
                    finding_type="untracked_object",
                    bucket=object_info.bucket,
                    storage_key=object_info.storage_key,
                    object_size_bytes=object_info.size_bytes,
                    object_modified_at=object_info.last_modified,
                )
            )

        with factory.begin() as db:
            run = db.get(StorageScanRun, scan_run_id)
            if run is None or run.status == "cancelled":
                return
            db.execute(
                delete(StorageScanFinding).where(StorageScanFinding.run_id == scan_run_id)
            )
            db.add_all(findings)
            run.scanned_files = len(files)
            run.scanned_objects = len(objects)
            run.consistent_count = consistent_count
            run.retained_deleted_count = retained_deleted_count
            run.missing_object_count = missing_object_count
            run.untracked_object_count = untracked_object_count
            run.size_mismatch_count = size_mismatch_count
            run.error_count = 0
            run.status = "succeeded"
            run.finished_at = utcnow()
    except Exception:
        with factory.begin() as db:
            run = db.get(StorageScanRun, scan_run_id)
            if run is not None and run.status != "cancelled":
                run.status = "failed"
                run.error_count = 1
                run.error_code = "STORAGE_SCAN_FAILED"
                run.error_message = "Storage consistency scan could not be completed."
                run.finished_at = utcnow()
        raise


def _urlsafe_encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _urlsafe_decode(payload: str) -> bytes:
    padding = "=" * (-len(payload) % 4)
    return base64.urlsafe_b64decode(payload + padding)


def _sign_preview(payload: dict) -> str:
    encoded = _urlsafe_encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(
        settings.jwt_secret_key.encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded}.{_urlsafe_encode(signature)}"


def _decode_preview(token: str) -> dict:
    try:
        encoded, supplied_signature = token.split(".", 1)
        expected_signature = hmac.new(
            settings.jwt_secret_key.encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(
            expected_signature,
            _urlsafe_decode(supplied_signature),
        ):
            raise ValueError("signature mismatch")
        payload = json.loads(_urlsafe_decode(encoded))
    except (
        binascii.Error,
        UnicodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        raise AppHTTPException(
            422,
            "INVALID_REMEDIATION_PREVIEW",
            "Remediation preview token is invalid.",
        ) from exc
    if not isinstance(payload, dict):
        raise AppHTTPException(
            422,
            "INVALID_REMEDIATION_PREVIEW",
            "Remediation preview token is invalid.",
        )
    try:
        finding_ids = payload["finding_ids"]
        metadata = payload["metadata"]
        target_digest = payload["target_digest"]
        valid = (
            isinstance(payload["actor_user_id"], int)
            and not isinstance(payload["actor_user_id"], bool)
            and payload["actor_user_id"] > 0
            and isinstance(payload["action"], str)
            and isinstance(finding_ids, list)
            and bool(finding_ids)
            and len(finding_ids) <= REMEDIATION_MAX_TARGETS
            and all(
                isinstance(item, int) and not isinstance(item, bool) and item > 0
                for item in finding_ids
            )
            and len(set(finding_ids)) == len(finding_ids)
            and isinstance(target_digest, str)
            and len(target_digest) == 64
            and all(char in "0123456789abcdef" for char in target_digest)
            and isinstance(payload["count"], int)
            and payload["count"] == len(finding_ids)
            and isinstance(payload["total_bytes"], int)
            and 0 <= payload["total_bytes"] <= REMEDIATION_MAX_BYTES
            and isinstance(payload["expires_at"], int)
            and isinstance(metadata, dict)
            and all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in metadata.items()
            )
        )
    except KeyError as exc:
        raise AppHTTPException(
            422,
            "INVALID_REMEDIATION_PREVIEW",
            "Remediation preview token is invalid.",
        ) from exc
    if not valid:
        raise AppHTTPException(
            422,
            "INVALID_REMEDIATION_PREVIEW",
            "Remediation preview token is invalid.",
        )
    return payload


def _load_findings(
    db: Session,
    finding_ids: list[int],
    *,
    for_update: bool = False,
) -> list[StorageScanFinding]:
    statement = (
        select(StorageScanFinding)
        .where(StorageScanFinding.id.in_(finding_ids))
        .order_by(StorageScanFinding.id)
    )
    if for_update:
        statement = statement.with_for_update()
    findings = list(db.scalars(statement).all())
    if len(findings) != len(finding_ids):
        raise AppHTTPException(
            404,
            "REMEDIATION_FINDING_NOT_FOUND",
            "One or more storage findings no longer exist.",
        )
    if any(item.resolution_status != "open" for item in findings):
        raise AppHTTPException(
            409,
            "REMEDIATION_ALREADY_RESOLVED",
            "One or more storage findings are already resolved.",
        )
    return findings


def _target_snapshot(
    db: Session,
    storage: AbstractStorageBackend,
    findings: list[StorageScanFinding],
) -> tuple[str, int]:
    target_rows: list[dict] = []
    total_bytes = 0
    for finding in findings:
        stored_file = db.get(StoredFile, finding.file_id) if finding.file_id else None
        try:
            object_info = storage.stat_object(finding.bucket, finding.storage_key)
        except StorageObjectNotFound:
            object_info = None
        object_size = object_info.size_bytes if object_info else None
        total_bytes += int(
            object_size
            if object_size is not None
            else (stored_file.size_bytes if stored_file is not None else 0)
        )
        target_rows.append(
            {
                "finding_id": finding.id,
                "finding_type": finding.finding_type,
                "bucket": finding.bucket,
                "storage_key": finding.storage_key,
                "resolution_status": finding.resolution_status,
                "file_id": stored_file.id if stored_file else None,
                "file_status": stored_file.status if stored_file else None,
                "file_size": stored_file.size_bytes if stored_file else None,
                "deleted_at": (
                    stored_file.deleted_at.isoformat()
                    if stored_file is not None and stored_file.deleted_at
                    else None
                ),
                "object_size": object_size,
                "object_modified_at": (
                    object_info.last_modified.isoformat()
                    if object_info is not None and object_info.last_modified
                    else None
                ),
            }
        )
    digest = hashlib.sha256(
        json.dumps(target_rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return digest, total_bytes


def _validate_action_targets(
    findings: list[StorageScanFinding],
    action: str,
) -> None:
    required_type = REMEDIATION_ACTION_FINDING_TYPES.get(action)
    if required_type is None:
        raise AppHTTPException(
            422,
            "INVALID_REMEDIATION_ACTION",
            "Remediation action is not supported.",
        )
    if any(item.finding_type != required_type for item in findings):
        raise AppHTTPException(
            422,
            "REMEDIATION_ACTION_MISMATCH",
            "Selected findings are not compatible with this action.",
        )


def preview_remediation(
    db: Session,
    storage: AbstractStorageBackend,
    *,
    actor_user_id: int,
    finding_ids: list[int],
    action: str,
    metadata: dict[str, str] | None = None,
) -> RemediationPreview:
    unique_ids = sorted(set(finding_ids))
    if not unique_ids:
        raise AppHTTPException(422, "REMEDIATION_EMPTY", "Select at least one finding.")
    if len(unique_ids) > REMEDIATION_MAX_TARGETS:
        raise AppHTTPException(
            413,
            "REMEDIATION_TARGET_LIMIT",
            "Too many findings were selected for one remediation.",
        )
    findings = _load_findings(db, unique_ids)
    _validate_action_targets(findings, action)
    metadata = metadata or {}
    if action == "register_existing":
        if len(findings) != 1:
            raise AppHTTPException(
                422,
                "REGISTER_EXISTING_SINGLE_TARGET",
                "Register one existing object at a time.",
            )
        original_name = metadata.get("original_name", "").strip()
        if not original_name or Path(original_name).name != original_name:
            raise AppHTTPException(
                422,
                "REGISTER_EXISTING_NAME_REQUIRED",
                "A plain original_name is required to register an existing object.",
            )
        metadata = {"original_name": original_name}
    digest, total_bytes = _target_snapshot(db, storage, findings)
    if total_bytes > REMEDIATION_MAX_BYTES:
        raise AppHTTPException(
            413,
            "REMEDIATION_BYTE_LIMIT",
            "Selected findings exceed the remediation byte limit.",
        )
    expires_at = datetime.now(UTC) + REMEDIATION_PREVIEW_TTL
    payload = {
        "actor_user_id": actor_user_id,
        "action": action,
        "finding_ids": unique_ids,
        "target_digest": digest,
        "count": len(unique_ids),
        "total_bytes": total_bytes,
        "expires_at": int(expires_at.timestamp()),
        "metadata": metadata,
    }
    risk = {
        "restore": "Restore soft-deleted registrations whose objects still exist.",
        "register_existing": "Create a new MySQL registration from the current object bytes.",
        "soft_delete_missing": "Mark registrations whose objects are missing as deleted.",
        "purge_untracked": "Permanently delete unregistered storage objects.",
    }[action]
    return RemediationPreview(
        action=action,
        finding_ids=unique_ids,
        count=len(unique_ids),
        total_bytes=total_bytes,
        risk=risk,
        expires_at=expires_at,
        confirmation_word="PURGE" if action == "purge_untracked" else None,
        token=_sign_preview(payload),
    )


def _hash_object(
    storage: AbstractStorageBackend,
    bucket: str,
    storage_key: str,
) -> tuple[int, str, str]:
    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    size = 0
    for chunk in storage.iter_file(bucket, storage_key):
        size += len(chunk)
        sha256.update(chunk)
        md5.update(chunk)
    return size, sha256.hexdigest(), md5.hexdigest()


def execute_remediation(
    db: Session,
    storage: AbstractStorageBackend,
    *,
    actor_user_id: int,
    preview_token: str,
    idempotency_key: str,
    request_id: str,
    confirmation_word: str | None = None,
) -> RemediationResult:
    if not idempotency_key.strip():
        raise AppHTTPException(
            422,
            "IDEMPOTENCY_KEY_REQUIRED",
            "An idempotency key is required.",
        )
    payload = _decode_preview(preview_token)
    if payload.get("actor_user_id") != actor_user_id:
        raise AppHTTPException(
            403,
            "REMEDIATION_PREVIEW_ACTOR_MISMATCH",
            "Remediation preview belongs to a different actor.",
        )
    if int(payload.get("expires_at", 0)) < int(datetime.now(UTC).timestamp()):
        raise AppHTTPException(
            409,
            "REMEDIATION_PREVIEW_EXPIRED",
            "Remediation preview has expired.",
        )
    action = str(payload.get("action", ""))
    _validate_action_targets([], action)
    if action == "purge_untracked" and confirmation_word != "PURGE":
        raise AppHTTPException(
            422,
            "REMEDIATION_CONFIRMATION_REQUIRED",
            "Enter the preview confirmation word before purging objects.",
        )
    existing = db.scalar(
        select(FileTransfer).where(
            FileTransfer.actor_user_id == actor_user_id,
            FileTransfer.operation == action,
            FileTransfer.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.status in {"prepared", "in_progress"}:
            raise AppHTTPException(
                409,
                "REMEDIATION_IN_PROGRESS",
                "A remediation with this idempotency key is still in progress.",
                {"transfer_uid": existing.transfer_uid},
            )
        return RemediationResult(
            transfer_uid=existing.transfer_uid,
            action=action,
            status=existing.status,
            count=int(payload.get("count", 0)),
            file_ids=[existing.file_id] if existing.file_id is not None else [],
        )
    finding_ids = [int(item) for item in payload.get("finding_ids", [])]
    findings = _load_findings(db, finding_ids, for_update=True)
    _validate_action_targets(findings, action)
    current_digest, total_bytes = _target_snapshot(db, storage, findings)
    if current_digest != payload.get("target_digest"):
        raise AppHTTPException(
            409,
            "REMEDIATION_PREVIEW_STALE",
            "Storage or database state changed after preview.",
        )

    purge_transfer_uid: str | None = None
    durable_purge_transfer = False
    if action == "purge_untracked":
        purge_transfer_uid, durable_purge_transfer = _prepare_purge_transfer(
            db,
            actor_user_id=actor_user_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            preview_token=preview_token,
            findings=findings,
            total_bytes=total_bytes,
        )
        if durable_purge_transfer:
            from app.modules.files.interface import (
                register_pending_destructive_transfer,
            )

            register_pending_destructive_transfer(
                db,
                purge_transfer_uid,
                transferred_bytes=total_bytes,
            )

    metadata = payload.get("metadata") or {}
    affected_file_ids: list[int] = []
    deleted_bytes = 0
    try:
        for finding in findings:
            stored_file = db.get(StoredFile, finding.file_id) if finding.file_id else None
            if stored_file is not None:
                db.refresh(stored_file, with_for_update=True)
            if action == "restore":
                if stored_file is None or stored_file.status != "deleted":
                    raise AppHTTPException(409, "REMEDIATION_PREVIEW_STALE", "File changed.")
                stored_file.status = "available"
                stored_file.deleted_at = None
                affected_file_ids.append(stored_file.id)
            elif action == "soft_delete_missing":
                if stored_file is None or stored_file.status != "available":
                    raise AppHTTPException(409, "REMEDIATION_PREVIEW_STALE", "File changed.")
                stored_file.status = "deleted"
                stored_file.deleted_at = utcnow()
                affected_file_ids.append(stored_file.id)
            elif action == "register_existing":
                if db.scalar(
                    select(StoredFile).where(
                        StoredFile.bucket == finding.bucket,
                        StoredFile.storage_key == finding.storage_key,
                    )
                ) is not None:
                    raise AppHTTPException(409, "REMEDIATION_PREVIEW_STALE", "Object was registered.")
                original_name = str(metadata["original_name"])
                size, sha256, md5 = _hash_object(storage, finding.bucket, finding.storage_key)
                file_ext = Path(original_name).suffix.lower() or ".bin"
                content_type = mimetypes.guess_type(original_name)[0] or "application/octet-stream"
                stored_file = StoredFile(
                    bucket=finding.bucket,
                    storage_key=finding.storage_key,
                    original_name=original_name,
                    file_ext=file_ext,
                    content_type=content_type,
                    size_bytes=size,
                    sha256=sha256,
                    md5=md5,
                    uploaded_by=actor_user_id,
                    status="available",
                )
                db.add(stored_file)
                db.flush()
                finding.file_id = stored_file.id
                affected_file_ids.append(stored_file.id)
            elif action == "purge_untracked":
                storage.delete_object(finding.bucket, finding.storage_key)
                deleted_bytes += finding.object_size_bytes or 0
            finding.resolution_status = "resolved"
            finding.resolution_action = action
            finding.resolved_by = actor_user_id
            finding.resolved_at = utcnow()
    except StorageError as exc:
        if durable_purge_transfer and purge_transfer_uid is not None:
            from app.modules.files.interface import session_factory_for, settle_transfer

            settle_transfer(
                session_factory_for(db),
                purge_transfer_uid,
                status="compensation_required" if deleted_bytes else "failed",
                transferred_bytes=deleted_bytes,
                error_code=(
                    "PURGE_PARTIAL_FAILURE" if deleted_bytes else "STORAGE_DELETE_FAILED"
                ),
                error_message="Storage purge did not remove every previewed object.",
            )
        raise AppHTTPException(
            503,
            "STORAGE_DELETE_FAILED",
            "Storage remediation could not remove every selected object.",
        ) from exc

    if purge_transfer_uid is not None:
        transfer_uid = purge_transfer_uid
        if not durable_purge_transfer:
            transfer = db.scalar(
                select(FileTransfer).where(
                    FileTransfer.transfer_uid == purge_transfer_uid
                )
            )
            assert transfer is not None
            transfer.status = "succeeded"
            transfer.transferred_bytes = total_bytes
            transfer.finished_at = utcnow()
    else:
        transfer = FileTransfer(
            transfer_uid=str(uuid4()),
            direction="internal",
            operation=action,
            status="succeeded",
            file_id=affected_file_ids[0] if len(affected_file_ids) == 1 else None,
            batch_ref=f"remediation:{hashlib.sha256(preview_token.encode()).hexdigest()[:16]}",
            actor_user_id=actor_user_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            bucket=findings[0].bucket if len(findings) == 1 else None,
            storage_key=findings[0].storage_key if len(findings) == 1 else None,
            original_name=(str(metadata.get("original_name")) if metadata else None),
            expected_bytes=total_bytes,
            transferred_bytes=total_bytes,
            started_at=utcnow(),
            finished_at=utcnow(),
        )
        db.add(transfer)
        db.flush()
        transfer_uid = transfer.transfer_uid
    return RemediationResult(
        transfer_uid=transfer_uid,
        action=action,
        status="succeeded",
        count=len(findings),
        file_ids=affected_file_ids,
    )
