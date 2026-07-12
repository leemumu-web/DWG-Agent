from __future__ import annotations

from io import BytesIO, StringIO

import ezdxf
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AppHTTPException
from app.models.file import StoredFile
from app.models.file_transfer import FileTransfer
from app.services import dxf_preview_service as service
from app.storage.base import StorageError
from app.storage.local_storage import LocalFileStorage


def _dxf_bytes(*, block_lines: int = 0) -> bytes:
    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()
    msp.add_line((0, 0), (120, 40), dxfattribs={"layer": "0"})
    msp.add_circle((40, 20), 12, dxfattribs={"layer": "0"})
    if block_lines:
        block = doc.blocks.new(name="PREVIEW_TEST_BLOCK")
        for index in range(block_lines):
            block.add_line((index, 0), (index, 10))
        msp.add_blockref("PREVIEW_TEST_BLOCK", (10, 10))
    stream = StringIO()
    doc.write(stream)
    return stream.getvalue().encode(doc.output_encoding, errors="replace")


def test_render_dxf_returns_safe_svg_and_metadata() -> None:
    rendered = service.render_dxf_to_svg(_dxf_bytes(block_lines=3))

    lower = rendered.payload.lower()
    assert rendered.payload.startswith(b"<?xml")
    assert b"<svg" in lower
    assert rendered.content_type == "image/svg+xml"
    assert rendered.document_entities >= rendered.modelspace_entities >= 3
    assert rendered.entity_counts["LINE"] >= 1
    assert "0" in rendered.layers
    assert rendered.bounds.max_x > rendered.bounds.min_x
    assert rendered.bounds.max_y > rendered.bounds.min_y
    assert not any(token in lower for token in service.FORBIDDEN_SVG_TOKENS)


def test_document_entity_limit_counts_entities_outside_modelspace(monkeypatch) -> None:
    baseline = service.inspect_dxf(_dxf_bytes(block_lines=12))
    assert baseline.document_entities > baseline.modelspace_entities
    monkeypatch.setattr(
        service,
        "MAX_DXF_ENTITIES",
        baseline.modelspace_entities,
    )

    with pytest.raises(AppHTTPException) as exc:
        service.render_dxf_to_svg(_dxf_bytes(block_lines=12))

    assert exc.value.status_code == 413
    assert exc.value.detail["code"] == "DXF_TOO_COMPLEX"


def test_output_size_limit_is_enforced(monkeypatch) -> None:
    monkeypatch.setattr(service, "MAX_PREVIEW_BYTES", 32)

    with pytest.raises(AppHTTPException) as exc:
        service.render_dxf_to_svg(_dxf_bytes())

    assert exc.value.status_code == 413
    assert exc.value.detail["code"] == "DXF_PREVIEW_TOO_LARGE"


def test_invalid_dxf_has_public_error() -> None:
    with pytest.raises(AppHTTPException) as exc:
        service.render_dxf_to_svg(b"not-a-dxf")

    assert exc.value.status_code == 415
    assert exc.value.detail["code"] == "DXF_PARSE_ERROR"
    assert "Traceback" not in exc.value.detail["message"]


def test_declared_source_size_limit_is_checked() -> None:
    with pytest.raises(AppHTTPException) as exc:
        service.validate_dxf_source_size(service.MAX_DXF_SIZE_BYTES + 1)

    assert exc.value.status_code == 413
    assert exc.value.detail["code"] == "DXF_TOO_LARGE"


def _source_file(db: Session, storage: LocalFileStorage, payload: bytes) -> StoredFile:
    storage.put_fileobj(
        "dxf-original",
        "uploads/preview-source.dxf",
        BytesIO(payload),
        length=len(payload),
        content_type="application/dxf",
    )
    source = StoredFile(
        bucket="dxf-original",
        storage_key="uploads/preview-source.dxf",
        original_name="结构详图.dxf",
        file_ext=".dxf",
        content_type="application/dxf",
        size_bytes=len(payload),
        sha256="a" * 64,
        uploaded_by=None,
        status="available",
    )
    db.add(source)
    db.commit()
    return source


def test_preview_generation_registers_file_and_generated_transfer(
    db: Session,
    tmp_path,
    monkeypatch,
) -> None:
    payload = _dxf_bytes(block_lines=3)
    storage = LocalFileStorage(tmp_path / "storage")
    source = _source_file(db, storage, payload)
    monkeypatch.setattr("app.services.storage_service.get_storage_backend", lambda: storage)

    result = service.get_or_create_dxf_preview(
        db,
        source,
        payload,
        storage=storage,
        request_id="preview-generate-1",
    )
    db.commit()

    assert result.cached is False
    assert result.preview_file.file_ext == ".svg"
    assert result.preview_file.batch_name == service.preview_batch_name(source)
    transfer = db.scalar(select(FileTransfer).where(FileTransfer.file_id == result.preview_file.id))
    assert transfer is not None
    assert (transfer.direction, transfer.operation, transfer.status) == (
        "internal",
        "preview_generate",
        "succeeded",
    )
    assert transfer.transferred_bytes == result.preview_file.size_bytes


