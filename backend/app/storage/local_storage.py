from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from app.storage.base import AbstractStorageBackend, StorageObjectNotFound
from app.utils.path_utils import ensure_within_root


class LocalFileStorage(AbstractStorageBackend):
    def __init__(self, root: Path):
        self.root = root

    def _path(self, bucket: str, storage_key: str) -> Path:
        return ensure_within_root(self.root, self.root / bucket / storage_key)

    def put_fileobj(
        self,
        bucket: str,
        storage_key: str,
        fileobj: BinaryIO,
        *,
        length: int,
        content_type: str | None = None,
    ) -> None:
        path = self._path(bucket, storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fileobj.seek(0)
        with path.open("wb") as out:
            shutil.copyfileobj(fileobj, out, length=1024 * 1024)

    def iter_file(
        self,
        bucket: str,
        storage_key: str,
        *,
        chunk_size: int = 1024 * 1024,
    ) -> Iterator[bytes]:
        path = self._path(bucket, storage_key)
        if not path.is_file():
            raise StorageObjectNotFound(f"{bucket}/{storage_key}")

        def _iter() -> Iterator[bytes]:
            with path.open("rb") as src:
                while chunk := src.read(chunk_size):
                    yield chunk

        return _iter()

    def local_path(self, bucket: str, storage_key: str) -> Path | None:
        return self._path(bucket, storage_key)

    def delete_object(self, bucket: str, storage_key: str) -> None:
        self._path(bucket, storage_key).unlink(missing_ok=True)
