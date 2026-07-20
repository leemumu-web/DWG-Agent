from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.files.interface import (
    FileTransfer,
    StorageScanFinding,
    StorageScanRun,
    StoredFile,
)


def _stored_file(*, bucket: str, storage_key: str) -> StoredFile:
    return StoredFile(
        bucket=bucket,
        storage_key=storage_key,
        original_name="sample.dwg",
        file_ext=".dwg",
        content_type="application/acad",
        size_bytes=1024,
        sha256="a" * 64,
        md5="b" * 32,
        status="available",
    )


def test_file_storage_location_is_unique(db):
    db.add_all(
        [
            _stored_file(bucket="dwg-original", storage_key="uploads/a.dwg"),
            _stored_file(bucket="dwg-original", storage_key="uploads/a.dwg"),
        ]
    )

    with pytest.raises(IntegrityError):
        db.commit()


def test_same_storage_key_can_exist_in_different_buckets(db):
    db.add_all(
        [
            _stored_file(bucket="dwg-original", storage_key="uploads/a.dwg"),
            _stored_file(bucket="dwg-derived", storage_key="uploads/a.dwg"),
        ]
    )

    db.commit()


def test_transfer_defaults_to_prepared(db):
    row = FileTransfer(
        transfer_uid=str(uuid4()),
        direction="inbound",
        operation="upload",
        request_id="req-model",
    )
    db.add(row)

    db.commit()

    assert row.status == "prepared"
    assert row.transferred_bytes == 0


def test_scan_finding_is_unique_per_run_location_and_type(db):
    run = StorageScanRun(backend="local", status="queued")
    db.add(run)
    db.flush()
    finding = {
        "run_id": run.id,
        "finding_type": "missing_object",
        "bucket": "dwg-original",
        "storage_key": "uploads/missing.dwg",
    }
    db.add_all([StorageScanFinding(**finding), StorageScanFinding(**finding)])

    with pytest.raises(IntegrityError):
        db.commit()


def test_scan_run_counters_default_to_zero(db):
    run = StorageScanRun(backend="minio", status="queued")
    db.add(run)

    db.commit()

    assert run.scanned_files == 0
    assert run.scanned_objects == 0
    assert run.consistent_count == 0
    assert run.retained_deleted_count == 0
    assert run.missing_object_count == 0
    assert run.untracked_object_count == 0
    assert run.size_mismatch_count == 0
    assert run.error_count == 0
