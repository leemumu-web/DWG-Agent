from __future__ import annotations

import json
import zipfile
from io import BytesIO

import openpyxl
import pytest
from sqlalchemy import select

from app.modules.files.interface import save_bytes_as_file
from app.modules.jobs.interface import JobDispatch
from app.modules.workflows import interface as workflow_service
from app.modules.workflows.intake.registration import (
    validate_input_dwg_folder_manifest,
    validate_input_excel_name,
)
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


def _upload_excel(client, headers, workflow_id: int, name: str, payload: bytes):
    return client.post(
        f"/api/v1/workflows/{workflow_id}/input-excel",
        headers=headers,
        files={"upload": (name, payload, "application/octet-stream")},
    )


def _upload_dwg_folder(client, headers, workflow_id: int, entries):
    return client.post(
        f"/api/v1/workflows/{workflow_id}/input-dwg-folder",
        headers=headers,
        data={"relative_paths": json.dumps([f"生产图纸/{name}" for name, _ in entries])},
        files=[
            ("uploads", (name, payload, "application/octet-stream"))
            for name, payload in entries
        ],
    )


def _upload_inputs(client, headers, workflow_id: int, entries):
    excel_entries = [
        (name, payload) for name, payload in entries if name.lower().endswith((".xls", ".xlsx"))
    ]
    dwg_entries = [
        (name, payload) for name, payload in entries if name.lower().endswith(".dwg")
    ]
    assert len(excel_entries) == 1
    excel = _upload_excel(client, headers, workflow_id, *excel_entries[0])
    if excel.status_code != 201:
        return excel
    return _upload_dwg_folder(client, headers, workflow_id, dwg_entries)


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "生产批次/./A.dwg",
        "生产批次//A.dwg",
        "C:/生产批次/A.dwg",
        "C:生产批次/A.dwg",
        "生产批次/\x00A.dwg",
    ],
)
def test_input_folder_manifest_rejects_noncanonical_paths(unsafe_path):
    with pytest.raises(AppHTTPException) as raised:
        validate_input_dwg_folder_manifest(
            ["A.dwg", "B.dwg"],
            [unsafe_path, "生产批次/B.dwg"],
        )
    assert raised.value.detail["code"] == "INPUT_FOLDER_MANIFEST_INVALID"


def test_input_folder_manifest_accepts_regular_unicode_engineering_names():
    folder = validate_input_dwg_folder_manifest(
        ["BH（腹板）Ⅰ.dwg", "BOX１２.dwg"],
        ["生产图纸（一期）/BH（腹板）Ⅰ.dwg", "生产图纸（一期）/BOX１２.dwg"],
    )

    assert folder == "生产图纸（一期）"


def test_input_folder_manifest_accepts_5000_dwg_files():
    names = [f"drawing-{index:04d}.dwg" for index in range(5000)]

    folder = validate_input_dwg_folder_manifest(
        names,
        [f"生产图纸/{name}" for name in names],
    )

    assert folder == "生产图纸"


def test_input_folder_route_accepts_more_than_framework_default_file_parts(monkeypatch, tmp_path):
    """The endpoint must override Starlette's default 1000 multipart-file limit."""
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client, "multipart-5000")
    created = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch",
        headers=owner_headers,
    )
    assert created.status_code == 201, created.text

    response = _upload_dwg_folder(
        client,
        owner_headers,
        workflow_id,
        [
            (f"D{index:04d}.dwg", b"AC1027" + bytes(2048))
            for index in range(1001)
        ],
    )

    assert response.status_code == 201, response.text
    assert response.json()["data"]["counts"]["dwg"] == 1001


