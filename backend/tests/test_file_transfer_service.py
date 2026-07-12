from __future__ import annotations

from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import AppHTTPException
from app.db.init_db import init_db
from app.main import app
from app.models.file import StoredFile
from app.models.file_transfer import FileTransfer
from app.services.file_transfer_service import (
    TransferSpec,
    begin_transfer,
    complete_transfer_in_transaction,
    mark_transfer_in_progress,
    settle_stream,
    settle_transfer,
)
from app.services.storage_service import save_bytes_as_file
from app.storage.base import StorageError
from app.storage.local_storage import LocalFileStorage

_DWG = b"AC1027" + b"\x00" * 1018


def _factory(db):
    return sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)


def _admin_headers(client: TestClient) -> dict[str, str]:
    init_db()
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert login.status_code == 201, login.text
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def _upload(
    client: TestClient,
    headers: dict[str, str],
    idempotency_key: str,
    *,
    batch_name: str | None = None,
):
    path = "/api/v1/files"
    if batch_name:
        path = f"{path}?batch_name={batch_name}"
    return client.post(
        path,
        headers={**headers, "Idempotency-Key": idempotency_key},
        files={"upload": ("sample.dwg", BytesIO(_DWG), "application/acad")},
    )


def test_begin_transfer_reuses_succeeded_idempotent_operation(db):
    factory = _factory(db)
    spec = TransferSpec(
        direction="inbound",
        operation="upload",
        actor_user_id=None,
        request_id="req-1",
        idempotency_key="same-upload",
    )
    first = begin_transfer(factory, spec)
    settle_transfer(
        factory,
        first.transfer_uid,
        status="succeeded",
        transferred_bytes=10,
    )

    second = begin_transfer(factory, spec)

    assert second.transfer_uid == first.transfer_uid
    assert second.status == "succeeded"
    assert second.transferred_bytes == 10


def test_begin_transfer_rejects_duplicate_in_progress_operation(db):
    factory = _factory(db)
    spec = TransferSpec(
        direction="inbound",
        operation="upload",
        actor_user_id=None,
        request_id="req-2",
        idempotency_key="active-upload",
    )
    first = begin_transfer(factory, spec)
    mark_transfer_in_progress(
        factory,
        first.transfer_uid,
        bucket="dwg-original",
        storage_key="uploads/a.dwg",
        expected_bytes=10,
    )

    with pytest.raises(AppHTTPException) as exc:
        begin_transfer(factory, spec)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "TRANSFER_IN_PROGRESS"


def test_complete_transfer_joins_callers_transaction(db):
    factory = _factory(db)
    transfer = begin_transfer(
        factory,
        TransferSpec(
            direction="internal",
            operation="generated",
            actor_user_id=None,
            request_id="job-4",
            idempotency_key="job-4-result",
        ),
    )

    with factory.begin() as transaction:
        complete_transfer_in_transaction(
            transaction,
            transfer.transfer_uid,
            file_id=None,
            bucket="dwg-derived",
            storage_key="jobs/4/result.json",
            original_name="result.json",
            transferred_bytes=12,
        )

    with factory() as verify:
        model = verify.scalar(
            select(FileTransfer).where(FileTransfer.transfer_uid == transfer.transfer_uid)
        )
        assert model is not None
        assert model.status == "succeeded"
        assert model.bucket == "dwg-derived"
        assert model.transferred_bytes == 12


def test_generated_file_automatically_records_internal_transfer(db, tmp_path, monkeypatch):
    init_db()
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.services.storage_service.get_storage_backend",
        lambda: storage,
    )

    stored = save_bytes_as_file(
        db,
        bucket="dwg-derived",
        storage_key="jobs/42/result.dxf",
        original_name="result.dxf",
        file_ext=".dxf",
        content_type="application/dxf",
        payload=b"generated-dxf",
        uploaded_by=1,
        batch_name="job-42",
    )
    db.commit()

    transfer = db.scalar(
        select(FileTransfer).where(
            FileTransfer.file_id == stored.id,
            FileTransfer.operation == "generated",
        )
    )
    assert transfer is not None
    assert transfer.direction == "internal"
    assert transfer.status == "succeeded"
    assert transfer.transferred_bytes == len(b"generated-dxf")
    assert transfer.bucket == "dwg-derived"
    assert transfer.storage_key == "jobs/42/result.dxf"


