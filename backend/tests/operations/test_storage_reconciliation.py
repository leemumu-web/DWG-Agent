from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.bootstrap.seed import init_db
from app.modules.files.interface import (
    FileTransfer,
    StorageScanFinding,
    StorageScanRun,
    StoredFile,
    register_pending_destructive_transfer,
)
from app.modules.operations.storage_reconciliation.remediation import (
    _sign_preview,
    execute_remediation,
    preview_remediation,
)
from app.modules.operations.storage_reconciliation.scanning import execute_scan_run
from app.platform.database.mixins import utcnow
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage.local import LocalFileStorage


def _factory(db):
    return sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)


def _file(db, *, bucket: str, key: str, status: str = "available", size: int = 3):
    row = StoredFile(
        bucket=bucket,
        storage_key=key,
        original_name=key.rsplit("/", 1)[-1],
        file_ext=".dwg",
        content_type="application/acad",
        size_bytes=size,
        sha256="a" * 64,
        md5="b" * 32,
        status=status,
    )
    db.add(row)
    db.flush()
    return row


def _put(storage, bucket: str, key: str, payload: bytes):
    storage.put_fileobj(
        bucket,
        key,
        BytesIO(payload),
        length=len(payload),
        content_type="application/octet-stream",
    )


def _run(db, storage, buckets: list[str], *, scope_bucket: str | None = None):
    run = StorageScanRun(
        backend="local",
        status="queued",
        scope_bucket=scope_bucket,
    )
    db.add(run)
    db.commit()
    execute_scan_run(run.id, factory=_factory(db), storage=storage, buckets=buckets)
    db.expire_all()
    return db.get(StorageScanRun, run.id)


