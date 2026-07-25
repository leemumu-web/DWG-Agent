"""Bounded-memory ZIP streaming for immutable storage objects."""

from __future__ import annotations

import io
import zipfile
from collections import deque
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from app.platform.storage.base import AbstractStorageBackend


@dataclass(frozen=True)
class StorageZipMember:
    bucket: str
    storage_key: str
    archive_path: str


class _UnseekableZipSink(io.RawIOBase):
    """Collect only bytes not yet yielded to the HTTP response."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: deque[bytes] = deque()
        self._position = 0

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def tell(self) -> int:
        return self._position

    def write(self, data: bytes | bytearray | memoryview) -> int:
        payload = bytes(data)
        if payload:
            self._chunks.append(payload)
            self._position += len(payload)
        return len(payload)

    def drain(self) -> Iterator[bytes]:
        while self._chunks:
            yield self._chunks.popleft()


def iter_storage_zip(
    storage: AbstractStorageBackend,
    members: Sequence[StorageZipMember],
) -> Iterator[bytes]:
    """Yield a valid ZIP while keeping at most the current storage chunk in memory."""

    sink = _UnseekableZipSink()
    with zipfile.ZipFile(
        sink,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        allowZip64=True,
    ) as archive:
        for member in members:
            with archive.open(member.archive_path, mode="w", force_zip64=True) as target:
                yield from sink.drain()
                for chunk in storage.iter_file(member.bucket, member.storage_key):
                    target.write(chunk)
                    yield from sink.drain()
            yield from sink.drain()
    yield from sink.drain()


__all__ = ["StorageZipMember", "iter_storage_zip"]
