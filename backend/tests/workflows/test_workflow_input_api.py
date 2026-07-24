from __future__ import annotations

import json
import zipfile
from io import BytesIO

import openpyxl
import pytest
from sqlalchemy import select

from app.modules.workflows import interface as workflow_service
from app.modules.workflows.intake.registration import validate_input_folder_manifest
from app.modules.workflows.interface import WorkflowInputBatch
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage.local import LocalFileStorage
from tests.support import workflow_api as workflow_test_api
from tests.support.database import open_test_session


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


def _upload_folder(client, headers, workflow_id: int, entries):
    return client.post(
        f"/api/v1/workflows/{workflow_id}/input-folder",
        headers=headers,
        data={"relative_paths": json.dumps([f"生产批次/{name}" for name, _ in entries])},
        files=[
            ("uploads", (name, payload, "application/octet-stream"))
            for name, payload in entries
        ],
    )


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "生产批次/./A.dwg",
        "生产批次//A.dwg",
        "C:/生产批次/A.dwg",
        "生产批次/\x00A.dwg",
    ],
)
def test_input_folder_manifest_rejects_noncanonical_paths(unsafe_path):
    with pytest.raises(AppHTTPException) as raised:
        validate_input_folder_manifest(
            ["A.dwg", "parts.xlsx"],
            [unsafe_path, "生产批次/parts.xlsx"],
        )
    assert raised.value.detail["code"] == "INPUT_FOLDER_MANIFEST_INVALID"


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

    imported = _upload_folder(
        client,
        owner_headers,
        workflow_id,
        [("A.dwg", b"AC1027" + bytes(2048)), ("parts.xlsx", _xlsx())],
    )
    assert imported.status_code == 201, imported.text

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


