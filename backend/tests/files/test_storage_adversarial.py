"""Adversarial tests for the DWG-Agent data & storage layer.

Covers path-traversal guards, byte-level round-trips, rollback compensation,
soft-delete semantics, the StoredFile status lifecycle, and upload input
validation (empty / oversized). No new dependencies — pytest only.
"""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.modules.files.interface import StoredFile, save_bytes_as_file, save_upload_file
from app.platform.config.settings import settings
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage.local import LocalFileStorage
from app.platform.storage.paths import ensure_within_root

# ── test doubles ──────────────────────────────────────────────────────────────


class FakeUpload:
    """Minimal duck-typed stand-in for starlette's UploadFile.

    ``save_upload_file`` only relies on ``.filename``, ``.content_type`` and
    ``await .read(n)`` — so we avoid coupling the test to a starlette version.
    """

    def __init__(self, filename: str, data: bytes, content_type: str | None = None):
        self.filename = filename
        self.content_type = content_type
        self._buf = BytesIO(data)

    async def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)


def _use_local_backend(monkeypatch, root: Path) -> LocalFileStorage:
    """Point the service layer's storage backend at a real temp-dir adapter."""
    storage = LocalFileStorage(root)
    monkeypatch.setattr(
        "app.platform.storage.factory.get_storage_backend", lambda: storage
    )
    return storage


def _make_stored(db: Session, **overrides) -> StoredFile:
    defaults = dict(
        bucket="dwg-original",
        storage_key="uploads/deadbeef.dwg",
        original_name="drawing.dwg",
        file_ext=".dwg",
        content_type="application/octet-stream",
        size_bytes=1234,
        sha256="0" * 64,
        md5="0" * 32,
        status="available",
        uploaded_by=None,
    )
    defaults.update(overrides)
    stored = StoredFile(**defaults)
    db.add(stored)
    db.flush()
    return stored


# ── 1. ensure_within_root rejects sibling-directory bypass ────────────────────


def test_ensure_within_root_rejects_sibling_directory_prefix(tmp_path: Path):
    """`/app/var/storage-evil/x` shares a string prefix with the root but must
    NOT be accepted — this is exactly the case str.startswith would let slip."""
    root = tmp_path / "storage"
    root.mkdir()
    evil = tmp_path / "storage-evil" / "x"

    with pytest.raises(AppHTTPException) as exc:
        ensure_within_root(root, evil)

    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "INVALID_STORAGE_PATH"


def test_ensure_within_root_accepts_legitimate_child(tmp_path: Path):
    """Positive control: a genuine descendant resolves and is returned."""
    root = tmp_path / "storage"
    root.mkdir()
    candidate = root / "dwg-original" / "uploads" / "a.dwg"

    resolved = ensure_within_root(root, candidate)

    assert resolved == candidate.resolve()
    assert resolved.is_relative_to(root.resolve())


def test_windows_parent_directory_sync_is_a_supported_noop(tmp_path: Path, monkeypatch):
    from app.platform.storage import local

    monkeypatch.setattr(local.os, "name", "nt")
    monkeypatch.setattr(local.os, "open", lambda *_args, **_kwargs: pytest.fail("directory open is unsupported on Windows"))
    local._fsync_parent_directory(tmp_path)


# ── 8. `../` inside storage_key is rejected ───────────────────────────────────


def test_ensure_within_root_rejects_dotdot_traversal(tmp_path: Path):
    root = tmp_path / "storage"
    root.mkdir()
    traversal = root / "dwg-original" / ".." / ".." / ".." / "etc" / "passwd"

    with pytest.raises(AppHTTPException) as exc:
        ensure_within_root(root, traversal)

    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "INVALID_STORAGE_PATH"


def test_local_storage_rejects_dotdot_storage_key(tmp_path: Path):
    """The traversal guard must fire through the real adapter code path too,
    before any bytes touch the filesystem."""
    storage = LocalFileStorage(tmp_path / "storage")
    payload = BytesIO(b"AC1032 pwn")

    with pytest.raises(AppHTTPException) as exc:
        storage.put_fileobj(
            "dwg-original",
            "../../../../tmp/escape.dwg",
            payload,
            length=payload.getbuffer().nbytes,
        )

    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "INVALID_STORAGE_PATH"
    # And nothing leaked outside the root.
    assert not (tmp_path / "tmp" / "escape.dwg").exists()


# ── 2. put_fileobj write → read-back → byte-for-byte compare ──────────────────


def test_local_put_fileobj_round_trip_is_byte_exact(tmp_path: Path):
    storage = LocalFileStorage(tmp_path / "storage")
    # Non-trivial payload with NUL bytes to catch text-mode / truncation bugs.
    payload = b"AC1032" + bytes(range(256)) * 32 + b"\x00\xff\x00tail"

    storage.put_fileobj(
        "dwg-original",
        "uploads/round-trip.dwg",
        BytesIO(payload),
        length=len(payload),
    )

    # File really exists at the resolved local path.
    local = storage.local_path("dwg-original", "uploads/round-trip.dwg")
    assert local is not None and local.is_file()
    assert local.stat().st_size == len(payload)

    # And read-back via the streaming iterator matches exactly.
    read_back = b"".join(storage.iter_file("dwg-original", "uploads/round-trip.dwg"))
    assert read_back == payload
    # No stray temp files linger after the atomic rename.
    assert not list(local.parent.glob(".dwg-tmp-*"))


