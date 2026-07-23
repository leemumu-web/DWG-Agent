from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.bootstrap.seed import init_db
from app.main import app
from app.modules.files.interface import StoredFile
from app.modules.identity.interface import Role, User
from app.modules.operations.audit.models import AuditLog
from app.modules.remnant_inventory.models import (
    Remnant,
    RemnantImportBatch,
    RemnantImportItem,
    RemnantMaterial,
    RemnantPart,
)
from app.platform.config.settings import settings
from app.platform.security.tokens import hash_password
from tests.support.database import get_test_session_factory

HEADERS = [
    "余料编号",
    "材质",
    "厚度(mm)",
    "项目编号",
    "零件编号",
    "库存状态",
    "原始图纸文件名",
    "导入人",
    "导入时间",
    "当前预留人",
    "预留时间",
    "领用人",
    "领用时间",
    "最后更新时间",
]


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setattr(settings, "remnant_inventory_enabled", True)
    init_db()
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def worker_headers(client: TestClient) -> dict[str, str]:
    with get_test_session_factory()() as db:
        role = db.scalar(select(Role).where(Role.code == "remnant_worker"))
        assert role is not None
        worker = User(
            username="export-worker",
            real_name="导出工人",
            password_hash=hash_password("WorkerPass123"),
            roles=[role],
        )
        db.add(worker)
        db.commit()
    response = client.post(
        "/api/v1/auth/sessions",
        json={"username": "export-worker", "password": "WorkerPass123"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def _seed_remnant() -> int:
    with get_test_session_factory()() as db:
        worker = db.scalar(select(User).where(User.username == "export-worker"))
        assert worker is not None
        material = RemnantMaterial(code="Q355B", family_code="Q355", enabled=True)
        db.add(material)
        db.flush()
        source = StoredFile(
            bucket="test",
            storage_key="remnants/export-source.dxf",
            original_name="精武路余料图.dxf",
            file_ext=".dxf",
            content_type="application/dxf",
            size_bytes=100,
            sha256="1" * 64,
            uploaded_by=worker.id,
        )
        db.add(source)
        db.flush()
        batch = RemnantImportBatch(
            created_by=worker.id,
            status="confirmed",
            total_count=1,
            confirmed_count=1,
        )
        db.add(batch)
        db.flush()
        item = RemnantImportItem(
            batch_id=batch.id,
            source_file_id=source.id,
            dxf_file_id=source.id,
            source_sha256="1" * 64,
            source_ext=".dxf",
            status="confirmed",
        )
        db.add(item)
        db.flush()
        timestamp = datetime(2026, 7, 23, 1, 2, 3, tzinfo=UTC)
        remnant = Remnant(
            import_item_id=item.id,
            source_file_id=source.id,
            dxf_file_id=source.id,
            source_sha256="1" * 64,
            thickness_mm="28.000",
            material_id=material.id,
            project_no="精武路外框项目",
            status="used",
            imported_by=worker.id,
            confirmed_by=worker.id,
            confirmed_at=timestamp,
            used_by=worker.id,
            used_at=timestamp,
        )
        db.add(remnant)
        db.flush()
        db.add_all(
            [
                RemnantPart(remnant_id=remnant.id, part_no="JWL-1014-B-4"),
                RemnantPart(remnant_id=remnant.id, part_no="ND-1053-3"),
            ]
        )
        db.commit()
        return remnant.id


def test_worker_exports_all_remnants_as_one_styled_row_per_remnant(
    client, worker_headers
) -> None:
    remnant_id = _seed_remnant()

    response = client.get("/api/v1/remnants/export.xlsx", headers=worker_headers)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "filename*=utf-8''" in response.headers["content-disposition"].lower()
    assert response.content.startswith(b"PK")
    workbook = openpyxl.load_workbook(BytesIO(response.content))
    assert workbook.sheetnames == ["全部余料"]
    sheet = workbook["全部余料"]
    assert sheet.freeze_panes == "A2"
    assert [cell.value for cell in sheet[1]] == HEADERS
    values = [cell.value for cell in sheet[2]]
    assert values[:8] == [
        remnant_id,
        "Q355B",
        28,
        "精武路外框项目",
        "JWL-1014-B-4、ND-1053-3",
        "已领用",
        "精武路余料图.dxf",
        "导出工人",
    ]
    assert isinstance(values[8], datetime)
    assert values[9:12] == [None, None, "导出工人"]
    assert isinstance(values[12], datetime)
    assert isinstance(values[13], datetime)
    with get_test_session_factory()() as db:
        assert db.scalar(
            select(AuditLog).where(AuditLog.action == "remnants.export")
        ) is not None


def test_empty_inventory_export_still_contains_the_header(client, worker_headers) -> None:
    response = client.get("/api/v1/remnants/export.xlsx", headers=worker_headers)

    assert response.status_code == 200, response.text
    workbook = openpyxl.load_workbook(BytesIO(response.content))
    sheet = workbook["全部余料"]
    assert sheet.max_row == 1
    assert [cell.value for cell in sheet[1]] == HEADERS
