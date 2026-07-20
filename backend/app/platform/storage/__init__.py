"""Object-byte adapter interface and concrete Local/MinIO implementations."""

from app.platform.storage.base import (
    AbstractStorageBackend,
    ObjectInfo,
    ObjectPage,
    StorageConfigurationError,
    StorageError,
    StorageObjectNotFound,
)
from app.platform.storage.local import LocalFileStorage
from app.platform.storage.minio import MinioStorage

__all__ = [
    "AbstractStorageBackend",
    "LocalFileStorage",
    "MinioStorage",
    "ObjectInfo",
    "ObjectPage",
    "StorageConfigurationError",
    "StorageError",
    "StorageObjectNotFound",
]
