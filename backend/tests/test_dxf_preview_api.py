from __future__ import annotations

import hashlib
from io import BytesIO, StringIO
from uuid import uuid4

import ezdxf
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bootstrap.seed import init_db
from app.main import app
from app.modules.cad_processing.preview import MAX_DXF_SIZE_BYTES
from app.modules.files.interface import FileTransfer, StoredFile
from app.platform.storage.local import LocalFileStorage


def _dxf_bytes() -> bytes:
    doc = ezdxf.new("R2010", setup=True)
    doc.modelspace().add_line((0, 0), (100, 60))
    doc.modelspace().add_circle((30, 20), 10)
    stream = StringIO()
    doc.write(stream)
    return stream.getvalue().encode(doc.output_encoding, errors="replace")


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/sessions",
        json={"username": username, "password": password},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def _admin(client: TestClient) -> dict[str, str]:
    init_db()
    return _login(client, "admin", "SuperAdminPass1")


def _create_engineer(
    client: TestClient,
    admin_headers: dict[str, str],
) -> dict[str, str]:
    username = f"preview-user-{uuid4().hex[:8]}"
    password = "PreviewUserPass1"
    response = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"username": username, "password": password, "real_name": "预览隔离用户"},
    )
    assert response.status_code == 201, response.text
    user_id = response.json()["data"]["id"]
    role_response = client.post(
        f"/api/v1/users/{user_id}/roles",
        headers=admin_headers,
        json={"role_code": "engineer"},
    )
    assert role_response.status_code == 201, role_response.text
    return _login(client, username, password)


def _upload_dxf(
    client: TestClient,
    headers: dict[str, str],
    payload: bytes,
) -> int:
    response = client.post(
        "/api/v1/files",
        headers=headers,
        files={"upload": ("结构详图.dxf", BytesIO(payload), "application/dxf")},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _use_storage(monkeypatch, storage: LocalFileStorage) -> None:
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)


def test_dxf_preview_generates_cache_and_streams_authenticated_content(
    db: Session,
    tmp_path,
    monkeypatch,
) -> None:
    storage = LocalFileStorage(tmp_path / "storage")
    _use_storage(monkeypatch, storage)
    client = TestClient(app)
    headers = _admin(client)
    source_id = _upload_dxf(client, headers, _dxf_bytes())

    generated = client.get(f"/api/v1/files/{source_id}/dxf-preview", headers=headers)

    assert generated.status_code == 200, generated.text
    data = generated.json()["data"]
    assert data["file_id"] == source_id
    assert data["cached"] is False
    assert data["preview_file_id"] > 0
    assert data["content_url"].endswith(f"preview_file_id={data['preview_file_id']}")
    assert data["document_entities"] >= data["modelspace_entities"] >= 2
    assert data["entity_counts"]["LINE"] == 1

    content = client.get(data["content_url"], headers=headers)

    assert content.status_code == 200, content.text
    assert content.headers["content-type"].startswith("image/svg+xml")
    assert int(content.headers["content-length"]) == len(content.content)
    assert content.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in content.headers["content-security-policy"]
    assert b"<svg" in content.content.lower()

    cached = client.get(f"/api/v1/files/{source_id}/dxf-preview", headers=headers)
    assert cached.status_code == 200, cached.text
    assert cached.json()["data"]["cached"] is True
    assert cached.json()["data"]["preview_file_id"] == data["preview_file_id"]

    db.expire_all()
    outbound = db.scalar(
        select(FileTransfer)
        .where(
            FileTransfer.file_id == data["preview_file_id"],
            FileTransfer.direction == "outbound",
            FileTransfer.operation == "preview",
        )
        .order_by(FileTransfer.id.desc())
    )
    assert outbound is not None
    assert outbound.status == "succeeded"
    assert outbound.transferred_bytes == len(content.content)


def test_dxf_preview_rejects_declared_oversize_before_storage_read(
    db: Session,
    tmp_path,
    monkeypatch,
) -> None:
    class GuardedStorage(LocalFileStorage):
        def iter_file(self, bucket: str, storage_key: str, *, chunk_size: int = 1024 * 1024):
            raise AssertionError("oversize source must be rejected before storage read")

    storage = GuardedStorage(tmp_path / "storage")
    _use_storage(monkeypatch, storage)
    client = TestClient(app)
    headers = _admin(client)
    source = StoredFile(
        bucket="dxf-original",
        storage_key="uploads/too-large.dxf",
        original_name="too-large.dxf",
        file_ext=".dxf",
        content_type="application/dxf",
        size_bytes=MAX_DXF_SIZE_BYTES + 1,
        sha256="b" * 64,
        status="available",
    )
    db.add(source)
    db.commit()

    response = client.get(f"/api/v1/files/{source.id}/dxf-preview", headers=headers)

    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "DXF_TOO_LARGE"


def test_dxf_preview_requires_source_access(
    tmp_path,
    monkeypatch,
) -> None:
    storage = LocalFileStorage(tmp_path / "storage")
    _use_storage(monkeypatch, storage)
    client = TestClient(app)
    admin_headers = _admin(client)
    source_id = _upload_dxf(client, admin_headers, _dxf_bytes())
    stranger_headers = _create_engineer(client, admin_headers)

    response = client.get(
        f"/api/v1/files/{source_id}/dxf-preview",
        headers=stranger_headers,
    )

    assert response.status_code == 403, response.text