def test_input_batch_items_are_server_paginated(monkeypatch, tmp_path):
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client, "input-pagination")
    created = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch",
        headers=owner_headers,
    )
    assert created.status_code == 201, created.text
    uploaded = _upload_dwg_folder(
        client,
        owner_headers,
        workflow_id,
        [
            (f"D{index:03d}.dwg", b"AC1027" + bytes(2048))
            for index in range(61)
        ],
    )
    assert uploaded.status_code == 201, uploaded.text

    first = client.get(
        f"/api/v1/workflows/{workflow_id}/input-batch",
        headers=owner_headers,
        params={"item_page": 1, "item_page_size": 25},
    )
    second = client.get(
        f"/api/v1/workflows/{workflow_id}/input-batch",
        headers=owner_headers,
        params={"item_page": 3, "item_page_size": 25},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_data = first.json()["data"]
    second_data = second.json()["data"]
    assert first_data["item_total"] == 61
    assert first_data["item_page"] == 1
    assert first_data["item_page_size"] == 25
    assert len(first_data["items"]) == 25
    assert second_data["item_page"] == 3
    assert len(second_data["items"]) == 11
    assert {item["id"] for item in first_data["items"]}.isdisjoint(
        item["id"] for item in second_data["items"]
    )


def test_input_folder_route_reports_domain_limit_for_a_large_path_manifest(monkeypatch, tmp_path):
    """Long paths for 5000 drawings must not hit Starlette's 1 MiB field default."""
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client, "multipart-manifest")
    created = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch",
        headers=owner_headers,
    )
    assert created.status_code == 201, created.text

    names = [f"D{index:04d}.dwg" for index in range(5001)]
    response = client.post(
        f"/api/v1/workflows/{workflow_id}/input-dwg-folder",
        headers=owner_headers,
        data={
            "relative_paths": json.dumps(
                [f"生产图纸/{'a' * 240}/{name}" for name in names],
                ensure_ascii=False,
            )
        },
        files=[
            ("uploads", (name, b"AC1027" + bytes(8), "application/octet-stream"))
            for name in names
        ],
    )

    assert response.status_code == 413, response.text
    detail = response.json()["error"]
    assert detail["code"] == "INPUT_FOLDER_TOO_MANY_FILES"
    assert detail["details"]["selected_files"] == 5001


def test_input_folder_manifest_rejects_more_than_5000_files():
    names = [f"drawing-{index:04d}.dwg" for index in range(5001)]

    with pytest.raises(AppHTTPException) as raised:
        validate_input_dwg_folder_manifest(
            names,
            [f"生产图纸/{name}" for name in names],
        )

    assert raised.value.detail["code"] == "INPUT_FOLDER_TOO_MANY_FILES"
    assert raised.value.detail["details"]["maximum_files"] == 5000


@pytest.mark.parametrize("name", ["parts.xls", "parts.xlsx", "PARTS.XLSX"])
def test_excel_upload_name_accepts_supported_extensions(name):
    validate_input_excel_name(name)


@pytest.mark.parametrize("name", ["parts.csv", "parts.xlsm", "drawing.dwg", ""])
def test_excel_upload_name_rejects_other_extensions(name):
    with pytest.raises(AppHTTPException) as raised:
        validate_input_excel_name(name)
    assert raised.value.detail["code"] == "INPUT_EXCEL_FILE_TYPE_NOT_ALLOWED"


def test_dwg_folder_manifest_rejects_non_dwg_upload():
    with pytest.raises(AppHTTPException) as raised:
        validate_input_dwg_folder_manifest(
            ["A.dwg", "notes.pdf"],
            ["图纸/A.dwg", "图纸/notes.pdf"],
        )
    assert raised.value.detail["code"] == "INPUT_DWG_FOLDER_FILE_TYPE_NOT_ALLOWED"


def test_create_register_list_and_prepare_conversion(monkeypatch, tmp_path):
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client)
    monkeypatch.setattr("app.platform.config.settings.settings.dxf_pipeline_enabled", True)

    created = client.post(f"/api/v1/workflows/{workflow_id}/input-batch", headers=owner_headers)
    replay = client.post(f"/api/v1/workflows/{workflow_id}/input-batch", headers=owner_headers)
    assert created.status_code == 201, created.text
    assert replay.status_code == 200, replay.text
    batch_id = created.json()["data"]["id"]
    assert replay.json()["data"]["id"] == batch_id

    imported = _upload_inputs(
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
    job_id = conversion.json()["data"]["jobs"][0]["id"]
    with open_test_session() as db:
        dispatch = db.scalar(select(JobDispatch).where(JobDispatch.job_id == job_id))
        assert dispatch is not None
        assert dispatch.task_type == "convert_dwg_to_dxf"
        assert dispatch.dispatch_mode == "conversion_batch"


def test_conversion_rejects_dwg_only_input(monkeypatch, tmp_path):
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client, "dwg-only")
    monkeypatch.setattr("app.platform.config.settings.settings.dxf_pipeline_enabled", True)
    client.post(f"/api/v1/workflows/{workflow_id}/input-batch", headers=owner_headers)
    imported = _upload_dwg_folder(
        client,
        owner_headers,
        workflow_id,
        [("A.dwg", b"AC1027" + bytes(2048))],
    )
    assert imported.status_code == 201, imported.text

    conversion = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch/conversion-requests",
        headers=owner_headers,
    )

    assert conversion.status_code == 409
    assert conversion.json()["error"]["code"] == "INPUT_EXCEL_REQUIRED"