def test_excel_final_upload_automatically_records_inbound_transfer(
    db,
    tmp_path,
    monkeypatch,
):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.services.storage_service.get_storage_backend",
        lambda: storage,
    )
    monkeypatch.setattr(
        "app.api.v1.excel_final_api.settings.excel_final_pipeline_enabled",
        True,
    )
    client = TestClient(app)
    headers = _admin_headers(client)

    response = client.post(
        "/api/v1/excel-final/upload",
        headers=headers,
        files={
            "upload": (
                "parts.xlsx",
                BytesIO(b"PK\x03\x04minimal-xlsx-payload"),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert response.status_code == 201, response.text
    file_id = response.json()["data"]["file_id"]
    db.expire_all()
    transfer = db.scalar(
        select(FileTransfer).where(
            FileTransfer.file_id == file_id,
            FileTransfer.direction == "inbound",
        )
    )
    assert transfer is not None
    assert transfer.operation == "upload"
    assert transfer.status == "succeeded"
    assert transfer.transferred_bytes == len(b"PK\x03\x04minimal-xlsx-payload")


def test_settle_transfer_persists_public_failure_without_secret(db):
    factory = _factory(db)
    transfer = begin_transfer(
        factory,
        TransferSpec(
            direction="inbound",
            operation="upload",
            actor_user_id=None,
            request_id="req-failed",
            idempotency_key="failed-upload",
        ),
    )

    settled = settle_transfer(
        factory,
        transfer.transfer_uid,
        status="compensation_required",
        transferred_bytes=7,
        error_code="STORAGE_COMPENSATION_REQUIRED",
        error_message="Uploaded object could not be removed after metadata rollback.",
    )

    assert settled.status == "compensation_required"
    assert settled.error_code == "STORAGE_COMPENSATION_REQUIRED"
    assert "mysql://" not in (settled.error_message or "")


def test_upload_commits_file_transfer_with_metadata(db, tmp_path, monkeypatch):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.services.storage_service.get_storage_backend", lambda: storage
    )
    client = TestClient(app)
    headers = _admin_headers(client)

    response = _upload(client, headers, "upload-ledger-1")

    assert response.status_code == 201, response.text
    file_id = response.json()["data"]["id"]
    db.expire_all()
    transfer = db.scalar(
        select(FileTransfer).where(FileTransfer.idempotency_key == "upload-ledger-1")
    )
    assert transfer is not None
    assert transfer.status == "succeeded"
    assert transfer.file_id == file_id
    assert transfer.transferred_bytes == len(_DWG)
    assert storage.object_exists(transfer.bucket, transfer.storage_key)


def test_upload_retry_with_same_idempotency_key_reuses_file(db, tmp_path, monkeypatch):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.services.storage_service.get_storage_backend", lambda: storage
    )
    client = TestClient(app)
    headers = _admin_headers(client)

    first = _upload(client, headers, "upload-ledger-retry")
    second = _upload(client, headers, "upload-ledger-retry")

    assert first.status_code == second.status_code == 201
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(StoredFile)) == 1
    assert db.scalar(select(func.count()).select_from(FileTransfer)) == 1
    assert storage.bucket_object_counts(["dwg-original"])["dwg-original"] == 1


def _fail_stored_file_flush(session: Session, _flush_context, _instances) -> None:
    if any(isinstance(item, StoredFile) for item in session.new):
        raise RuntimeError("synthetic metadata failure")


def test_upload_metadata_failure_compensates_object(db, tmp_path, monkeypatch):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.services.storage_service.get_storage_backend", lambda: storage
    )
    client = TestClient(app, raise_server_exceptions=False)
    headers = _admin_headers(client)
    event.listen(Session, "before_flush", _fail_stored_file_flush)
    try:
        response = _upload(client, headers, "upload-ledger-db-fail")
    finally:
        event.remove(Session, "before_flush", _fail_stored_file_flush)

    assert response.status_code == 500
    db.expire_all()
    transfer = db.scalar(
        select(FileTransfer).where(
            FileTransfer.idempotency_key == "upload-ledger-db-fail"
        )
    )
    assert transfer is not None
    assert transfer.status == "failed"
    assert storage.bucket_object_counts(["dwg-original"])["dwg-original"] == 0


def test_upload_compensation_failure_is_persisted(db, tmp_path, monkeypatch):
    class FailingDeleteStorage(LocalFileStorage):
        def delete_object(self, bucket: str, storage_key: str) -> None:
            raise StorageError("synthetic delete failure")

    storage = FailingDeleteStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.services.storage_service.get_storage_backend", lambda: storage
    )
    client = TestClient(app, raise_server_exceptions=False)
    headers = _admin_headers(client)
    event.listen(Session, "before_flush", _fail_stored_file_flush)
    try:
        response = _upload(client, headers, "upload-ledger-comp-fail")
    finally:
        event.remove(Session, "before_flush", _fail_stored_file_flush)

    assert response.status_code == 500
    db.expire_all()
    transfer = db.scalar(
        select(FileTransfer).where(
            FileTransfer.idempotency_key == "upload-ledger-comp-fail"
        )
    )
    assert transfer is not None
    assert transfer.status == "compensation_required"
    assert transfer.error_code == "STORAGE_COMPENSATION_REQUIRED"
    assert storage.bucket_object_counts(["dwg-original"])["dwg-original"] == 1


def _outbound_transfer(factory, key: str):
    return begin_transfer(
        factory,
        TransferSpec(
            direction="outbound",
            operation="download",
            actor_user_id=None,
            request_id=f"req-{key}",
            idempotency_key=key,
        ),
    )


def test_stream_settles_succeeded_after_exhaustion(db):
    factory = _factory(db)
    transfer = _outbound_transfer(factory, "download-success")

    payload = b"".join(
        settle_stream(factory, transfer.transfer_uid, iter((b"ab", b"c")))
    )

    assert payload == b"abc"
    with factory() as verify:
        row = verify.scalar(
            select(FileTransfer).where(
                FileTransfer.transfer_uid == transfer.transfer_uid
            )
        )
        assert row is not None
        assert row.status == "succeeded"
        assert row.transferred_bytes == 3


