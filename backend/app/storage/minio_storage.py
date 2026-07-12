from __future__ import annotations

from collections.abc import Iterator
from itertools import islice
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlparse

from minio import Minio
from minio.error import MinioException, S3Error
from urllib3.exceptions import HTTPError

from app.storage.base import (
    AbstractStorageBackend,
    ObjectInfo,
    ObjectPage,
    StorageConfigurationError,
    StorageError,
    StorageObjectNotFound,
)


def _parse_minio_endpoint(endpoint: str) -> tuple[str, bool]:
    parsed = urlparse(endpoint)
    if parsed.scheme in {"http", "https"}:
        if not parsed.netloc:
            raise StorageConfigurationError("MINIO_ENDPOINT is missing host.")
        return parsed.netloc, parsed.scheme == "https"
    if "://" in endpoint:
        raise StorageConfigurationError("MINIO_ENDPOINT must use http or https.")
    return endpoint, False


class MinioStorage(AbstractStorageBackend):
    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        client: Minio | None = None,
    ):
        if client is None and (not access_key or not secret_key):
            raise StorageConfigurationError(
                "MINIO_ACCESS_KEY and MINIO_SECRET_KEY are required when STORAGE_BACKEND=minio."
            )
        host, secure = _parse_minio_endpoint(endpoint)
        self._client = client or Minio(
            host,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )

    def check_health(self) -> None:
        try:
            self._client.list_buckets()
        except (MinioException, HTTPError, OSError) as exc:
            raise StorageError("MinIO is unavailable.") from exc

    def _ensure_bucket(self, bucket: str) -> None:
        try:
            if not self._client.bucket_exists(bucket):
                self._client.make_bucket(bucket)
        except S3Error as exc:
            if exc.code in {"BucketAlreadyExists", "BucketAlreadyOwnedByYou"}:
                try:
                    if self._client.bucket_exists(bucket):
                        return
                except (MinioException, HTTPError, OSError):
                    pass
            raise StorageError(f"Failed to ensure MinIO bucket {bucket}: {exc}") from exc
        except (MinioException, HTTPError, OSError) as exc:
            raise StorageError(f"Failed to ensure MinIO bucket {bucket}: {exc}") from exc

    def put_fileobj(
        self,
        bucket: str,
        storage_key: str,
        fileobj: BinaryIO,
        *,
        length: int,
        content_type: str | None = None,
    ) -> None:
        self._ensure_bucket(bucket)
        fileobj.seek(0)
        try:
            self._client.put_object(
                bucket,
                storage_key,
                fileobj,
                length=length,
                content_type=content_type,
            )
        except (MinioException, HTTPError, OSError) as exc:
            raise StorageError(
                f"Failed to write MinIO object {bucket}/{storage_key}: {exc}"
            ) from exc

    def iter_file(
        self,
        bucket: str,
        storage_key: str,
        *,
        chunk_size: int = 1024 * 1024,
    ) -> Iterator[bytes]:
        try:
            response = self._client.get_object(bucket, storage_key)
        except S3Error as exc:
            if exc.code in {"NoSuchBucket", "NoSuchKey", "NoSuchObject"}:
                raise StorageObjectNotFound(f"{bucket}/{storage_key}") from exc
            raise StorageError(
                f"Failed to read MinIO object {bucket}/{storage_key}: {exc}"
            ) from exc
        except (MinioException, HTTPError, OSError) as exc:
            raise StorageError(
                f"Failed to read MinIO object {bucket}/{storage_key}: {exc}"
            ) from exc

        def _iter() -> Iterator[bytes]:
            try:
                yield from response.stream(chunk_size)
            finally:
                response.close()
                response.release_conn()

        return _iter()

    def bucket_object_counts(self, buckets: list[str]) -> dict[str, int]:
        """Return object counts for configured buckets without exposing object keys."""
        counts: dict[str, int] = {}
        for bucket in buckets:
            try:
                if not self._client.bucket_exists(bucket):
                    counts[bucket] = 0
                    continue
                counts[bucket] = sum(1 for _ in self._client.list_objects(bucket, recursive=True))
            except (MinioException, HTTPError, OSError) as exc:
                raise StorageError(f"Failed to inspect MinIO bucket {bucket}.") from exc
        return counts

    def local_path(self, bucket: str, storage_key: str) -> Path | None:
        return None

    def delete_object(self, bucket: str, storage_key: str) -> None:
        try:
            self._client.remove_object(bucket, storage_key)
        except S3Error as exc:
            if exc.code not in {"NoSuchBucket", "NoSuchKey", "NoSuchObject"}:
                raise StorageError(
                    f"Failed to delete MinIO object {bucket}/{storage_key}: {exc}"
                ) from exc
        except (MinioException, HTTPError, OSError) as exc:
            raise StorageError(
                f"Failed to delete MinIO object {bucket}/{storage_key}: {exc}"
            ) from exc

    def stat_object(self, bucket: str, storage_key: str) -> ObjectInfo:
        try:
            item = self._client.stat_object(bucket, storage_key)
        except S3Error as exc:
            if exc.code in {"NoSuchBucket", "NoSuchKey", "NoSuchObject", "NotFound"}:
                raise StorageObjectNotFound(f"{bucket}/{storage_key}") from exc
            raise StorageError(f"Failed to inspect MinIO object {bucket}/{storage_key}.") from exc
        except (MinioException, HTTPError, OSError) as exc:
            raise StorageError(f"Failed to inspect MinIO object {bucket}/{storage_key}.") from exc
        return ObjectInfo(
            bucket=bucket,
            storage_key=storage_key,
            size_bytes=int(item.size),
            last_modified=item.last_modified,
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
        try:
            listed = self._client.list_objects(
                bucket,
                prefix=prefix or None,
                recursive=True,
                start_after=cursor,
            )
            selected = list(islice(listed, page_size + 1))
        except S3Error as exc:
            if exc.code == "NoSuchBucket":
                return ObjectPage(items=[], next_cursor=None)
            raise StorageError(f"Failed to list MinIO bucket {bucket}.") from exc
        except (MinioException, HTTPError, OSError) as exc:
            raise StorageError(f"Failed to list MinIO bucket {bucket}.") from exc

        has_more = len(selected) > page_size
        selected = selected[:page_size]
        items = [
            ObjectInfo(
                bucket=bucket,
                storage_key=item.object_name,
                size_bytes=int(item.size),
                last_modified=item.last_modified,
            )
            for item in selected
        ]
        next_cursor = items[-1].storage_key if has_more and items else None
        return ObjectPage(items=items, next_cursor=next_cursor)