def test_separate_uploads_register_one_excel_and_all_dwgs(
    monkeypatch, tmp_path
):
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client, "input-folder")
    client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch",
        headers=owner_headers,
    )

    excel_response = _upload_excel(
        client,
        owner_headers,
        workflow_id,
        "parts.xlsx",
        _xlsx(),
    )
    assert excel_response.status_code == 201, excel_response.text
    response = client.post(
        f"/api/v1/workflows/{workflow_id}/input-dwg-folder",
        headers=owner_headers,
        data={
            "relative_paths": json.dumps(
                [
                    "生产批次/图纸/A.dwg",
                    "生产批次/图纸/B.dwg",
                ],
                ensure_ascii=False,
            )
        },
        files=[
            ("uploads", ("A.dwg", b"AC1027" + bytes(2048), "application/octet-stream")),
            ("uploads", ("B.dwg", b"AC1027" + bytes(2048), "application/octet-stream")),
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
    assert "/api/v1/workflows/{workflow_id}/input-excel" in paths
    assert "/api/v1/workflows/{workflow_id}/input-dwg-folder" in paths
    assert "/api/v1/workflows/{workflow_id}/input-folder" in paths
    assert "post" not in paths["/api/v1/workflows/{workflow_id}/input-folder"]
    assert "/api/v1/workflows/{workflow_id}/input-batch/files" not in paths


def test_dwg_folder_rejects_any_unapproved_file_before_storage(monkeypatch, tmp_path, caplog):
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client, "input-folder-invalid")
    batch = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch",
        headers=owner_headers,
    ).json()["data"]

    response = client.post(
        f"/api/v1/workflows/{workflow_id}/input-dwg-folder",
        headers={**owner_headers, "X-Request-ID": "folder-invalid-request"},
        data={
            "relative_paths": json.dumps(
                [
                    "生产批次/A.dwg",
                    "生产批次/说明.txt",
                ],
                ensure_ascii=False,
            )
        },
        files=[
            ("uploads", ("A.dwg", b"AC1027" + bytes(2048), "application/octet-stream")),
            ("uploads", ("说明.txt", b"not production input", "text/plain")),
        ],
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "INPUT_DWG_FOLDER_FILE_TYPE_NOT_ALLOWED"
    assert any(
        "request_id=folder-invalid-request" in record.message
        and "code=INPUT_DWG_FOLDER_FILE_TYPE_NOT_ALLOWED" in record.message
        for record in caplog.records
    )
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


def test_dwg_folder_persists_30_files_in_one_request(monkeypatch, tmp_path):
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client, "input-folder-thirty")
    client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch",
        headers=owner_headers,
    )
    entries = [
        (f"drawing-{index:02d}.dwg", b"AC1027" + bytes(2048))
        for index in range(30)
    ]

    response = _upload_dwg_folder(
        client,
        owner_headers,
        workflow_id,
        entries,
    )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["counts"]["dwg"] == 30
    source_items = [item for item in data["items"] if item["role"] == "source_dwg"]
    assert len(source_items) == 30
    assert {item["original_name"] for item in source_items} == {
        name for name, _payload in entries
    }


