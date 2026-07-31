from __future__ import annotations

import json
import zipfile
from datetime import date, datetime
from io import BytesIO
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.bootstrap.seed import init_db
from app.main import app
from app.modules.files.interface import StoredFile
from app.modules.operations.daily_archive.models import DailyArchiveRun
from app.platform.storage.base import StorageObjectNotFound
from app.platform.storage.local import LocalFileStorage

_DWG = b"AC1027" + b"\x00" * 1018
_BEIJING = ZoneInfo("Asia/Shanghai")


def test_daily_archive_window_and_manifest_use_beijing_wall_time():
    from app.modules.operations.daily_archive.planning import _business_iso, _day_window

    start, end = _day_window(date(2026, 8, 1))

    assert start.isoformat() == "2026-08-01T00:00:00+08:00"
    assert end.isoformat() == "2026-08-02T00:00:00+08:00"
    assert _business_iso(datetime(2026, 8, 1, 1, 2, 3)) == "2026-08-01T01:02:03+08:00"


def _admin_headers(client: TestClient) -> dict[str, str]:
    init_db()
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert login.status_code == 201
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def test_daily_archive_preview_freezes_business_day_snapshot(tmp_path, monkeypatch, db):
    from app.modules.operations.daily_archive.planning import preview_daily_archive

    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)
    inside = StoredFile(
        bucket="dwg-original",
        storage_key="uploads/inside.dwg",
        original_name="inside.dwg",
        file_ext=".dwg",
        content_type="application/acad",
        size_bytes=3,
        sha256="a" * 64,
        status="available",
        created_at=datetime(2026, 7, 20, 0, 0, tzinfo=_BEIJING),
    )
    outside = StoredFile(
        bucket="dwg-original",
        storage_key="uploads/outside.dwg",
        original_name="outside.dwg",
        file_ext=".dwg",
        content_type="application/acad",
        size_bytes=5,
        sha256="b" * 64,
        status="available",
        created_at=datetime(2026, 7, 21, 0, 0, tzinfo=_BEIJING),
    )
    previous_archive = StoredFile(
        bucket="dwg-reports",
        storage_key="daily-archives/2026-07-20/old.zip",
        original_name="old.zip",
        file_ext=".zip",
        content_type="application/zip",
        size_bytes=7,
        sha256="c" * 64,
        status="available",
        created_at=datetime(2026, 7, 20, 10, 0, tzinfo=_BEIJING),
    )
    db.add_all([inside, outside, previous_archive])
    db.commit()

    preview = preview_daily_archive(
        db,
        archive_date=datetime(2026, 7, 20).date(),
        scope_bucket=None,
    )

    assert preview.file_count == 1
    assert preview.total_bytes == 3
    assert preview.excluded_archive_files == 1
    assert preview.bucket_counts == {"dwg-original": 1}
    assert preview.format_counts == {".dwg": 1}
    assert preview.can_archive is True
    assert preview.timezone == "Asia/Shanghai"


def test_daily_archive_api_persists_zip_manifest_and_reuses_submission(
    tmp_path,
    monkeypatch,
    db,
):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)
    client = TestClient(app)
    headers = _admin_headers(client)
    uploaded = client.post(
        "/api/v1/files",
        headers={**headers, "Idempotency-Key": "daily-archive-source"},
        files={"upload": ("daily.dwg", BytesIO(_DWG), "application/acad")},
    )
    assert uploaded.status_code == 201, uploaded.text
    archive_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()

    preview = client.post(
        "/api/v1/data-admin/daily-archives/preview",
        headers=headers,
        json={"archive_date": archive_date},
    )
    assert preview.status_code == 200, preview.text
    preview_data = preview.json()["data"]
    assert preview_data["file_count"] == 1
    assert preview_data["can_archive"] is True

    created = client.post(
        "/api/v1/data-admin/daily-archives",
        headers=headers,
        json={
            "preview_token": preview_data["preview_token"],
            "idempotency_key": "daily-archive-api-1",
        },
    )
    assert created.status_code == 202, created.text
    result = created.json()["data"]
    assert result["status"] == "succeeded"
    assert result["archive_file_id"]
    assert result["manifest_file_id"]
    assert result["reused"] is False

    db.expire_all()
    run = db.get(DailyArchiveRun, result["id"])
    assert run is not None
    archive_file = db.get(StoredFile, run.archive_file_id)
    manifest_file = db.get(StoredFile, run.manifest_file_id)
    assert archive_file is not None and manifest_file is not None
    assert archive_file.bucket == "dwg-reports"
    assert archive_file.storage_key.startswith(f"daily-archives/{archive_date}/")
    assert storage.object_exists(archive_file.bucket, archive_file.storage_key)
    assert storage.object_exists(manifest_file.bucket, manifest_file.storage_key)

    zip_path = storage.local_path(archive_file.bucket, archive_file.storage_key)
    assert zip_path is not None
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        assert "manifest.json" in names
        assert any(name.endswith("_daily.dwg") for name in names)
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["source_manifest_sha256"] == run.source_manifest_sha256
        assert len(manifest["files"]) == 1

    repeated = client.post(
        "/api/v1/data-admin/daily-archives",
        headers=headers,
        json={
            "preview_token": preview_data["preview_token"],
            "idempotency_key": "daily-archive-api-1",
        },
    )
    assert repeated.status_code == 202, repeated.text
    assert repeated.json()["data"]["id"] == result["id"]
    assert repeated.json()["data"]["reused"] is True
    assert len(db.scalars(select(DailyArchiveRun)).all()) == 1


def test_daily_archive_rejects_tampered_preview_token(tmp_path, monkeypatch):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)
    client = TestClient(app)
    headers = _admin_headers(client)

    response = client.post(
        "/api/v1/data-admin/daily-archives",
        headers=headers,
        json={"preview_token": "tampered.token", "idempotency_key": "bad-token"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DAILY_ARCHIVE_PREVIEW_INVALID"


def test_daily_archive_marks_run_failed_when_frozen_object_is_missing(
    tmp_path,
    monkeypatch,
    db,
):
    from app.modules.operations.daily_archive.execution import execute_daily_archive_run
    from app.modules.operations.daily_archive.planning import (
        prepare_daily_archive_run,
        preview_daily_archive,
    )

    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)
    init_db()
    source = StoredFile(
        bucket="dwg-original",
        storage_key="uploads/missing.dwg",
        original_name="missing.dwg",
        file_ext=".dwg",
        content_type="application/acad",
        size_bytes=3,
        sha256="d" * 64,
        status="available",
    )
    db.add(source)
    db.commit()
    preview = preview_daily_archive(
        db,
        archive_date=datetime.now(ZoneInfo("Asia/Shanghai")).date(),
        scope_bucket=None,
    )
    admin_id = db.scalar(select(DailyArchiveRun.actor_user_id).limit(1))
    if admin_id is None:
        from app.modules.identity.interface import User

        admin_id = db.scalar(select(User.id).order_by(User.id).limit(1))
    assert admin_id is not None
    run, reused = prepare_daily_archive_run(
        db,
        actor_user_id=admin_id,
        preview_token=preview.preview_token,
        idempotency_key="missing-object-run",
    )
    assert reused is False
    run_id = run.id
    db.commit()
    factory = sessionmaker(
        bind=db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    with pytest.raises(StorageObjectNotFound):
        execute_daily_archive_run(run_id, factory=factory)

    db.expire_all()
    failed = db.get(DailyArchiveRun, run_id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error_code == "DAILY_ARCHIVE_STORAGE_READ_FAILED"
    assert failed.archive_file_id is None
    assert failed.manifest_file_id is None
