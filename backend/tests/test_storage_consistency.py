from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.services.storage_service import (
    clear_storage_backend_cache,
    get_storage_backend,
    save_bytes_as_file,
)
from app.storage.base import StorageError
from app.storage.local_storage import LocalFileStorage
from app.storage.minio_storage import MinioStorage


class TrackingStorage:
    def __init__(self):
        self.written: list[tuple[str, str]] = []
        self.deleted: list[tuple[str, str]] = []

    def put_fileobj(self, bucket, storage_key, fileobj, *, length, content_type=None):
        assert len(fileobj.read()) == length
        self.written.append((bucket, storage_key))

    def delete_object(self, bucket, storage_key):
        self.deleted.append((bucket, storage_key))


def _save(db: Session, storage: TrackingStorage, monkeypatch):
    monkeypatch.setattr("app.services.storage_service.get_storage_backend", lambda: storage)
    return save_bytes_as_file(
        db,
        bucket="dwg-derived",
        storage_key="jobs/1/result.json",
        original_name="result.json",
        file_ext=".json",
        content_type="application/json",
        payload=b"{}",
        uploaded_by=None,
    )


def test_storage_object_is_deleted_when_database_transaction_rolls_back(db: Session, monkeypatch):
    storage = TrackingStorage()

    _save(db, storage, monkeypatch)
    db.rollback()

    assert storage.written == [("dwg-derived", "jobs/1/result.json")]
    assert storage.deleted == [("dwg-derived", "jobs/1/result.json")]


def test_storage_object_is_retained_after_database_commit(db: Session, monkeypatch):
    storage = TrackingStorage()

    _save(db, storage, monkeypatch)
    db.commit()
    db.rollback()

    assert storage.written == [("dwg-derived", "jobs/1/result.json")]
    assert storage.deleted == []


def test_storage_backend_is_cached_for_the_same_configuration(tmp_path: Path, monkeypatch):
    from app.services import storage_service

    clear_storage_backend_cache()
    monkeypatch.setattr(storage_service.settings, "storage_backend", "local")
    monkeypatch.setattr(storage_service.settings, "local_storage_root", tmp_path)

    first = get_storage_backend()
    second = get_storage_backend()

    assert isinstance(first, LocalFileStorage)
    assert first is second
    clear_storage_backend_cache()


def test_minio_network_error_is_normalized():
    class OfflineClient:
        def bucket_exists(self, _bucket):
            raise OSError("connection refused")

    storage = MinioStorage(
        endpoint="http://minio:9000",
        access_key="minio",
        secret_key="secret",
        client=OfflineClient(),
    )

    with pytest.raises(StorageError, match="Failed to ensure MinIO bucket"):
        storage.put_fileobj(
            "dwg-original",
            "uploads/a.dwg",
            BytesIO(b"data"),
            length=4,
        )


def test_readiness_returns_503_when_storage_is_unavailable(monkeypatch):
    from app import main as main_module

    monkeypatch.setattr(
        main_module,
        "storage_health",
        lambda: {"status": "error", "message": "storage unavailable"},
        raising=False,
    )

    response = TestClient(app).get("/health/ready")

    assert response.status_code == 503
    data = response.json()["data"]
    assert data["status"] == "error"
    assert data["storage"] == {
        "status": "error",
        "message": "Storage is unavailable.",
    }