def test_input_folder_upload_registers_one_excel_and_all_dwgs_atomically(
    monkeypatch, tmp_path
):
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client, "input-folder")
    client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch",
        headers=owner_headers,
    )

    response = client.post(
        f"/api/v1/workflows/{workflow_id}/input-folder",
        headers=owner_headers,
        data={
            "relative_paths": json.dumps(
                [
                    "生产批次/图纸/A.dwg",
                    "生产批次/图纸/B.dwg",
                    "生产批次/清单/parts.xlsx",
                ],
                ensure_ascii=False,
            )
        },
        files=[
            ("uploads", ("A.dwg", b"AC1027" + bytes(2048), "application/octet-stream")),
            ("uploads", ("B.dwg", b"AC1027" + bytes(2048), "application/octet-stream")),
            (
                "uploads",
                (
                    "parts.xlsx",
                    _xlsx(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            ),
        ],
    )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["counts"] == {
        "dwg": 2,
        "excel": 1,
        "paired": 0,
        "converting": 0,
        "failed": 0,
    }
    assert {item["original_name"] for item in data["items"]} == {
        "A.dwg",
        "B.dwg",
        "parts.xlsx",
    }
    paths = client.app.openapi()["paths"]
    assert "/api/v1/workflows/{workflow_id}/input-folder" in paths
    assert "/api/v1/workflows/{workflow_id}/input-batch/files" not in paths


def test_input_folder_rejects_any_unapproved_file_before_storage(monkeypatch, tmp_path):
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client, "input-folder-invalid")
    batch = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch",
        headers=owner_headers,
    ).json()["data"]

    response = client.post(
        f"/api/v1/workflows/{workflow_id}/input-folder",
        headers=owner_headers,
        data={
            "relative_paths": json.dumps(
                [
                    "生产批次/A.dwg",
                    "生产批次/parts.xlsx",
                    "生产批次/说明.txt",
                ],
                ensure_ascii=False,
            )
        },
        files=[
            ("uploads", ("A.dwg", b"AC1027" + bytes(2048), "application/octet-stream")),
            (
                "uploads",
                (
                    "parts.xlsx",
                    _xlsx(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            ),
            ("uploads", ("说明.txt", b"not production input", "text/plain")),
        ],
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "INPUT_FOLDER_FILE_TYPE_NOT_ALLOWED"
    detail = client.get(
        f"/api/v1/workflows/{workflow_id}/input-batch",
        headers=owner_headers,
    ).json()["data"]
    assert detail["items"] == []
    files = client.get(
        "/api/v1/files",
        headers=owner_headers,
        params={"batch_name": f"workflow-input-{batch['id']}"},
    ).json()
    assert files["pagination"]["total"] == 0


def test_workflow_download_is_one_zip_with_stage_folders(monkeypatch, tmp_path):
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client, "workflow-archive")
    client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch",
        headers=owner_headers,
    )
    imported = client.post(
        f"/api/v1/workflows/{workflow_id}/input-folder",
        headers=owner_headers,
        data={
            "relative_paths": json.dumps(
                ["生产批次/A.dwg", "生产批次/parts.xlsx"],
                ensure_ascii=False,
            )
        },
        files=[
            ("uploads", ("A.dwg", b"AC1027" + bytes(2048), "application/octet-stream")),
            (
                "uploads",
                (
                    "parts.xlsx",
                    _xlsx(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            ),
        ],
    ).json()["data"]
    with open_test_session() as db:
        workflow = workflow_service.get_workflow_or_404(db, workflow_id)
        for item in imported["items"]:
            workflow_service.attach_artifact(
                db,
                workflow,
                stage_code="source_intake",
                artifact_type=item["role"],
                file_id=item["file"]["id"],
            )
        db.commit()

    response = client.get(
        f"/api/v1/workflows/{workflow_id}/download-archive",
        headers=owner_headers,
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert ".zip" in response.headers["content-disposition"]
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert len(names) == 2
        assert all(name.startswith(f"workflow-{workflow_id}/01_source_intake/") for name in names)
        assert any(name.endswith("/source_dwg/A.dwg") for name in names)
        assert any(name.endswith("/source_excel/parts.xlsx") for name in names)

    source_file_id = imported["items"][0]["file"]["id"]
    single = client.get(
        f"/api/v1/files/{source_file_id}/download-url",
        headers=owner_headers,
    )
    assert single.status_code == 409
    assert single.json()["error"]["code"] == "WORKFLOW_ARCHIVE_DOWNLOAD_REQUIRED"
    for suffix, payload in (
        ("/download-zip/preview", {"file_ids": [source_file_id], "formats": ["dwg"]}),
        (
            "/download-zip",
            {
                "file_ids": [source_file_id],
                "formats": ["dwg"],
                "folder_name": "single-production-file",
            },
        ),
    ):
        bypass = client.post(
            f"/api/v1/files{suffix}",
            headers=owner_headers,
            json=payload,
        )
        assert bypass.status_code == 409
        assert bypass.json()["error"]["code"] == "WORKFLOW_ARCHIVE_DOWNLOAD_REQUIRED"


def test_registration_rejects_human_dxf_and_second_excel(monkeypatch, tmp_path):
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client, "input-errors")
    client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch", headers=owner_headers
    )
    duplicate = _upload_folder(
        client,
        owner_headers,
        workflow_id,
        [
            ("A.dwg", b"AC1027" + bytes(2048)),
            ("first.xlsx", _xlsx()),
            ("second.xlsx", _xlsx()),
        ],
    )
    manual_dxf = _upload_folder(
        client,
        owner_headers,
        workflow_id,
        [
            ("A.dwg", b"AC1027" + bytes(2048)),
            ("parts.xlsx", _xlsx()),
            ("manual.dxf", b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n"),
        ],
    )

    assert duplicate.status_code == 422
    assert duplicate.json()["error"]["code"] == "INPUT_FOLDER_EXCEL_COUNT_INVALID"
    assert manual_dxf.status_code == 422
    assert manual_dxf.json()["error"]["code"] == "INPUT_DXF_NOT_ALLOWED"


def test_invalid_excel_rejects_whole_folder_atomically(monkeypatch, tmp_path):
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client, "invalid-excel-ledger")
    batch = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch",
        headers=owner_headers,
    ).json()["data"]
    registered = _upload_folder(
        client,
        owner_headers,
        workflow_id,
        [
            ("protected.dwg", b"AC1027" + bytes(2048)),
            ("component-only.xlsx", _invalid_xlsx()),
        ],
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
    assert data["status"] == "uploading"
    assert data["freeze_ready"] is False
    assert data["items"] == []
    files = client.get(
        "/api/v1/files",
        headers=owner_headers,
        params={"batch_name": f"workflow-input-{batch['id']}"},
    ).json()
    assert files["pagination"]["total"] == 0


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
        "/api/v1/workflows/{workflow_id}/input-folder": {"post", "delete"},
        "/api/v1/workflows/{workflow_id}/input-batch/conversion-requests": {"post"},
        "/api/v1/workflows/{workflow_id}/input-batch/freeze": {"post"},
        "/api/v1/workflows/{workflow_id}/download-archive": {"get"},
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
            if method != "delete" and (content := success.get("content")):
                if "application/json" in content:
                    assert content["application/json"]["schema"]


def test_frozen_input_source_cannot_be_deleted_through_files_api(db, monkeypatch, tmp_path):
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client, "frozen-delete")
    batch_data = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch", headers=owner_headers
    ).json()["data"]
    imported = _upload_folder(
        client,
        owner_headers,
        workflow_id,
        [
            ("protected.dwg", b"AC1027" + bytes(2048)),
            ("parts.xlsx", _xlsx()),
        ],
    )
    assert imported.status_code == 201, imported.text
    file_id = next(
        item["file"]["id"]
        for item in imported.json()["data"]["items"]
        if item["role"] == "source_dwg"
    )
    batch = db.scalar(select(WorkflowInputBatch).where(WorkflowInputBatch.id == batch_data["id"]))
    assert batch is not None
    batch.status = "frozen"
    db.commit()

    deleted = client.delete(f"/api/v1/files/{file_id}", headers=owner_headers)

    assert deleted.status_code == 409, deleted.text
    assert deleted.json()["error"]["code"] == "FILE_REFERENCED_BY_FROZEN_INPUT"