# ── 3. rollback deletes pending storage objects (real temp-dir backend) ───────


def test_rollback_compensation_removes_written_object(
    db: Session, tmp_path: Path, monkeypatch
):
    _use_local_backend(monkeypatch, tmp_path / "storage")

    stored = save_bytes_as_file(
        db,
        bucket="dwg-derived",
        storage_key="jobs/7/result.json",
        original_name="result.json",
        file_ext=".json",
        content_type="application/json",
        payload=b'{"ok": true}',
        uploaded_by=None,
    )
    on_disk = tmp_path / "storage" / "dwg-derived" / "jobs" / "7" / "result.json"
    assert on_disk.is_file(), "object must be written before the DB commits"
    assert stored.id is not None

    db.rollback()

    assert not on_disk.exists(), "after_rollback must compensate the orphaned object"


def test_commit_retains_written_object(db: Session, tmp_path: Path, monkeypatch):
    """Symmetry check: a committed transaction must NOT delete the object."""
    _use_local_backend(monkeypatch, tmp_path / "storage")

    save_bytes_as_file(
        db,
        bucket="dwg-derived",
        storage_key="jobs/8/keep.json",
        original_name="keep.json",
        file_ext=".json",
        content_type="application/json",
        payload=b"{}",
        uploaded_by=None,
    )
    on_disk = tmp_path / "storage" / "dwg-derived" / "jobs" / "8" / "keep.json"

    db.commit()

    assert on_disk.is_file(), "committed object must survive"


# ── 4. soft delete does NOT remove the storage object ─────────────────────────


def test_soft_delete_keeps_storage_object(db: Session, tmp_path: Path, monkeypatch):
    storage = _use_local_backend(monkeypatch, tmp_path / "storage")
    storage.put_fileobj(
        "dwg-original",
        "uploads/keepme.dwg",
        BytesIO(b"AC1032 real bytes"),
        length=17,
    )
    on_disk = tmp_path / "storage" / "dwg-original" / "uploads" / "keepme.dwg"
    stored = _make_stored(db, storage_key="uploads/keepme.dwg")
    db.commit()

    # Soft delete: status flips, storage object is intentionally left behind.
    stored.status = "deleted"
    db.commit()

    assert stored.status == "deleted"
    assert on_disk.is_file(), "soft delete must not touch the storage object"


# ── 5. StoredFile status lifecycle: deleted files are not downloadable ────────


def test_deleted_file_is_not_downloadable(db: Session):
    """The download handler must reject a soft-deleted row with 404 before it
    ever reaches access checks or the storage backend."""
    from app.modules.files.routes import downloads as files_api

    stored = _make_stored(db, status="available")
    db.commit()

    # available → deleted
    stored.status = "deleted"
    db.commit()

    # current_user / request are untouched before the status guard fires.
    with pytest.raises(AppHTTPException) as exc:
        files_api.download_file(
            file_id=stored.id,
            request=None,
            current_user=None,
            expires=9999999999,
            signature="whatever",
            db=db,
        )

    assert exc.value.status_code == 404


def test_status_defaults_to_available(db: Session):
    stored = _make_stored(db)
    db.commit()
    refreshed = db.get(StoredFile, stored.id)
    assert refreshed.status == "available"


# ── 6. empty upload is rejected ───────────────────────────────────────────────


def test_empty_upload_is_rejected(db: Session, tmp_path: Path, monkeypatch):
    _use_local_backend(monkeypatch, tmp_path / "storage")
    upload = FakeUpload("empty.dxf", b"", content_type="application/octet-stream")

    with pytest.raises(AppHTTPException) as exc:
        asyncio.run(save_upload_file(db, upload, uploaded_by=None))

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "EMPTY_FILE"
    # Nothing persisted.
    assert db.query(StoredFile).count() == 0


# ── 7. upload exceeding max_upload_size_mb is rejected ────────────────────────


def test_oversized_upload_is_rejected(db: Session, tmp_path: Path, monkeypatch):
    _use_local_backend(monkeypatch, tmp_path / "storage")
    monkeypatch.setattr(settings, "max_upload_size_mb", 1)
    # 1 MiB + 10 bytes — trips the > max check on the second read chunk.
    oversized = b"D" * (1024 * 1024 + 10)
    upload = FakeUpload("big.dxf", oversized, content_type="application/octet-stream")

    with pytest.raises(AppHTTPException) as exc:
        asyncio.run(save_upload_file(db, upload, uploaded_by=None))

    assert exc.value.status_code == 413
    assert exc.value.detail["code"] == "FILE_TOO_LARGE"
    assert db.query(StoredFile).count() == 0
