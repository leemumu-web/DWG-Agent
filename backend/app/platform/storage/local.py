from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO

from app.platform.storage.base import (
    AbstractStorageBackend,
    ObjectInfo,
    ObjectPage,
    StorageCapacity,
    StorageError,
    StorageObjectNotFound,
)
from app.platform.storage.paths import ensure_within_root
from app.platform.time import BUSINESS_TIMEZONE


def _fsync_parent_directory(path: Path) -> None:
    """Persist a directory entry where the operating system supports it."""
    if os.name == "nt":
        return
    parent_fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


class LocalFileStorage(AbstractStorageBackend):
    def __init__(
        self,
        root: Path,
        *,
        warning_percent: int = 80,
        critical_percent: int = 90,
    ):
        self.root = root
        self.warning_percent = warning_percent
        self.critical_percent = critical_percent

    def _path(self, bucket: str, storage_key: str) -> Path:
        return ensure_within_root(self.root, self.root / bucket / storage_key)

    def check_health(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(prefix=".dwg-health-", dir=self.root):
                pass
        except OSError as exc:
            raise StorageError("Local storage is not writable.") from exc

    def capacity(self) -> StorageCapacity:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(self.root)
            return StorageCapacity.from_values(
                total_bytes=usage.total,
                # ``free`` is the space available to this non-root process.
                # Filesystem-reserved blocks make shutil's used + free smaller
                # than total, so total - free is the conservative operator view.
                used_bytes=usage.total - usage.free,
                free_bytes=usage.free,
                warning_percent=self.warning_percent,
                critical_percent=self.critical_percent,
            )
        except (OSError, ValueError):
            return StorageCapacity.unknown("local_capacity_unavailable")

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

        # Write to a sibling temp file, then atomically rename into place.
        # This prevents readers from seeing a truncated object after a crash,
        # and matches MinIO's server-side atomic PUT semantics.
        tmp = NamedTemporaryFile(
            dir=path.parent,
            prefix=".dwg-tmp-",
            delete=False,
        )
        try:
            shutil.copyfileobj(fileobj, tmp, length=1024 * 1024)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            os.replace(tmp.name, path)
            # Make the directory entry durable after rename.
            _fsync_parent_directory(path.parent)
        except BaseException:
            tmp.close()
            Path(tmp.name).unlink(missing_ok=True)
            raise

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

    def bucket_object_counts(self, buckets: list[str]) -> dict[str, int]:
        """Count files per bucket (local adapter).

        Mirrors ``MinioStorage.bucket_object_counts`` so the infrastructure
        overview reports real numbers for both backends.
        """
        counts: dict[str, int] = {}
        for bucket in buckets:
            bucket_dir = self.root / bucket
            if not bucket_dir.is_dir():
                counts[bucket] = 0
                continue
            counts[bucket] = sum(1 for _ in bucket_dir.rglob("*") if _.is_file())
        return counts

    def delete_object(self, bucket: str, storage_key: str) -> None:
        try:
            self._path(bucket, storage_key).unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(
                f"Failed to delete local object {bucket}/{storage_key}."
            ) from exc

    def stat_object(self, bucket: str, storage_key: str) -> ObjectInfo:
        path = self._path(bucket, storage_key)
        if not path.is_file():
            raise StorageObjectNotFound(f"{bucket}/{storage_key}")
        try:
            stat = path.stat()
        except FileNotFoundError as exc:
            raise StorageObjectNotFound(f"{bucket}/{storage_key}") from exc
        except OSError as exc:
            raise StorageError(f"Failed to inspect local object {bucket}/{storage_key}.") from exc
        return ObjectInfo(
            bucket=bucket,
            storage_key=storage_key,
            size_bytes=stat.st_size,
            last_modified=datetime.fromtimestamp(stat.st_mtime, BUSINESS_TIMEZONE),
        )

    def list_objects(
        self,
        bucket: str,
        *,
        prefix: str,
        cursor: str | None,
        page_size: int,
    ) -> ObjectPage:
        if not 1 <= page_size <= 200:
            raise ValueError("page_size must be between 1 and 200")
        bucket_dir = self._path(bucket, "")
        self._path(bucket, prefix)
        if cursor is not None:
            self._path(bucket, cursor)
        if not bucket_dir.is_dir():
            return ObjectPage(items=[], next_cursor=None)

        candidates: list[tuple[str, Path]] = []
        try:
            for path in bucket_dir.rglob("*"):
                if not path.is_file():
                    continue
                key = path.relative_to(bucket_dir).as_posix()
                if key.startswith(prefix) and (cursor is None or key > cursor):
                    candidates.append((key, path))
        except OSError as exc:
            raise StorageError(f"Failed to list local bucket {bucket}.") from exc

        candidates.sort(key=lambda item: item[0])
        selected = candidates[: page_size + 1]
        has_more = len(selected) > page_size
        selected = selected[:page_size]
        items: list[ObjectInfo] = []
        for key, path in selected:
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise StorageError(f"Failed to inspect local object {bucket}/{key}.") from exc
            items.append(
                ObjectInfo(
                    bucket=bucket,
                    storage_key=key,
                    size_bytes=stat.st_size,
                    last_modified=datetime.fromtimestamp(stat.st_mtime, BUSINESS_TIMEZONE),
                )
            )
        next_cursor = items[-1].storage_key if has_more and items else None
        return ObjectPage(items=items, next_cursor=next_cursor)