def test_cleared_input_folder_can_restore_exact_registered_sources(monkeypatch, tmp_path):
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client, "input-folder-restore")
    client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch",
        headers=owner_headers,
    )
    imported = _upload_inputs(
        client,
        owner_headers,
        workflow_id,
        [
            ("A.dwg", b"AC1027" + bytes(2048)),
            ("B.dwg", b"AC1027" + bytes(2048)),
            ("parts.xlsx", _xlsx()),
        ],
    )
    assert imported.status_code == 201, imported.text
    imported_ids = {item["file"]["id"] for item in imported.json()["data"]["items"]}

    cleared = client.delete(
        f"/api/v1/workflows/{workflow_id}/input-folder",
        headers=owner_headers,
    )
    empty = client.get(
        f"/api/v1/workflows/{workflow_id}/input-batch",
        headers=owner_headers,
    )

    assert cleared.status_code == 204, cleared.text
    assert empty.json()["data"]["items"] == []
    assert empty.json()["data"]["recoverable_file_count"] == 3

    restored = client.post(
        f"/api/v1/workflows/{workflow_id}/input-folder/restore",
        headers=owner_headers,
    )

    assert restored.status_code == 200, restored.text
    data = restored.json()["data"]
    assert data["counts"]["dwg"] == 2
    assert data["counts"]["excel"] == 1
    assert data["recoverable_file_count"] == 0
    assert {item["file"]["id"] for item in data["items"]} == imported_ids

    duplicate = client.post(
        f"/api/v1/workflows/{workflow_id}/input-folder/restore",
        headers=owner_headers,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "INPUT_RESTORE_NOT_AVAILABLE"


def test_workflow_download_is_one_zip_with_stage_folders(monkeypatch, tmp_path):
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client, "workflow-archive")
    client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch",
        headers=owner_headers,
    )
    imported_response = _upload_inputs(
        client,
        owner_headers,
        workflow_id,
        [("A.dwg", b"AC1027" + bytes(2048)), ("parts.xlsx", _xlsx())],
    )
    assert imported_response.status_code == 201, imported_response.text
    imported = imported_response.json()["data"]
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

    standalone = client.post(
        "/api/v1/files",
        headers={**owner_headers, "Idempotency-Key": "standalone-conversion-source"},
        params={"batch_name": "standalone-conversion"},
        files={"upload": ("standalone.dwg", b"AC1027" + bytes(2048), "application/octet-stream")},
    )
    assert standalone.status_code == 201, standalone.text
    standalone_id = standalone.json()["data"]["id"]

    standalone_files = client.get(
        "/api/v1/files?standalone_only=true&page_size=200",
        headers=owner_headers,
    )
    assert standalone_files.status_code == 200, standalone_files.text
    visible_ids = {item["id"] for item in standalone_files.json()["data"]}
    assert standalone_id in visible_ids
    assert source_file_id not in visible_ids

    standalone_batches = client.get(
        "/api/v1/files/batches?standalone_only=true",
        headers=owner_headers,
    )
    assert standalone_batches.status_code == 200, standalone_batches.text
    visible_batches = {item["name"] for item in standalone_batches.json()["data"]}
    assert "standalone-conversion" in visible_batches
    production_batch_name = imported["items"][0]["file"]["batch_name"]
    assert production_batch_name not in visible_batches


