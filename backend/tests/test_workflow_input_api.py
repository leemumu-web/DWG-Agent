from __future__ import annotations

from io import BytesIO

import openpyxl

from app.storage.local_storage import LocalFileStorage
from tests.test_workflow_api import (
    _admin_headers,
    _client,
    _engineer_user,
    _project,
)


def _xlsx() -> bytes:
    workbook = openpyxl.Workbook()
    workbook.active["A1"] = "构件编号"
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _use_storage(monkeypatch, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr("app.services.storage_service.get_storage_backend", lambda: storage)
    monkeypatch.setattr("app.services.workflow_input_service.get_storage_backend", lambda: storage)
    return storage


def _setup(client, prefix="input-api"):
    admin_headers = _admin_headers(client)
    _, owner_headers = _engineer_user(client, admin_headers, prefix)
    project_id = _project(client, owner_headers)
    created = client.post(
        "/api/v1/workflows",
        headers=owner_headers,
        json={
            "project_id": project_id,
            "name": "Production source intake",
            "workflow_type": "linux_production",
        },
    )
    assert created.status_code == 201, created.text
    return admin_headers, owner_headers, project_id, created.json()["data"]["id"]


def _upload(client, headers, name: str, payload: bytes, batch_id: int) -> int:
    response = client.post(
        "/api/v1/files",
        headers={**headers, "Idempotency-Key": f"input-{batch_id}-{name}"},
        params={"batch_name": f"workflow-input-{batch_id}"},
        files={"upload": (name, payload, "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def test_create_register_list_and_prepare_conversion(monkeypatch, tmp_path):
    _use_storage(monkeypatch, tmp_path)
    client = _client()
    _, owner_headers, _, workflow_id = _setup(client)
    dispatched: list[tuple[str, list[tuple[int, int]]]] = []
    monkeypatch.setattr("app.core.config.settings.dxf_pipeline_enabled", True)
    monkeypatch.setattr(
        "app.api.v1.workflow_inputs_api.dispatch_committed_conversion_batch",
        lambda *, task_type, jobs: dispatched.append((task_type, jobs)),
    )

    created = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch", headers=owner_headers
    )
    replay = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch", headers=owner_headers
    )
    assert created.status_code == 201, created.text
    assert replay.status_code == 200, replay.text
    batch_id = created.json()["data"]["id"]
    assert replay.json()["data"]["id"] == batch_id

    dwg_id = _upload(client, owner_headers, "A.dwg", b"AC1027" + bytes(2048), batch_id)
    excel_id = _upload(client, owner_headers, "parts.xlsx", _xlsx(), batch_id)
    for file_id in (dwg_id, excel_id):
        registered = client.post(
            f"/api/v1/workflows/{workflow_id}/input-batch/files",
            headers=owner_headers,
            json={"file_id": file_id},
        )
        assert registered.status_code == 201, registered.text

    replayed = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch/files",
        headers=owner_headers,
        json={"file_id": dwg_id},
    )
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["data"]["reused"] is True

    conversion = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch/conversion-requests",
        headers=owner_headers,
    )
    detail = client.get(
        f"/api/v1/workflows/{workflow_id}/input-batch", headers=owner_headers
    )

    assert conversion.status_code == 202, conversion.text
    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["counts"] == {
        "dwg": 1,
        "excel": 1,
        "paired": 0,
        "converting": 1,
        "failed": 0,
    }
    assert data["freeze_ready"] is False
    assert len(data["items"]) == 2
    assert dispatched and dispatched[0][0] == "convert_dwg_to_dxf"


def test_registration_rejects_human_dxf_and_second_excel(monkeypatch, tmp_path):
    _use_storage(monkeypatch, tmp_path)
    client = _client()
    _, owner_headers, _, workflow_id = _setup(client, "input-errors")
    batch = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch", headers=owner_headers
    ).json()["data"]
    first_excel = _upload(client, owner_headers, "first.xlsx", _xlsx(), batch["id"])
    second_excel = _upload(client, owner_headers, "second.xlsx", _xlsx(), batch["id"])
    dxf = _upload(client, owner_headers, "manual.dxf", b"0\nEOF\n", batch["id"])
    assert client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch/files",
        headers=owner_headers,
        json={"file_id": first_excel},
    ).status_code == 201

    duplicate = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch/files",
        headers=owner_headers,
        json={"file_id": second_excel},
    )
    manual_dxf = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch/files",
        headers=owner_headers,
        json={"file_id": dxf},
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "INPUT_EXCEL_ALREADY_EXISTS"
    assert manual_dxf.status_code == 422
    assert manual_dxf.json()["error"]["code"] == "INPUT_DXF_NOT_ALLOWED"


def test_input_batch_is_project_scoped(monkeypatch, tmp_path):
    _use_storage(monkeypatch, tmp_path)
    client = _client()
    admin_headers, owner_headers, _, workflow_id = _setup(client, "input-owner")
    _, stranger_headers = _engineer_user(client, admin_headers, "input-stranger")
    assert client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch", headers=owner_headers
    ).status_code == 201

    forbidden = client.get(
        f"/api/v1/workflows/{workflow_id}/input-batch", headers=stranger_headers
    )

    assert forbidden.status_code == 403


def test_input_batch_openapi_exposes_complete_guarded_surface():
    paths = _client().app.openapi()["paths"]

    expected = {
        "/api/v1/workflows/{workflow_id}/input-batch": {"get", "post"},
        "/api/v1/workflows/{workflow_id}/input-batch/files": {"post"},
        "/api/v1/workflows/{workflow_id}/input-batch/files/{item_id}": {"delete"},
        "/api/v1/workflows/{workflow_id}/input-batch/conversion-requests": {"post"},
        "/api/v1/workflows/{workflow_id}/input-batch/freeze": {"post"},
    }
    for path, methods in expected.items():
        assert path in paths
        assert methods <= set(paths[path])
        for method in methods:
            assert paths[path][method]["summary"]
            success = next(
                response
                for code, response in paths[path][method]["responses"].items()
                if code.startswith("2")
            )
            if code := success.get("content"):
                assert code["application/json"]["schema"]
