from __future__ import annotations

from io import BytesIO

import openpyxl
from sqlalchemy import select

from app.modules.workflows.interface import WorkflowInputBatch
from app.platform.storage.local import LocalFileStorage
from tests.support import workflow_api as workflow_test_api


def _xlsx() -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "原表"
    sheet.append(["构件编号", "零件号", "规格", "长度(mm)", "材质", "数量"])
    sheet.append(["C-1", None, "BH500*300*12*20", 1000, "Q355B", 1])
    sheet.append([None, "P-1", "PL10*200", 100, "Q355B", 1])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _invalid_xlsx() -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "构件汇总"
    sheet.append(["构件编号", "规格", "长度(mm)", "材质", "数量"])
    sheet.append(["C-1", "BH500*300*12*20", 1000, "Q355B", 1])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _use_storage(monkeypatch, tmp_path):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr("app.platform.storage.factory.get_storage_backend", lambda: storage)
    monkeypatch.setattr(
        "app.modules.workflows.intake.registration.get_storage_backend", lambda: storage
    )
    return storage


def _setup(client, prefix="input-api"):
    admin_headers = workflow_test_api.admin_headers(client)
    _, owner_headers = workflow_test_api.create_engineer_user(client, admin_headers, prefix)
    project_id = workflow_test_api.create_project(client, owner_headers)
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
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client)
    dispatched: list[tuple[str, list[tuple[int, int]]]] = []
    monkeypatch.setattr("app.platform.config.settings.settings.dxf_pipeline_enabled", True)
    monkeypatch.setattr(
        "app.modules.workflows.routes.intake.dispatch_committed_conversion_batch",
        lambda *, task_type, jobs: dispatched.append((task_type, jobs)),
    )

    created = client.post(f"/api/v1/workflows/{workflow_id}/input-batch", headers=owner_headers)
    replay = client.post(f"/api/v1/workflows/{workflow_id}/input-batch", headers=owner_headers)
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
    detail = client.get(f"/api/v1/workflows/{workflow_id}/input-batch", headers=owner_headers)

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
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client, "input-errors")
    batch = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch", headers=owner_headers
    ).json()["data"]
    first_excel = _upload(client, owner_headers, "first.xlsx", _xlsx(), batch["id"])
    second_excel = _upload(client, owner_headers, "second.xlsx", _xlsx(), batch["id"])
    dxf = _upload(
        client,
        owner_headers,
        "manual.dxf",
        b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n",
        batch["id"],
    )
    assert (
        client.post(
            f"/api/v1/workflows/{workflow_id}/input-batch/files",
            headers=owner_headers,
            json={"file_id": first_excel},
        ).status_code
        == 201
    )

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


def test_invalid_excel_registration_commits_failed_item_before_422(monkeypatch, tmp_path):
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client, "invalid-excel-ledger")
    batch = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch",
        headers=owner_headers,
    ).json()["data"]
    excel_id = _upload(
        client,
        owner_headers,
        "component-only.xlsx",
        _invalid_xlsx(),
        batch["id"],
    )

    registered = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch/files",
        headers=owner_headers,
        json={"file_id": excel_id},
    )

    assert registered.status_code == 422, registered.text
    error = registered.json()["error"]
    assert error["code"] == "EXCEL_INPUT_COMPONENT_ONLY"
    assert error["details"]["failure"]["action"]

    detail = client.get(
        f"/api/v1/workflows/{workflow_id}/input-batch",
        headers=owner_headers,
    )
    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["status"] == "needs_attention"
    assert data["freeze_ready"] is False
    assert data["counts"]["excel"] == 1
    assert data["counts"]["failed"] == 1
    excel_issue = next(issue for issue in data["issues"] if issue["code"] == error["code"])
    assert excel_issue["failure"]["code"] == error["code"]
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["status"] == "failed"
    assert item["error_code"] == error["code"]
    assert item["validation"]["failure"] == error["details"]["failure"]
    assert item["validation_contract_version"] == 1
    assert len(item["validated_sha256"]) == 64


def test_input_batch_is_project_scoped(monkeypatch, tmp_path):
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    admin_headers, owner_headers, _, workflow_id = _setup(client, "input-owner")
    _, stranger_headers = workflow_test_api.create_engineer_user(client, admin_headers, "input-stranger")
    assert (
        client.post(
            f"/api/v1/workflows/{workflow_id}/input-batch", headers=owner_headers
        ).status_code
        == 201
    )

    forbidden = client.get(f"/api/v1/workflows/{workflow_id}/input-batch", headers=stranger_headers)

    assert forbidden.status_code == 403


def test_input_batch_openapi_exposes_complete_guarded_surface():
    paths = workflow_test_api.client().app.openapi()["paths"]

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


def test_frozen_input_source_cannot_be_deleted_through_files_api(db, monkeypatch, tmp_path):
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client, "frozen-delete")
    batch_data = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch", headers=owner_headers
    ).json()["data"]
    file_id = _upload(
        client,
        owner_headers,
        "protected.dwg",
        b"AC1027" + bytes(2048),
        batch_data["id"],
    )
    registered = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch/files",
        headers=owner_headers,
        json={"file_id": file_id},
    )
    assert registered.status_code == 201, registered.text
    batch = db.scalar(select(WorkflowInputBatch).where(WorkflowInputBatch.id == batch_data["id"]))
    assert batch is not None
    batch.status = "frozen"
    db.commit()

    deleted = client.delete(f"/api/v1/files/{file_id}", headers=owner_headers)

    assert deleted.status_code == 409, deleted.text
    assert deleted.json()["error"]["code"] == "FILE_REFERENCED_BY_FROZEN_INPUT"