def test_minio_style_cache_hit_uses_stat_not_local_path(
    db: Session,
    tmp_path,
    monkeypatch,
) -> None:
    class NoLocalPathStorage(LocalFileStorage):
        def local_path(self, bucket: str, storage_key: str):
            return None

    payload = _dxf_bytes()
    storage = NoLocalPathStorage(tmp_path / "storage")
    source = _source_file(db, storage, payload)
    monkeypatch.setattr("app.services.storage_service.get_storage_backend", lambda: storage)
    first = service.get_or_create_dxf_preview(
        db,
        source,
        payload,
        storage=storage,
        request_id="preview-cache-1",
    )
    db.commit()

    def _must_not_render(_inspected):
        raise AssertionError("cache hit must not render the SVG again")

    monkeypatch.setattr(service, "render_inspected_dxf_to_svg", _must_not_render)
    second = service.get_or_create_dxf_preview(
        db,
        source,
        payload,
        storage=storage,
        request_id="preview-cache-2",
    )

    assert second.cached is True
    assert second.preview_file.id == first.preview_file.id


def test_missing_cached_object_is_replaced_and_recorded(
    db: Session,
    tmp_path,
    monkeypatch,
) -> None:
    payload = _dxf_bytes()
    storage = LocalFileStorage(tmp_path / "storage")
    source = _source_file(db, storage, payload)
    monkeypatch.setattr("app.services.storage_service.get_storage_backend", lambda: storage)
    first = service.get_or_create_dxf_preview(
        db,
        source,
        payload,
        storage=storage,
        request_id="preview-replace-1",
    )
    db.commit()
    storage.delete_object(first.preview_file.bucket, first.preview_file.storage_key)

    second = service.get_or_create_dxf_preview(
        db,
        source,
        payload,
        storage=storage,
        request_id="preview-replace-2",
    )
    db.commit()
    db.refresh(first.preview_file)

    assert first.preview_file.status == "deleted"
    assert second.preview_file.id != first.preview_file.id
    invalidation = db.scalar(
        select(FileTransfer).where(
            FileTransfer.file_id == first.preview_file.id,
            FileTransfer.operation == "preview_invalidate",
        )
    )
    assert invalidation is not None
    assert invalidation.status == "succeeded"


def test_preview_prepares_durable_transfer_before_render_and_source_lock(
    db: Session,
    tmp_path,
    monkeypatch,
) -> None:
    payload = _dxf_bytes()
    storage = LocalFileStorage(tmp_path / "storage")
    source = _source_file(db, storage, payload)
    monkeypatch.setattr("app.services.storage_service.get_storage_backend", lambda: storage)
    events: list[str] = []
    real_prepare = service.prepare_transfer_in_transaction
    real_render = service.render_inspected_dxf_to_svg
    real_save = service.save_bytes_as_file

    def prepare_spy(*args, **kwargs):
        events.append("prepare")
        return real_prepare(*args, **kwargs)

    def render_spy(*args, **kwargs):
        events.append("render")
        return real_render(*args, **kwargs)

    def save_spy(*args, **kwargs):
        events.append(f"save:{bool(kwargs.get('transfer_uid'))}")
        return real_save(*args, **kwargs)

    monkeypatch.setattr(service, "prepare_transfer_in_transaction", prepare_spy)
    monkeypatch.setattr(service, "render_inspected_dxf_to_svg", render_spy)
    monkeypatch.setattr(service, "save_bytes_as_file", save_spy)

    result = service.get_or_create_dxf_preview(
        db,
        source,
        payload,
        storage=storage,
        request_id="preview-order-1",
    )
    db.commit()

    assert events == ["prepare", "render", "save:True"]
    transfer = db.scalar(select(FileTransfer).where(FileTransfer.file_id == result.preview_file.id))
    assert transfer is not None
    assert transfer.status == "succeeded"


def test_preview_write_failure_keeps_durable_failed_transfer(
    db: Session,
    tmp_path,
    monkeypatch,
) -> None:
    class PreviewWriteFailureStorage(LocalFileStorage):
        def put_fileobj(
            self,
            bucket,
            storage_key,
            fileobj,
            *,
            length,
            content_type=None,
        ):
            if storage_key.startswith("previews/dxf/"):
                raise StorageError("preview write failed")
            return super().put_fileobj(
                bucket,
                storage_key,
                fileobj,
                length=length,
                content_type=content_type,
            )

    payload = _dxf_bytes()
    storage = PreviewWriteFailureStorage(tmp_path / "storage")
    source = _source_file(db, storage, payload)
    monkeypatch.setattr("app.services.storage_service.get_storage_backend", lambda: storage)

    with pytest.raises(AppHTTPException) as exc:
        service.get_or_create_dxf_preview(
            db,
            source,
            payload,
            storage=storage,
            request_id="preview-failure-1",
        )
    db.rollback()

    assert exc.value.detail["code"] == "STORAGE_WRITE_FAILED"
    transfer = db.scalar(select(FileTransfer).where(FileTransfer.operation == "preview_generate"))
    assert transfer is not None
    assert transfer.status == "failed"
