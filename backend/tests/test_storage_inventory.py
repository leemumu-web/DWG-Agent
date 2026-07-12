from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO

import pytest
from minio.error import S3Error

from app.storage.base import (
    AbstractStorageBackend,
    ObjectInfo,
    StorageError,
    StorageObjectNotFound,
)
from app.storage.local_storage import LocalFileStorage
from app.storage.minio_storage import MinioStorage


def _put(storage: AbstractStorageBackend, bucket: str, key: str, payload: bytes) -> None:
    storage.put_fileobj(
        bucket,
        key,
        BytesIO(payload),
        length=len(payload),
        content_type="application/octet-stream",
    )


def test_local_inventory_is_stably_cursor_paged(tmp_path: Path):
    storage = LocalFileStorage(tmp_path / "storage")
    _put(storage, "dwg-original", "uploads/b.dwg", b"bb")
    _put(storage, "dwg-original", "uploads/a.dwg", b"a")
    _put(storage, "dwg-original", "other/c.dwg", b"ccc")

    first = storage.list_objects(
        "dwg-original", prefix="uploads/", cursor=None, page_size=1
    )
    second = storage.list_objects(
        "dwg-original", prefix="uploads/", cursor=first.next_cursor, page_size=1
    )

    assert [item.storage_key for item in first.items] == ["uploads/a.dwg"]
    assert first.next_cursor == "uploads/a.dwg"
    assert [item.storage_key for item in second.items] == ["uploads/b.dwg"]
    assert second.next_cursor is None


def test_local_stat_and_exists_distinguish_missing_objects(tmp_path: Path):
    storage = LocalFileStorage(tmp_path / "storage")
    _put(storage, "dwg-original", "uploads/a.dwg", b"abc")

    info = storage.stat_object("dwg-original", "uploads/a.dwg")

    assert info.bucket == "dwg-original"
    assert info.storage_key == "uploads/a.dwg"
    assert info.size_bytes == 3
    assert info.last_modified is not None
    assert storage.object_exists("dwg-original", "uploads/a.dwg") is True
    assert storage.object_exists("dwg-original", "uploads/missing.dwg") is False
    with pytest.raises(StorageObjectNotFound):
        storage.stat_object("dwg-original", "uploads/missing.dwg")


class _FailingStorage(AbstractStorageBackend):
    def check_health(self) -> None:
        return None

    def put_fileobj(
        self,
        bucket: str,
        storage_key: str,
        fileobj: BinaryIO,
        *,
        length: int,
        content_type: str | None = None,
    ) -> None:
        return None

    def iter_file(
        self,
        bucket: str,
        storage_key: str,
        *,
        chunk_size: int = 1024 * 1024,
    ) -> Iterator[bytes]:
        return iter(())

    def local_path(self, bucket: str, storage_key: str) -> Path | None:
        return None

    def delete_object(self, bucket: str, storage_key: str) -> None:
        return None

    def stat_object(self, bucket: str, storage_key: str) -> ObjectInfo:
        raise StorageError("offline")

    def list_objects(self, bucket: str, *, prefix: str, cursor: str | None, page_size: int):
        raise StorageError("offline")


def test_object_exists_does_not_hide_backend_errors():
    with pytest.raises(StorageError, match="offline"):
        _FailingStorage().object_exists("dwg-original", "uploads/a.dwg")


class _FakeMinioClient:
    def __init__(self):
        modified = datetime(2026, 7, 12, tzinfo=UTC)
        self.objects = [
            SimpleNamespace(object_name="uploads/a.dwg", size=1, last_modified=modified),
            SimpleNamespace(object_name="uploads/b.dwg", size=2, last_modified=modified),
        ]
        self.calls: list[dict] = []

    def stat_object(self, bucket: str, key: str):
        for item in self.objects:
            if item.object_name == key:
                return item
        raise S3Error(None, "NoSuchKey", "missing", key, "request", "host")

    def list_objects(self, bucket: str, **kwargs):
        self.calls.append({"bucket": bucket, **kwargs})
        start_after = kwargs.get("start_after")
        return (item for item in self.objects if not start_after or item.object_name > start_after)


def test_minio_inventory_uses_cursor_as_start_after():
    client = _FakeMinioClient()
    storage = MinioStorage(
        endpoint="http://minio:9000",
        access_key="minio",
        secret_key="secret",
        client=client,
    )

    first = storage.list_objects(
        "dwg-original", prefix="uploads/", cursor=None, page_size=1
    )
    second = storage.list_objects(
        "dwg-original", prefix="uploads/", cursor=first.next_cursor, page_size=1
    )

    assert [item.storage_key for item in first.items] == ["uploads/a.dwg"]
    assert first.next_cursor == "uploads/a.dwg"
    assert [item.storage_key for item in second.items] == ["uploads/b.dwg"]
    assert second.next_cursor is None
    assert client.calls[1]["start_after"] == "uploads/a.dwg"


def test_minio_stat_normalizes_missing_key():
    storage = MinioStorage(
        endpoint="http://minio:9000",
        access_key="minio",
        secret_key="secret",
        client=_FakeMinioClient(),
    )

    assert storage.object_exists("dwg-original", "uploads/missing.dwg") is False
