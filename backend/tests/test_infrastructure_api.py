from __future__ import annotations

import hashlib
from io import BytesIO

from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.init_db import init_db
from app.main import app
from app.models.file import StoredFile
from app.services import infrastructure_service
from app.storage.base import StorageConfigurationError
from app.storage.local_storage import LocalFileStorage

_DWG_STUB = b"AC1027" + b"\x00" * 1018  # >= 1024 bytes minimum file size


def _admin_headers(client: TestClient) -> dict[str, str]:
    init_db()
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert login.status_code == 201, login.text
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def _viewer_headers(client: TestClient) -> dict[str, str]:
    """Create a fresh viewer-scoped user and return its auth headers."""
    import uuid

    admin_headers = _admin_headers(client)
    username = f"infra-viewer-{uuid.uuid4().hex[:10]}"
    password = "ViewerPass1234"
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"username": username, "password": password, "real_name": "Infra Viewer"},
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["data"]["id"]

    assign = client.post(
        f"/api/v1/users/{user_id}/roles",
        headers=admin_headers,
        json={"role_code": "viewer"},
    )
    assert assign.status_code == 201, assign.text

    login = client.post(
        "/api/v1/auth/sessions", json={"username": username, "password": password}
    )
    assert login.status_code == 201, login.text
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def test_infrastructure_overview_requires_admin():
    client = TestClient(app)
    headers = _admin_headers(client)

    response = client.get("/api/v1/system/infrastructure", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["status"] in {"ok", "degraded"}
    assert payload["database"]["status"] == "ok"
    assert payload["database"]["engine"] == "sqlite"  # test isolation uses SQLite
    assert payload["database"]["table_count"] is not None
    assert payload["storage"]["backend"] == "local"
    assert isinstance(payload["storage"]["buckets"], list)
    assert payload["catalog"]["available_files"] == 0
    assert payload["catalog"]["tracked_bytes"] == 0
    assert payload["recovery"]["automated_backup"] is False
    # All five top-level infrastructure sections must be present (req 2).
    for section in ("database", "storage", "catalog", "capacity", "recovery"):
        assert section in payload, f"missing top-level section: {section}"
    # Local backend must report real disk capacity (req 3).
    assert payload["capacity"]["status"] == "ok"
    assert isinstance(payload["capacity"]["disk_total_bytes"], int)
    assert payload["capacity"]["disk_total_bytes"] > 0


def test_infrastructure_overview_reflects_uploaded_file():
    client = TestClient(app)
    headers = _admin_headers(client)

    upload = client.post(
        "/api/v1/files",
        headers=headers,
        files={"upload": ("sample.dwg", BytesIO(_DWG_STUB), "application/acad")},
    )
    assert upload.status_code == 201, upload.text

    response = client.get("/api/v1/system/infrastructure", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["catalog"]["available_files"] == 1
    assert payload["catalog"]["tracked_bytes"] == len(_DWG_STUB)
    assert ".dwg" in payload["catalog"]["extensions"]
    # The original bucket should now show at least one tracked file.
    original = next(b for b in payload["storage"]["buckets"] if b["name"] == "dwg-original")
    assert original["tracked_files"] == 1


def test_infrastructure_overview_forbidden_for_non_admin():
    client = TestClient(app)
    viewer = _viewer_headers(client)
    response = client.get("/api/v1/system/infrastructure", headers=viewer)
    assert response.status_code == 403, response.text


def test_infrastructure_overview_requires_auth():
    client = TestClient(app)
    response = client.get("/api/v1/system/infrastructure")
    assert response.status_code == 401, response.text


# ---------------------------------------------------------------------------
# Service-layer / unit tests (no HTTP): exercise the reconciliation logic
# directly against the in-memory SQLite session provided by the `db` fixture.
# ---------------------------------------------------------------------------


def _make_stored_file(
    *, bucket: str, status: str, size_bytes: int, file_ext: str = ".dwg"
) -> StoredFile:
    """Build a minimal valid StoredFile row for reconciliation tests."""
    key = f"uploads/{status}-{size_bytes}-{file_ext}"
    digest = hashlib.sha256(key.encode()).hexdigest()
    return StoredFile(
        bucket=bucket,
        storage_key=key,
        original_name=f"sample{file_ext}",
        file_ext=file_ext,
        content_type="application/octet-stream",
        size_bytes=size_bytes,
        sha256=digest,
        md5=digest[:32],
        status=status,
    )


def test_capacity_reports_positive_disk_total_for_local(db):
    """Req 3: local backend capacity has status 'ok' and a positive int total."""
    overview = infrastructure_service.infrastructure_overview(db)
    assert settings.storage_backend == "local"
    capacity = overview["capacity"]
    assert capacity["status"] == "ok"
    assert isinstance(capacity["disk_total_bytes"], int)
    assert capacity["disk_total_bytes"] > 0
    assert isinstance(capacity["disk_used_bytes"], int)
    assert isinstance(capacity["disk_free_bytes"], int)


def test_bucket_object_counts_counts_files_and_zero_for_missing(tmp_path):
    """Req 4: LocalFileStorage counts objects per bucket dir, 0 for missing."""
    storage = LocalFileStorage(tmp_path)
    bucket = settings.minio_bucket_original
    bucket_dir = tmp_path / bucket
    (bucket_dir / "uploads").mkdir(parents=True)
    (bucket_dir / "top.dwg").write_bytes(b"x")
    (bucket_dir / "uploads" / "nested.dwg").write_bytes(b"y")

    counts = storage.bucket_object_counts([bucket, "does-not-exist"])

    # rglob must count files in nested subdirectories too.
    assert counts[bucket] == 2
    # A bucket directory that does not exist reconciles to 0, not an error.
    assert counts["does-not-exist"] == 0


def test_check_storage_degrades_on_configuration_error(db, monkeypatch):
    """Req 5: a StorageConfigurationError degrades to status 'error', no raise."""

    def _boom():
        raise StorageConfigurationError("backend not configured")

    monkeypatch.setattr(infrastructure_service, "get_storage_backend", _boom)

    result = infrastructure_service._check_storage(db)

    assert result["status"] == "error"
    assert result["backend"] == settings.storage_backend
    assert result["buckets"] == []
    assert "latency_ms" in result


def test_overview_degraded_when_storage_errors(db, monkeypatch):
    """A storage error must degrade the aggregate status without a 500/raise."""

    def _boom():
        raise StorageConfigurationError("backend not configured")

    monkeypatch.setattr(infrastructure_service, "get_storage_backend", _boom)

    overview = infrastructure_service.infrastructure_overview(db)
    assert overview["status"] == "degraded"
    assert overview["storage"]["status"] == "error"
    # Database and catalog must still be reported despite storage failure.
    assert overview["database"]["status"] == "ok"
    assert "catalog" in overview


def test_tracked_files_counts_only_available(db):
    """Req 6: bucket tracked_files reconciliation counts only status='available'."""
    bucket = settings.minio_bucket_original
    db.add_all(
        [
            _make_stored_file(bucket=bucket, status="available", size_bytes=1024),
            _make_stored_file(bucket=bucket, status="available", size_bytes=2048),
            _make_stored_file(bucket=bucket, status="deleted", size_bytes=4096),
            _make_stored_file(bucket=bucket, status="pending", size_bytes=8192),
        ]
    )
    # Commit (not just flush): _check_database reflects table names via
    # inspect(engine), which under the shared in-memory StaticPool connection
    # discards uncommitted rows. Committed rows survive that reflection.
    db.commit()

    overview = infrastructure_service.infrastructure_overview(db)
    entry = next(b for b in overview["storage"]["buckets"] if b["name"] == bucket)
    assert entry["tracked_files"] == 2  # deleted + pending excluded


def test_catalog_stats_count_only_available(db):
    """Req 7: catalog available_files / tracked_bytes count only available rows."""
    bucket = settings.minio_bucket_original
    db.add_all(
        [
            _make_stored_file(bucket=bucket, status="available", size_bytes=100),
            _make_stored_file(bucket=bucket, status="available", size_bytes=250),
            _make_stored_file(bucket=bucket, status="deleted", size_bytes=99999),
            _make_stored_file(bucket=bucket, status="pending", size_bytes=88888),
        ]
    )
    db.commit()  # see note in test_tracked_files_counts_only_available

    overview = infrastructure_service.infrastructure_overview(db)
    catalog = overview["catalog"]
    assert catalog["available_files"] == 2
    assert catalog["tracked_bytes"] == 350  # only the two available sizes


def test_default_bucket_object_counts_returns_zero():
    """The abstract base default yields 0 per bucket (backends override)."""
    from app.storage.base import AbstractStorageBackend

    class _StubBackend(AbstractStorageBackend):
        def check_health(self) -> None:  # pragma: no cover - trivial stub
            return None

        def put_fileobj(self, *a, **k) -> None:  # pragma: no cover
            return None

        def iter_file(self, *a, **k):  # pragma: no cover
            return iter(())

        def local_path(self, *a, **k):  # pragma: no cover
            return None

        def delete_object(self, *a, **k) -> None:  # pragma: no cover
            return None

        def stat_object(self, *a, **k):  # pragma: no cover
            from app.storage.base import StorageObjectNotFound

            raise StorageObjectNotFound("missing")

        def list_objects(self, *a, **k):  # pragma: no cover
            from app.storage.base import ObjectPage

            return ObjectPage(items=[], next_cursor=None)

    # _StubBackend does NOT override bucket_object_counts, so it exercises the
    # documented base default of zero-for-every-bucket.
    assert _StubBackend().bucket_object_counts(["a", "b"]) == {"a": 0, "b": 0}
