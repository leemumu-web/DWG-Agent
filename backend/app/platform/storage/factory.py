"""Configured storage adapter selection and health probing."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from app.platform.config.settings import settings
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage.base import (
    AbstractStorageBackend,
    StorageConfigurationError,
    StorageError,
)
from app.platform.storage.local import LocalFileStorage
from app.platform.storage.minio import MinioStorage
from app.platform.storage.paths import ensure_within_root

logger = logging.getLogger(__name__)


def build_storage_path(bucket: str, storage_key: str) -> Path:
    """Resolve a local object path without allowing root escape."""
    root = settings.local_storage_root
    return ensure_within_root(root, root / bucket / storage_key)


@lru_cache(maxsize=8)
def _get_storage_backend_cached(
    backend_name: str,
    local_root: str,
    minio_endpoint: str,
    minio_access_key: str,
    minio_secret_key: str,
) -> AbstractStorageBackend:
    if backend_name == "local":
        return LocalFileStorage(Path(local_root))
    if backend_name == "minio":
        try:
            return MinioStorage(
                endpoint=minio_endpoint,
                access_key=minio_access_key,
                secret_key=minio_secret_key,
            )
        except StorageConfigurationError as exc:
            raise AppHTTPException(
                500,
                "STORAGE_BACKEND_MISCONFIGURED",
                "Configured storage backend is not ready.",
            ) from exc
    raise AppHTTPException(
        500,
        "STORAGE_BACKEND_UNSUPPORTED",
        f"Unsupported storage backend: {backend_name}",
    )


def get_storage_backend() -> AbstractStorageBackend:
    return _get_storage_backend_cached(
        settings.storage_backend,
        str(settings.local_storage_root),
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
    )


def clear_storage_backend_cache() -> None:
    _get_storage_backend_cached.cache_clear()


def storage_health() -> dict[str, str]:
    try:
        get_storage_backend().check_health()
        return {"status": "ok", "message": "Storage is reachable."}
    except (AppHTTPException, StorageError) as exc:
        logger.warning("Storage readiness check failed: %s", exc)
        return {"status": "error", "message": "Storage is unavailable."}


__all__ = [
    "build_storage_path",
    "clear_storage_backend_cache",
    "get_storage_backend",
    "storage_health",
]
