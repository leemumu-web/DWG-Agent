from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO


class StorageError(Exception):
    """Base exception for storage backend failures."""


class StorageConfigurationError(StorageError):
    """Raised when the selected storage backend is not configured enough to run."""


class StorageObjectNotFound(StorageError):
    """Raised when a requested storage object does not exist."""


@dataclass(frozen=True)
class ObjectInfo:
    bucket: str
    storage_key: str
    size_bytes: int
    last_modified: datetime | None


@dataclass(frozen=True)
class ObjectPage:
    items: list[ObjectInfo]
    next_cursor: str | None


class AbstractStorageBackend(ABC):
    @abstractmethod
    def check_health(self) -> None:
        """Raise StorageError when the backend cannot serve requests."""

    @abstractmethod
    def put_fileobj(
        self,
        bucket: str,
        storage_key: str,
        fileobj: BinaryIO,
        *,
        length: int,
        content_type: str | None = None,
    ) -> None:
        """Persist a seekable file object at bucket/storage_key."""

    @abstractmethod
    def iter_file(
        self,
        bucket: str,
        storage_key: str,
        *,
        chunk_size: int = 1024 * 1024,
    ) -> Iterator[bytes]:
        """Return an iterator over object bytes."""

    @abstractmethod
    def local_path(self, bucket: str, storage_key: str) -> Path | None:
        """Return a local path for backends that expose one, otherwise None."""

    @abstractmethod
    def delete_object(self, bucket: str, storage_key: str) -> None:
        """Delete an object if it exists."""

    @abstractmethod
    def stat_object(self, bucket: str, storage_key: str) -> ObjectInfo:
        """Return object metadata or raise StorageObjectNotFound."""

    def object_exists(self, bucket: str, storage_key: str) -> bool:
        """Return False only for a confirmed missing object.

        Connectivity and permission failures remain StorageError so callers never
        misclassify an unavailable backend as missing data.
        """
        try:
            self.stat_object(bucket, storage_key)
            return True
        except StorageObjectNotFound:
            return False

    @abstractmethod
    def list_objects(
        self,
        bucket: str,
        *,
        prefix: str,
        cursor: str | None,
        page_size: int,
    ) -> ObjectPage:
        """Return one stable cursor page of objects ordered by storage key."""

    def bucket_object_counts(self, buckets: list[str]) -> dict[str, int]:
        """Return per-bucket object counts.

        Default implementation returns zero for every bucket.
        Backends that can enumerate objects (MinioStorage, LocalFileStorage)
        override this to provide real numbers.
        """
        return {b: 0 for b in buckets}