def test_dxf_preview_content_rejects_unrelated_registered_file(
    db: Session,
    tmp_path,
    monkeypatch,
) -> None:
    storage = LocalFileStorage(tmp_path / "storage")
    _use_storage(monkeypatch, storage)
    client = TestClient(app)
    headers = _admin(client)
    source_id = _upload_dxf(client, headers, _dxf_bytes())
    unrelated_payload = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
    storage.put_fileobj(
        "dwg-reports",
        "previews/unrelated.svg",
        BytesIO(unrelated_payload),
        length=len(unrelated_payload),
        content_type="image/svg+xml",
    )
    unrelated = StoredFile(
        bucket="dwg-reports",
        storage_key="previews/unrelated.svg",
        original_name="unrelated.svg",
        file_ext=".svg",
        content_type="image/svg+xml",
        size_bytes=len(unrelated_payload),
        sha256="c" * 64,
        status="available",
        batch_name="not-a-dxf-preview",
    )
    db.add(unrelated)
    db.commit()

    response = client.get(
        f"/api/v1/files/{source_id}/dxf-preview/content?preview_file_id={unrelated.id}",
        headers=headers,
    )

    assert response.status_code == 404, response.text


def test_dxf_preview_rejects_source_size_mismatch(
    db: Session,
    tmp_path,
    monkeypatch,
) -> None:
    storage = LocalFileStorage(tmp_path / "storage")
    _use_storage(monkeypatch, storage)
    client = TestClient(app)
    headers = _admin(client)
    payload = _dxf_bytes()
    storage.put_fileobj(
        "dxf-original",
        "uploads/size-mismatch.dxf",
        BytesIO(payload),
        length=len(payload),
        content_type="application/dxf",
    )
    source = StoredFile(
        bucket="dxf-original",
        storage_key="uploads/size-mismatch.dxf",
        original_name="size-mismatch.dxf",
        file_ext=".dxf",
        content_type="application/dxf",
        size_bytes=len(payload) + 1,
        sha256=hashlib.sha256(payload).hexdigest(),
        status="available",
    )
    db.add(source)
    db.commit()

    response = client.get(f"/api/v1/files/{source.id}/dxf-preview", headers=headers)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "STORAGE_SIZE_MISMATCH"


def test_dxf_preview_rejects_source_checksum_mismatch(
    db: Session,
    tmp_path,
    monkeypatch,
) -> None:
    storage = LocalFileStorage(tmp_path / "storage")
    _use_storage(monkeypatch, storage)
    client = TestClient(app)
    headers = _admin(client)
    payload = _dxf_bytes()
    storage.put_fileobj(
        "dxf-original",
        "uploads/checksum-mismatch.dxf",
        BytesIO(payload),
        length=len(payload),
        content_type="application/dxf",
    )
    source = StoredFile(
        bucket="dxf-original",
        storage_key="uploads/checksum-mismatch.dxf",
        original_name="checksum-mismatch.dxf",
        file_ext=".dxf",
        content_type="application/dxf",
        size_bytes=len(payload),
        sha256="d" * 64,
        status="available",
    )
    db.add(source)
    db.commit()

    response = client.get(f"/api/v1/files/{source.id}/dxf-preview", headers=headers)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "STORAGE_CHECKSUM_MISMATCH"


def test_dxf_preview_content_rejects_registered_size_mismatch(
    db: Session,
    tmp_path,
    monkeypatch,
) -> None:
    storage = LocalFileStorage(tmp_path / "storage")
    _use_storage(monkeypatch, storage)
    client = TestClient(app)
    headers = _admin(client)
    source_id = _upload_dxf(client, headers, _dxf_bytes())
    generated = client.get(f"/api/v1/files/{source_id}/dxf-preview", headers=headers)
    assert generated.status_code == 200, generated.text
    data = generated.json()["data"]
    preview = db.get(StoredFile, data["preview_file_id"])
    assert preview is not None
    preview.size_bytes += 1
    db.commit()

    response = client.get(data["content_url"], headers=headers)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "STORAGE_SIZE_MISMATCH"


def test_source_soft_delete_invalidates_registered_preview(
    db: Session,
    tmp_path,
    monkeypatch,
) -> None:
    storage = LocalFileStorage(tmp_path / "storage")
    _use_storage(monkeypatch, storage)
    client = TestClient(app)
    headers = _admin(client)
    source_id = _upload_dxf(client, headers, _dxf_bytes())
    generated = client.get(f"/api/v1/files/{source_id}/dxf-preview", headers=headers)
    assert generated.status_code == 200, generated.text
    preview_id = generated.json()["data"]["preview_file_id"]
    preview = db.get(StoredFile, preview_id)
    assert preview is not None
    assert storage.object_exists(preview.bucket, preview.storage_key)

    deleted = client.delete(f"/api/v1/files/{source_id}", headers=headers)

    assert deleted.status_code == 204, deleted.text
    db.expire_all()
    preview = db.get(StoredFile, preview_id)
    assert preview is not None
    assert preview.status == "deleted"
    invalidation = db.scalar(
        select(FileTransfer).where(
            FileTransfer.file_id == preview_id,
            FileTransfer.operation == "preview_invalidate",
        )
    )
    assert invalidation is not None
    assert invalidation.status == "succeeded"
    assert invalidation.actor_user_id is not None
    assert storage.object_exists(preview.bucket, preview.storage_key)
    content = client.get(
        f"/api/v1/files/{source_id}/dxf-preview/content?preview_file_id={preview_id}",
        headers=headers,
    )
    assert content.status_code == 404
