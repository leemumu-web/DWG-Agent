from __future__ import annotations

from io import StringIO

import ezdxf
import pytest
from fastapi.testclient import TestClient

from app.bootstrap.seed import init_db
from app.main import app
from app.modules.remnant_inventory.models import RemnantImportItem
from app.platform.config.settings import settings
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


def _dxf_bytes() -> bytes:
    stream = StringIO()
    document = ezdxf.new("R2018")
    document.modelspace().add_text("材质: Q235B")
    document.write(stream)
    return stream.getvalue().encode("utf-8")


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
