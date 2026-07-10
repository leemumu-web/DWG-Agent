from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO


class StorageError(Exception):
    """Base exception for storage backend failures."""


class StorageConfigurationError(StorageError):
    """Raised when the selected storage backend is not configured enough to run."""


class StorageObjectNotFound(StorageError):
    """Raised when a requested storage object does not exist."""


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