def test_workflow_stage_download_is_one_zip_with_only_stage_artifacts(
    monkeypatch, tmp_path
):
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    admin_headers, owner_headers, project_id, workflow_id = _setup(
        client, "workflow-stage-archive"
    )
    source_id = _upload(
        client,
        owner_headers,
        "source.dwg",
        b"AC1027" + bytes(2048),
        workflow_id,
    )
    classified_id = _upload(
        client,
        owner_headers,
        "member_001_pre_split.dxf",
        b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n",
        workflow_id,
    )
    with open_test_session() as db:
        workflow = workflow_service.get_workflow_or_404(db, workflow_id)
        report = save_bytes_as_file(
            db,
            bucket="workflow-results",
            storage_key=f"workflows/{workflow_id}/classification-report.json",
            original_name="classification-report.json",
            file_ext=".json",
            content_type="application/json",
            payload=b'{"classified": 1}',
            uploaded_by=workflow.created_by,
            batch_name=f"workflow-{workflow_id}",
        )
        manifest = save_bytes_as_file(
            db,
            bucket="workflow-results",
            storage_key=f"workflows/{workflow_id}/classification-manifest.json",
            original_name="classification-manifest.json",
            file_ext=".json",
            content_type="application/json",
            payload=b'{"files": ["member_001_pre_split.dxf"]}',
            uploaded_by=workflow.created_by,
            batch_name=f"workflow-{workflow_id}",
        )
        workflow_service.attach_artifact(
            db,
            workflow,
            stage_code="source_intake",
            artifact_type="source_dwg",
            file_id=source_id,
        )
        for artifact_type, file_id in (
            ("classified_dxf", classified_id),
            ("classification_report", report.id),
            ("classification_manifest", manifest.id),
        ):
            workflow_service.attach_artifact(
                db,
                workflow,
                stage_code="dxf_classification",
                artifact_type=artifact_type,
                file_id=file_id,
            )
        db.commit()

    response = client.get(
        f"/api/v1/workflows/{workflow_id}/stages/dxf_classification/download-archive",
        headers=owner_headers,
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert "workflow-" in response.headers["content-disposition"]
    assert "02_dxf_classification" in response.headers["content-disposition"]
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert len(names) == 3
        assert all(
            name.startswith(f"workflow-{workflow_id}/02_dxf_classification/")
            for name in names
        )
        assert any("/classified_dxf/" in name for name in names)
        assert any("/classification_report/" in name for name in names)
        assert any("/classification_manifest/" in name for name in names)
        assert not any("/source_intake/" in name for name in names)
        assert not any(name.lower().endswith(".dwg") for name in names)

    member_id, member_headers = workflow_test_api.create_engineer_user(
        client,
        admin_headers,
        "workflow-stage-archive-member",
    )
    workflow_test_api.add_project_member(
        client,
        project_id,
        member_id,
        "project_engineer",
        admin_headers,
    )
    member_response = client.get(
        f"/api/v1/workflows/{workflow_id}/stages/dxf_classification/download-archive",
        headers=member_headers,
    )
    assert member_response.status_code == 200, member_response.text

    empty = client.get(
        f"/api/v1/workflows/{workflow_id}/stages/drawing_processing/download-archive",
        headers=owner_headers,
    )
    assert empty.status_code == 409
    assert empty.json()["error"]["code"] == "WORKFLOW_STAGE_ARCHIVE_EMPTY"

    unknown = client.get(
        f"/api/v1/workflows/{workflow_id}/stages/not-a-stage/download-archive",
        headers=owner_headers,
    )
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "WORKFLOW_STAGE_UNKNOWN"

    _, stranger_headers = workflow_test_api.create_engineer_user(
        client,
        admin_headers,
        "workflow-stage-archive-stranger",
    )
    forbidden = client.get(
        f"/api/v1/workflows/{workflow_id}/stages/dxf_classification/download-archive",
        headers=stranger_headers,
    )
    assert forbidden.status_code == 403


def test_registration_rejects_human_dxf_and_second_excel(monkeypatch, tmp_path):
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client, "input-errors")
    client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch", headers=owner_headers
    )
    first_excel = _upload_excel(
        client,
        owner_headers,
        workflow_id,
        "first.xlsx",
        _xlsx(),
    )
    duplicate = _upload_excel(
        client,
        owner_headers,
        workflow_id,
        "second.xlsx",
        _xlsx(),
    )
    manual_dxf = _upload_dwg_folder(
        client,
        owner_headers,
        workflow_id,
        [
            ("A.dwg", b"AC1027" + bytes(2048)),
            ("manual.dxf", b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n"),
        ],
    )

    assert first_excel.status_code == 201, first_excel.text
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "INPUT_EXCEL_ALREADY_IMPORTED"
    assert manual_dxf.status_code == 422
    assert manual_dxf.json()["error"]["code"] == "INPUT_DWG_FOLDER_FILE_TYPE_NOT_ALLOWED"


def test_invalid_excel_is_rejected_without_partial_input(monkeypatch, tmp_path):
    _use_storage(monkeypatch, tmp_path)
    client = workflow_test_api.client()
    _, owner_headers, _, workflow_id = _setup(client, "invalid-excel-ledger")
    batch = client.post(
        f"/api/v1/workflows/{workflow_id}/input-batch",
        headers=owner_headers,
    ).json()["data"]
    registered = _upload_excel(
        client,
        owner_headers,
        workflow_id,
        "component-only.xlsx",
        _invalid_xlsx(),
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
        "/api/v1/workflows/{workflow_id}/input-excel": {"post"},
        "/api/v1/workflows/{workflow_id}/input-dwg-folder": {"post"},
        "/api/v1/workflows/{workflow_id}/input-folder": {"delete"},
        "/api/v1/workflows/{workflow_id}/input-folder/restore": {"post"},
        "/api/v1/workflows/{workflow_id}/input-batch/conversion-requests": {"post"},
        "/api/v1/workflows/{workflow_id}/input-batch/freeze": {"post"},
        "/api/v1/workflows/{workflow_id}/download-archive": {"get"},
        "/api/v1/workflows/{workflow_id}/stages/{stage_code}/download-archive": {"get"},
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
    imported = _upload_inputs(
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
