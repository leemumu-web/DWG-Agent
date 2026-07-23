from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO

import ezdxf
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


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setattr(settings, "remnant_inventory_enabled", True)
    init_db()
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def admin_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


@pytest.fixture
def worker_headers(client: TestClient) -> dict[str, str]:
    with get_test_session_factory()() as db:
        role = db.scalar(select(Role).where(Role.code == "remnant_worker"))
        assert role is not None
        user = User(
            username="material-worker",
            real_name="材质工人",
            password_hash=hash_password("WorkerPass123"),
            roles=[role],
        )
        db.add(user)
        db.commit()
    response = client.post(
        "/api/v1/auth/sessions",
        json={"username": "material-worker", "password": "WorkerPass123"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def _dxf_bytes() -> bytes:
    stream = StringIO()
    document = ezdxf.new("R2018")
    document.modelspace().add_text("材质: Q235B")
    document.write(stream)
    return stream.getvalue().encode("utf-8")


def _seed_global_remnants() -> tuple[int, int]:
    with get_test_session_factory()() as db:
        worker = db.scalar(select(User).where(User.username == "material-worker"))
        assert worker is not None
        materials = [
            RemnantMaterial(code="Q235B", family_code="Q235", enabled=True),
            RemnantMaterial(code="Q355B", family_code="Q355", enabled=True),
        ]
        db.add_all(materials)
        db.flush()
        batch = RemnantImportBatch(
            created_by=worker.id,
            status="confirmed",
            total_count=4,
            confirmed_count=4,
        )
        db.add(batch)
        db.flush()
        rows = [
            ("available", materials[0], Decimal("10"), "精武路项目A", ["JWL-1014-B-4"]),
            ("reserved", materials[1], Decimal("20"), "南京北站项目B", ["ND-1053-3"]),
            ("used", materials[0], Decimal("30"), "精武路项目C", ["DS-481-4"]),
            ("archived", materials[1], Decimal("40"), "其他项目D", ["3CB-3D-1"]),
        ]
        for index, (status, material, thickness, project, parts) in enumerate(rows, 1):
            digest = f"{index:064x}"
            source = StoredFile(
                bucket="test",
                storage_key=f"remnants/{index}.dxf",
                original_name=f"余料-{index}.dxf",
                file_ext=".dxf",
                content_type="application/dxf",
                size_bytes=100,
                sha256=digest,
                uploaded_by=worker.id,
            )
            db.add(source)
            db.flush()
            item = RemnantImportItem(
                batch_id=batch.id,
                source_file_id=source.id,
                dxf_file_id=source.id,
                source_sha256=digest,
                source_ext=".dxf",
                status="confirmed",
            )
            db.add(item)
            db.flush()
            remnant = Remnant(
                import_item_id=item.id,
                source_file_id=source.id,
                dxf_file_id=source.id,
                source_sha256=digest,
                thickness_mm=thickness,
                material_id=material.id,
                project_no=project,
                status=status,
                imported_by=worker.id,
                confirmed_by=worker.id,
                confirmed_at=datetime.now(UTC),
            )
            db.add(remnant)
            db.flush()
            db.add_all([RemnantPart(remnant_id=remnant.id, part_no=part) for part in parts])
        db.commit()
        return materials[0].id, materials[1].id


def test_feature_disabled_returns_stable_not_found(client, admin_headers, monkeypatch) -> None:
    monkeypatch.setattr(settings, "remnant_inventory_enabled", False)
    response = client.get("/api/v1/remnant-materials", headers=admin_headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REMNANT_INVENTORY_DISABLED"


def test_material_api_uses_envelope_auth_and_admin_permissions(client, admin_headers) -> None:
    assert client.get("/api/v1/remnant-materials").status_code == 401
    created = client.post(
        "/api/v1/remnant-materials",
        headers=admin_headers,
        json={"code": "Q235B", "family_code": "Q235"},
    )
    assert created.status_code == 201, created.text
    material_id = created.json()["data"]["id"]
    aliases = client.put(
        f"/api/v1/remnant-materials/{material_id}/aliases",
        headers=admin_headers,
        json={"aliases": ["普板", "Q235-B"]},
    )
    listed = client.get("/api/v1/remnant-materials", headers=admin_headers)
    assert aliases.status_code == 200
    assert listed.status_code == 200
    assert listed.json()["data"][0]["code"] == "Q235B"
    assert listed.json()["meta"]["request_id"]
    with get_test_session_factory()() as db:
        actions = set(db.scalars(select(AuditLog.action)).all())
    assert {"remnants.material.create", "remnants.material.aliases"} <= actions


def test_worker_resolves_or_creates_material_but_cannot_administer_it(
    client, worker_headers
) -> None:
    created = client.post(
        "/api/v1/remnant-materials/resolve-or-create",
        headers=worker_headers,
        json={"code": "q355b"},
    )
    assert created.status_code == 201, created.text
    payload = created.json()["data"]
    assert payload["created"] is True
    assert payload["material"]["code"] == payload["material"]["family_code"] == "Q355B"

    repeated = client.post(
        "/api/v1/remnant-materials/resolve-or-create",
        headers=worker_headers,
        json={"code": "Q355B"},
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["data"]["created"] is False

    material_id = payload["material"]["id"]
    assert client.patch(
        f"/api/v1/remnant-materials/{material_id}",
        headers=worker_headers,
        json={"enabled": False},
    ).status_code == 403
    assert client.put(
        f"/api/v1/remnant-materials/{material_id}/aliases",
        headers=worker_headers,
        json={"aliases": ["Q355-B"]},
    ).status_code == 403

    with get_test_session_factory()() as db:
        audits = list(
            db.scalars(
                select(AuditLog).where(AuditLog.action == "remnants.material.create")
            ).all()
        )
    assert len(audits) == 1


def test_multipart_import_edit_partial_confirm_and_inventory_lifecycle(
    client, admin_headers, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.modules.remnant_inventory.routes.dispatch_import_execution",
        lambda _dispatch: None,
    )
    material = client.post(
        "/api/v1/remnant-materials",
        headers=admin_headers,
        json={"code": "Q235B", "family_code": "Q235"},
    ).json()["data"]
    created = client.post(
        "/api/v1/remnant-import-batches",
        headers={**admin_headers, "X-Request-ID": "req-remnant-api"},
        files=[("files", ("actual-source.dxf", _dxf_bytes(), "application/dxf"))],
    )
    assert created.status_code == 202, created.text
    batch_id = created.json()["data"]["id"]
    item_id = created.json()["data"]["items"][0]["id"]

    with get_test_session_factory()() as db:
        item = db.get(RemnantImportItem, item_id)
        item.status = "pending_confirmation"
        item.material_candidates_json = [{"value": "Q235B", "evidence": []}]
        item.project_candidates_json = [{"value": "P-A", "evidence": []}]
        item.part_candidates_json = [{"value": "L-1", "evidence": []}]
        db.commit()

    detail = client.get(f"/api/v1/remnant-import-batches/{batch_id}", headers=admin_headers)
    assert detail.status_code == 200
    edited = client.patch(
        f"/api/v1/remnant-import-items/{item_id}",
        headers=admin_headers,
        json={
            "thickness_mm": "10.000",
            "material_id": material["id"],
            "project_no": "P-A",
            "parts": ["L-1", "L-2"],
        },
    )
    assert edited.status_code == 200, edited.text
    confirmed = client.post(
        "/api/v1/remnant-import-items/bulk-confirm",
        headers=admin_headers,
        json={"item_ids": [item_id, 999999]},
    )
    assert confirmed.status_code == 200, confirmed.text
    payload = confirmed.json()["data"]
    remnant_id = payload["confirmed"][0]["remnant_id"]
    assert payload["invalid"][0]["code"] == "REMNANT_IMPORT_ITEM_NOT_FOUND"

    search = client.get(
        "/api/v1/remnants",
        headers=admin_headers,
        params={"material_id": material["id"], "thickness_mm": "10.000"},
    )
    assert search.status_code == 200, search.text
    assert search.json()["data"][0]["id"] == remnant_id
    assert search.json()["pagination"]["total"] == 1

    reserved = client.post(
        f"/api/v1/remnants/{remnant_id}/reserve",
        headers=admin_headers,
        json={"version": 1},
    )
    assert reserved.status_code == 200, reserved.text
    download = client.get(f"/api/v1/remnants/{remnant_id}/original-download", headers=admin_headers)
    assert download.status_code == 200
    assert download.json()["data"]["file_name"] == "actual-source.dxf"
    assert download.json()["data"]["file_ext"] == ".dxf"
    assert "traceback" not in download.text.lower()
    with get_test_session_factory()() as db:
        actions = set(db.scalars(select(AuditLog.action)).all())
    assert {
        "remnants.import",
        "remnants.import.correct",
        "remnants.import.confirm",
        "remnants.reserve",
    } <= actions


def test_validation_and_missing_resources_keep_stable_error_envelopes(
    client, admin_headers
) -> None:
    response = client.get(
        "/api/v1/remnants",
        headers=admin_headers,
        params={"material_id": 1, "thickness_mm": "not-a-number"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    missing = client.get("/api/v1/remnants/999999", headers=admin_headers)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "REMNANT_NOT_FOUND"


def test_worker_can_page_filter_and_sort_the_global_inventory(
    client, worker_headers
) -> None:
    q235_id, _q355_id = _seed_global_remnants()

    first_page = client.get(
        "/api/v1/remnants/all",
        headers=worker_headers,
        params={"page": 1, "page_size": 2, "sort": "thickness_desc"},
    )

    assert first_page.status_code == 200, first_page.text
    assert first_page.json()["pagination"] == {
        "page": 1,
        "page_size": 2,
        "total": 4,
        "total_pages": 2,
    }
    assert [row["status"] for row in first_page.json()["data"]] == ["archived", "used"]
    assert [row["thickness_mm"] for row in first_page.json()["data"]] == ["40.000", "30.000"]

    filtered = client.get(
        "/api/v1/remnants/all",
        headers=worker_headers,
        params={
            "material_id": q235_id,
            "thickness_mm": "10",
            "statuses": "available",
            "project": "精武路",
            "part": "JWL-1014",
        },
    )

    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["pagination"]["total"] == 1
    assert filtered.json()["data"][0]["parts"] == ["JWL-1014-B-4"]