def test_scan_compares_bucket_and_key_not_key_alone(db, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    registered = _file(db, bucket="bucket-a", key="same.dwg")
    db.commit()
    _put(storage, "bucket-b", "same.dwg", b"abc")

    run = _run(db, storage, ["bucket-a", "bucket-b"])

    findings = db.scalars(
        select(StorageScanFinding)
        .where(StorageScanFinding.run_id == run.id)
        .order_by(StorageScanFinding.finding_type)
    ).all()
    assert {(item.finding_type, item.bucket) for item in findings} == {
        ("missing_object", "bucket-a"),
        ("untracked_object", "bucket-b"),
    }
    assert next(item for item in findings if item.bucket == "bucket-a").file_id == registered.id


def test_scan_classifies_deleted_object_as_retained(db, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    row = _file(db, bucket="dwg-original", key="deleted.dwg", status="deleted")
    db.commit()
    _put(storage, row.bucket, row.storage_key, b"abc")

    run = _run(db, storage, ["dwg-original"])

    finding = db.scalar(select(StorageScanFinding).where(StorageScanFinding.run_id == run.id))
    assert finding is not None
    assert finding.finding_type == "retained_deleted"
    assert run.retained_deleted_count == 1
    assert run.untracked_object_count == 0


def test_scan_counts_consistent_and_size_mismatch(db, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    _file(db, bucket="dwg-original", key="ok.dwg", size=3)
    mismatch = _file(db, bucket="dwg-original", key="bad.dwg", size=99)
    db.commit()
    _put(storage, "dwg-original", "ok.dwg", b"abc")
    _put(storage, "dwg-original", "bad.dwg", b"abc")

    run = _run(db, storage, ["dwg-original"])

    assert run.status == "succeeded"
    assert run.scanned_files == 2
    assert run.scanned_objects == 2
    assert run.consistent_count == 1
    assert run.size_mismatch_count == 1
    finding = db.scalar(
        select(StorageScanFinding).where(
            StorageScanFinding.run_id == run.id,
            StorageScanFinding.finding_type == "size_mismatch",
        )
    )
    assert finding is not None
    assert finding.file_id == mismatch.id
    assert finding.database_size_bytes == 99
    assert finding.object_size_bytes == 3


def test_scan_run_scope_restricts_configured_buckets(db, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    _file(db, bucket="bucket-a", key="tracked-a.dwg")
    _file(db, bucket="bucket-b", key="tracked-b.dwg")
    db.commit()
    _put(storage, "bucket-a", "tracked-a.dwg", b"abc")
    _put(storage, "bucket-b", "orphan-b.dwg", b"abc")

    run = _run(
        db,
        storage,
        ["bucket-a", "bucket-b"],
        scope_bucket="bucket-a",
    )

    findings = db.scalars(
        select(StorageScanFinding).where(StorageScanFinding.run_id == run.id)
    ).all()
    assert run.scanned_files == 1
    assert run.scanned_objects == 1
    assert run.consistent_count == 1
    assert findings == []


def test_execute_rejects_changed_target_after_preview(db, tmp_path):
    init_db()
    storage = LocalFileStorage(tmp_path / "storage")
    _put(storage, "dwg-original", "orphan/purge.dwg", b"abc")
    run = _run(db, storage, ["dwg-original"])
    finding = db.scalar(select(StorageScanFinding).where(StorageScanFinding.run_id == run.id))
    preview = preview_remediation(
        db,
        storage,
        actor_user_id=1,
        finding_ids=[finding.id],
        action="purge_untracked",
    )
    storage.delete_object(finding.bucket, finding.storage_key)

    with pytest.raises(AppHTTPException) as exc:
        execute_remediation(
            db,
            storage,
            actor_user_id=1,
            preview_token=preview.token,
            idempotency_key="purge-stale-1",
            request_id="req-purge-stale",
            confirmation_word="PURGE",
        )

    assert exc.value.detail["code"] == "REMEDIATION_PREVIEW_STALE"


def test_execute_rejects_signed_preview_with_invalid_payload_shape(db, tmp_path):
    init_db()
    storage = LocalFileStorage(tmp_path / "storage")
    token = _sign_preview(
        {
            "actor_user_id": 1,
            "action": "purge_untracked",
            "finding_ids": "not-a-list",
            "target_digest": "0" * 64,
            "count": 1,
            "total_bytes": 0,
            "expires_at": int((datetime.now(UTC) + timedelta(minutes=1)).timestamp()),
            "metadata": {},
        }
    )

    with pytest.raises(AppHTTPException) as exc:
        execute_remediation(
            db,
            storage,
            actor_user_id=1,
            preview_token=token,
            idempotency_key="invalid-preview-shape",
            request_id="req-invalid-preview-shape",
            confirmation_word="PURGE",
        )

    assert exc.value.detail["code"] == "INVALID_REMEDIATION_PREVIEW"


def test_register_existing_computes_digest_and_is_idempotent(db, tmp_path):
    init_db()
    storage = LocalFileStorage(tmp_path / "storage")
    payload = b"0\nSECTION\n2\nENTITIES\n0\nEOF\n"
    _put(storage, "dxf-original", "orphan/recovered-object", payload)
    run = _run(db, storage, ["dxf-original"])
    finding = db.scalar(select(StorageScanFinding).where(StorageScanFinding.run_id == run.id))
    preview = preview_remediation(
        db,
        storage,
        actor_user_id=1,
        finding_ids=[finding.id],
        action="register_existing",
        metadata={"original_name": "recovered.dxf"},
    )

    first = execute_remediation(
        db,
        storage,
        actor_user_id=1,
        preview_token=preview.token,
        idempotency_key="register-existing-1",
        request_id="req-register-existing",
    )
    db.commit()
    second = execute_remediation(
        db,
        storage,
        actor_user_id=1,
        preview_token=preview.token,
        idempotency_key="register-existing-1",
        request_id="req-register-existing-retry",
    )

    stored_file = db.get(StoredFile, first.file_ids[0])
    assert first.transfer_uid == second.transfer_uid
    assert stored_file.original_name == "recovered.dxf"
    assert stored_file.size_bytes == len(payload)
    assert stored_file.sha256 == hashlib.sha256(payload).hexdigest()
    assert stored_file.bucket == "dxf-original"
    assert stored_file.storage_key == "orphan/recovered-object"


def test_restore_clears_deleted_at_and_records_transfer(db, tmp_path):
    init_db()
    storage = LocalFileStorage(tmp_path / "storage")
    stored_file = _file(
        db,
        bucket="dwg-original",
        key="retained/restore.dwg",
        status="deleted",
    )
    stored_file.deleted_at = utcnow()
    db.commit()
    _put(storage, stored_file.bucket, stored_file.storage_key, b"abc")
    run = _run(db, storage, ["dwg-original"])
    finding = db.scalar(select(StorageScanFinding).where(StorageScanFinding.run_id == run.id))
    preview = preview_remediation(
        db,
        storage,
        actor_user_id=1,
        finding_ids=[finding.id],
        action="restore",
    )

    result = execute_remediation(
        db,
        storage,
        actor_user_id=1,
        preview_token=preview.token,
        idempotency_key="restore-retained-1",
        request_id="req-restore-retained",
    )
    db.commit()
    db.refresh(stored_file)
    db.refresh(finding)

    assert stored_file.status == "available"
    assert stored_file.deleted_at is None
    assert finding.resolution_status == "resolved"
    assert finding.resolution_action == "restore"
    transfer = db.scalar(
        select(FileTransfer).where(FileTransfer.transfer_uid == result.transfer_uid)
    )
    assert transfer.operation == "restore"
    assert transfer.status == "succeeded"


def test_soft_delete_missing_sets_deleted_at_and_records_transfer(db, tmp_path):
    init_db()
    storage = LocalFileStorage(tmp_path / "storage")
    stored_file = _file(
        db,
        bucket="dwg-original",
        key="missing/soft-delete.dwg",
        status="available",
    )
    db.commit()
    run = _run(db, storage, ["dwg-original"])
    finding = db.scalar(select(StorageScanFinding).where(StorageScanFinding.run_id == run.id))
    preview = preview_remediation(
        db,
        storage,
        actor_user_id=1,
        finding_ids=[finding.id],
        action="soft_delete_missing",
    )

    result = execute_remediation(
        db,
        storage,
        actor_user_id=1,
        preview_token=preview.token,
        idempotency_key="soft-delete-missing-1",
        request_id="req-soft-delete-missing",
    )
    db.commit()
    db.refresh(stored_file)

    assert stored_file.status == "deleted"
    assert stored_file.deleted_at is not None
    transfer = db.scalar(
        select(FileTransfer).where(FileTransfer.transfer_uid == result.transfer_uid)
    )
    assert transfer.operation == "soft_delete_missing"
    assert transfer.status == "succeeded"


def test_destructive_transfer_settles_only_after_metadata_commit(db):
    transfer = FileTransfer(
        transfer_uid="purge-after-commit",
        direction="internal",
        operation="purge_untracked",
        status="in_progress",
        request_id="req-purge-after-commit",
        expected_bytes=12,
    )
    db.add(transfer)
    db.commit()

    assert (
        db.scalar(select(FileTransfer.id).where(FileTransfer.transfer_uid == transfer.transfer_uid))
        == transfer.id
    )
    register_pending_destructive_transfer(
        db,
        transfer.transfer_uid,
        transferred_bytes=12,
    )
    db.commit()
    db.refresh(transfer)

    assert transfer.status == "succeeded"
    assert transfer.transferred_bytes == 12
    assert transfer.error_code is None


def test_destructive_transfer_exposes_compensation_when_metadata_rolls_back(db):
    transfer = FileTransfer(
        transfer_uid="purge-after-rollback",
        direction="internal",
        operation="purge_untracked",
        status="in_progress",
        request_id="req-purge-after-rollback",
        expected_bytes=7,
    )
    db.add(transfer)
    db.commit()

    assert (
        db.scalar(select(FileTransfer.id).where(FileTransfer.transfer_uid == transfer.transfer_uid))
        == transfer.id
    )
    register_pending_destructive_transfer(
        db,
        transfer.transfer_uid,
        transferred_bytes=7,
    )
    db.rollback()
    db.refresh(transfer)

    assert transfer.status == "compensation_required"
    assert transfer.transferred_bytes == 7
    assert transfer.error_code == "PURGE_METADATA_COMMIT_FAILED"