def test_stream_settles_failed_after_read_error(db):
    factory = _factory(db)
    transfer = _outbound_transfer(factory, "download-failed")

    def broken_chunks():
        yield b"ab"
        raise StorageError("backend offline")

    with pytest.raises(StorageError):
        b"".join(settle_stream(factory, transfer.transfer_uid, broken_chunks()))

    with factory() as verify:
        row = verify.scalar(
            select(FileTransfer).where(
                FileTransfer.transfer_uid == transfer.transfer_uid
            )
        )
        assert row is not None
        assert row.status == "failed"
        assert row.transferred_bytes == 2
        assert row.error_code == "STORAGE_READ_FAILED"


def test_stream_settles_cancelled_when_consumer_closes(db):
    factory = _factory(db)
    transfer = _outbound_transfer(factory, "download-cancelled")
    stream = settle_stream(factory, transfer.transfer_uid, iter((b"ab", b"c")))

    assert next(stream) == b"ab"
    stream.close()

    with factory() as verify:
        row = verify.scalar(
            select(FileTransfer).where(
                FileTransfer.transfer_uid == transfer.transfer_uid
            )
        )
        assert row is not None
        assert row.status == "cancelled"
        assert row.transferred_bytes == 2


def test_stream_closes_source_iterator_when_consumer_closes(db):
    factory = _factory(db)
    transfer = _outbound_transfer(factory, "download-closes-source")
    source_closed = False

    def source_chunks():
        nonlocal source_closed
        try:
            yield b"ab"
            yield b"c"
        finally:
            source_closed = True

    stream = settle_stream(factory, transfer.transfer_uid, source_chunks())
    assert next(stream) == b"ab"

    stream.close()

    assert source_closed is True


def test_file_download_settles_outbound_transfer(db, tmp_path, monkeypatch):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.services.storage_service.get_storage_backend", lambda: storage
    )
    monkeypatch.setattr("app.api.v1.files_api.get_storage_backend", lambda: storage)
    client = TestClient(app)
    headers = _admin_headers(client)
    uploaded = _upload(client, headers, "download-source")
    file_id = uploaded.json()["data"]["id"]
    signed = client.get(f"/api/v1/files/{file_id}/download-url", headers=headers)

    response = client.get(signed.json()["data"]["url"], headers=headers)

    assert response.status_code == 200
    assert response.content == _DWG
    db.expire_all()
    transfer = db.scalar(
        select(FileTransfer)
        .where(
            FileTransfer.file_id == file_id,
            FileTransfer.direction == "outbound",
            FileTransfer.operation == "download",
        )
        .order_by(FileTransfer.id.desc())
    )
    assert transfer is not None
    assert transfer.status == "succeeded"
    assert transfer.transferred_bytes == len(_DWG)


def test_zip_download_settles_outbound_transfer(db, tmp_path, monkeypatch):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.services.storage_service.get_storage_backend", lambda: storage
    )
    monkeypatch.setattr("app.api.v1.files_api.get_storage_backend", lambda: storage)
    client = TestClient(app)
    headers = _admin_headers(client)
    uploaded = _upload(client, headers, "zip-source")
    file_id = uploaded.json()["data"]["id"]

    response = client.post(
        "/api/v1/files/download-zip",
        headers=headers,
        json={"file_ids": [file_id], "formats": ["dwg"], "folder_name": "export"},
    )

    assert response.status_code == 200, response.text
    assert response.content.startswith(b"PK")
    db.expire_all()
    transfer = db.scalar(
        select(FileTransfer)
        .where(
            FileTransfer.direction == "outbound",
            FileTransfer.operation == "download_zip",
        )
        .order_by(FileTransfer.id.desc())
    )
    assert transfer is not None
    assert transfer.status == "succeeded"
    assert transfer.transferred_bytes == len(response.content)


def test_batch_zip_download_uses_strict_export_and_transfer_ledger(
    db, tmp_path, monkeypatch
):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.services.storage_service.get_storage_backend", lambda: storage
    )
    monkeypatch.setattr("app.api.v1.files_api.get_storage_backend", lambda: storage)
    client = TestClient(app, raise_server_exceptions=False)
    headers = _admin_headers(client)
    uploaded = _upload(
        client,
        headers,
        "batch-zip-source",
        batch_name="batch-a",
    )
    assert uploaded.status_code == 201

    response = client.get("/api/v1/files/batches/batch-a/download-zip", headers=headers)

    assert response.status_code == 200, response.text
    assert response.content.startswith(b"PK")
    db.expire_all()
    transfer = db.scalar(
        select(FileTransfer)
        .where(
            FileTransfer.direction == "outbound",
            FileTransfer.operation == "download_zip",
            FileTransfer.batch_ref == "batch-a",
        )
        .order_by(FileTransfer.id.desc())
    )
    assert transfer is not None
    assert transfer.status == "succeeded"
    assert transfer.transferred_bytes == len(response.content)
