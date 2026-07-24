from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.bootstrap.seed import init_db
from app.main import app
from app.modules.excel_processing.stage_adapter import ExcelFinalLookupResult
from app.modules.files.interface import FileTransfer, StoredFile
from app.modules.identity.interface import User
from app.modules.jobs.interface import Job
from app.platform.config.constants import TASK_EXCEL_FINAL
from app.platform.storage.base import StorageError
from app.platform.storage.local import LocalFileStorage


@pytest.fixture(autouse=True)
def _enable_pipeline_under_test(monkeypatch: pytest.MonkeyPatch):
    """Keep this module independent from the developer/CI feature-flag default."""
    monkeypatch.setattr(
        "app.modules.excel_processing.availability.settings.excel_final_pipeline_enabled",
        True,
    )


def _create_user(db: Session, username: str) -> User:
    user = User(
        username=username,
        real_name=username,
        password_hash="test-only",
        password_algo="argon2id",
        status="active",
    )
    db.add(user)
    db.flush()
    return user


def _job(*, user_id: int, request_key: str | None) -> Job:
    return Job(
        created_by=user_id,
        task_type=TASK_EXCEL_FINAL,
        precision_level="normal",
        pipeline="excel_final",
        status="queued",
        attempt=1,
        priority=0,
        progress=0,
        params_json={"file_id": 81},
        request_key=request_key,
    )


def test_job_request_key_is_unique_per_actor_and_task(db: Session):
    user = _create_user(db, "idempotency-owner")
    db.add(_job(user_id=user.id, request_key="process:key-1"))
    db.commit()

    db.add(_job(user_id=user.id, request_key="process:key-1"))

    with pytest.raises(IntegrityError):
        db.commit()


def _admin_client(db: Session) -> tuple[TestClient, dict[str, str], User]:
    init_db()
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert response.status_code == 201, response.text
    admin = db.scalar(select(User).where(User.username == "admin"))
    assert admin is not None
    return (
        client,
        {"Authorization": f"Bearer {response.json()['data']['access_token']}"},
        admin,
    )


def _excel_file(db: Session, *, owner_id: int, suffix: str) -> StoredFile:
    stored = StoredFile(
        bucket="dwg-reports",
        storage_key=f"tests/idempotency-{suffix}.xlsx",
        original_name=f"parts-{suffix}.xlsx",
        file_ext=".xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size_bytes=32,
        sha256=(suffix.encode().hex() + "0" * 64)[:64],
        uploaded_by=owner_id,
        status="available",
    )
    db.add(stored)
    db.commit()
    return stored


def _allow_registered_file_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.modules.excel_processing.routes.processing.preflight_stored_excel",
        lambda _stored: None,
    )


def test_process_replay_returns_same_job(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    client, headers, admin = _admin_client(db)
    stored = _excel_file(db, owner_id=admin.id, suffix="replay")
    _allow_registered_file_preflight(monkeypatch)
    dispatched: list[int] = []
    monkeypatch.setattr(
        "app.modules.excel_processing.routes.processing.dispatch_committed_job",
        lambda _db, job: dispatched.append(job.id),
    )
    request_headers = {**headers, "Idempotency-Key": "process-1"}

    first = client.post(
        f"/api/v1/excel-final/process?file_id={stored.id}", headers=request_headers
    )
    second = client.post(
        f"/api/v1/excel-final/process?file_id={stored.id}", headers=request_headers
    )

    assert first.status_code == second.status_code == 202
    assert first.json()["data"]["job_id"] == second.json()["data"]["job_id"]
    assert first.json()["data"]["reused"] is False
    assert second.json()["data"]["reused"] is True
    assert dispatched == [first.json()["data"]["job_id"]]
    assert db.scalar(select(func.count()).select_from(Job)) == 1


def test_process_rejects_same_key_for_different_file(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    client, headers, admin = _admin_client(db)
    first_file = _excel_file(db, owner_id=admin.id, suffix="first")
    second_file = _excel_file(db, owner_id=admin.id, suffix="second")
    _allow_registered_file_preflight(monkeypatch)
    monkeypatch.setattr(
        "app.modules.excel_processing.routes.processing.dispatch_committed_job",
        lambda _db, _job: None,
    )
    request_headers = {**headers, "Idempotency-Key": "process-conflict"}
    first = client.post(
        f"/api/v1/excel-final/process?file_id={first_file.id}", headers=request_headers
    )

    second = client.post(
        f"/api/v1/excel-final/process?file_id={second_file.id}", headers=request_headers
    )

    assert first.status_code == 202, first.text
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def _workbook_bytes() -> bytes:
    stream = BytesIO()
    book = Workbook()
    sheet = book.active
    sheet.title = "原表"
    sheet.append(["构件编号", "零件号", "规格", "长度(mm)", "材质", "数量"])
    sheet.append(["C-1", None, "BH500*300*12*20", 1000, "Q355B", 1])
    sheet.append([None, "P-1", "L50x5", 100, "Q235B", 1])
    book.save(stream)
    book.close()
    return stream.getvalue()


def _invalid_workbook_bytes() -> bytes:
    stream = BytesIO()
    book = Workbook()
    sheet = book.active
    sheet.title = "构件汇总"
    sheet.append(["构件编号", "规格", "长度(mm)", "材质", "数量"])
    sheet.append(["C-1", "BH500*300*12*20", 1000, "Q355B", 1])
    book.save(stream)
    book.close()
    return stream.getvalue()


def _post_workbook(
    client: TestClient,
    headers: dict[str, str],
    payload: bytes,
):
    return client.post(
        "/api/v1/excel-final/upload-and-process",
        headers=headers,
        files={
            "upload": (
                "parts.xlsx",
                payload,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )


def test_upload_and_process_replay_reuses_file_and_job(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.platform.storage.factory.get_storage_backend",
        lambda: storage,
    )
    client, headers, _admin = _admin_client(db)
    dispatched: list[int] = []
    monkeypatch.setattr(
        "app.modules.excel_processing.routes.processing.dispatch_committed_job",
        lambda _db, job: dispatched.append(job.id),
    )
    request_headers = {**headers, "Idempotency-Key": "upload-1"}
    workbook = _workbook_bytes()

    first = _post_workbook(client, request_headers, workbook)
    second = _post_workbook(client, request_headers, workbook)

    assert first.status_code == second.status_code == 202
    assert first.json()["data"]["file_id"] == second.json()["data"]["file_id"]
    assert first.json()["data"]["job_id"] == second.json()["data"]["job_id"]
    assert first.json()["data"]["reused"] is False
    assert second.json()["data"]["reused"] is True
    assert dispatched == [first.json()["data"]["job_id"]]
    assert db.scalar(select(func.count()).select_from(StoredFile)) == 1
    assert db.scalar(select(func.count()).select_from(FileTransfer)) == 1
    assert db.scalar(select(func.count()).select_from(Job)) == 1
    assert storage.bucket_object_counts(["dwg-reports"])["dwg-reports"] == 1


def test_upload_and_process_rejects_invalid_table_before_file_or_job_persistence(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.platform.storage.factory.get_storage_backend",
        lambda: storage,
    )
    client, headers, _admin = _admin_client(db)

    response = _post_workbook(client, headers, _invalid_workbook_bytes())

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "EXCEL_INPUT_COMPONENT_ONLY"
    assert error["message"] == "输入只有构件汇总，没有零件明细。"
    failure = error["details"]["failure"]
    assert failure["code"] == error["code"]
    assert "包含零件号" in failure["action"]
    assert failure["contract_version"] == 1
    assert response.json()["meta"]["request_id"]
    assert db.scalar(select(func.count()).select_from(StoredFile)) == 0
    assert db.scalar(select(func.count()).select_from(FileTransfer)) == 0
    assert db.scalar(select(func.count()).select_from(Job)) == 0
    assert storage.bucket_object_counts(["dwg-reports"])["dwg-reports"] == 0


def test_upload_only_rejects_invalid_table_with_same_failure_contract(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.platform.storage.factory.get_storage_backend",
        lambda: storage,
    )
    client, headers, _admin = _admin_client(db)

    response = client.post(
        "/api/v1/excel-final/upload",
        headers=headers,
        files={
            "upload": (
                "component-only.xlsx",
                _invalid_workbook_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert response.status_code == 422, response.text
    failure = response.json()["error"]["details"]["failure"]
    assert failure["code"] == "EXCEL_INPUT_COMPONENT_ONLY"
    assert failure["action"]
    assert db.scalar(select(func.count()).select_from(StoredFile)) == 0
    assert db.scalar(select(func.count()).select_from(FileTransfer)) == 0


def test_process_rejects_registered_object_with_changed_checksum_before_job(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.platform.storage.factory.get_storage_backend",
        lambda: storage,
    )
    client, headers, admin = _admin_client(db)
    payload = _workbook_bytes()
    storage.put_fileobj(
        "dwg-reports",
        "tests/changed.xlsx",
        BytesIO(payload),
        length=len(payload),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    stored = StoredFile(
        bucket="dwg-reports",
        storage_key="tests/changed.xlsx",
        original_name="changed.xlsx",
        file_ext=".xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size_bytes=len(payload),
        sha256=hashlib.sha256(b"previous bytes").hexdigest(),
        uploaded_by=admin.id,
        status="available",
    )
    db.add(stored)
    db.commit()

    response = client.post(
        f"/api/v1/excel-final/process?file_id={stored.id}",
        headers={**headers, "Idempotency-Key": "changed-object"},
    )

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "EXCEL_INPUT_OBJECT_CHANGED"
    assert error["details"]["failure"]["code"] == error["code"]
    assert "重新上传" in error["details"]["failure"]["action"]
    assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_process_rejects_non_excel_stored_file(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    client, headers, admin = _admin_client(db)
    stored = StoredFile(
        bucket="dwg-original",
        storage_key="tests/not-an-excel.dxf",
        original_name="not-an-excel.dxf",
        file_ext=".dxf",
        content_type="application/dxf",
        size_bytes=16,
        sha256="1" * 64,
        uploaded_by=admin.id,
        status="available",
    )
    db.add(stored)
    db.commit()
    monkeypatch.setattr(
        "app.modules.excel_processing.routes.processing.dispatch_committed_job",
        lambda _db, _job: None,
    )

    response = client.post(
        f"/api/v1/excel-final/process?file_id={stored.id}",
        headers={**headers, "Idempotency-Key": "reject-dxf"},
    )

    assert response.status_code == 415, response.text
    assert response.json()["error"]["code"] == "NOT_EXCEL"
    assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_process_accepts_macro_enabled_excel_source(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    client, headers, admin = _admin_client(db)
    stored = StoredFile(
        bucket="dwg-reports",
        storage_key="tests/macro-source.xlsm",
        original_name="macro-source.xlsm",
        file_ext=".xlsm",
        content_type="application/vnd.ms-excel.sheet.macroEnabled.12",
        size_bytes=32,
        sha256="2" * 64,
        uploaded_by=admin.id,
        status="available",
    )
    db.add(stored)
    db.commit()
    _allow_registered_file_preflight(monkeypatch)
    dispatched: list[int] = []
    monkeypatch.setattr(
        "app.modules.excel_processing.routes.processing.dispatch_committed_job",
        lambda _db, job: dispatched.append(job.id),
    )

    response = client.post(
        f"/api/v1/excel-final/process?file_id={stored.id}",
        headers={**headers, "Idempotency-Key": "accept-xlsm"},
    )

    assert response.status_code == 202, response.text
    assert dispatched == [response.json()["data"]["job_id"]]


def _ready_excel_final_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stage_root = tmp_path / "excel-final-stage"
    stage_root.mkdir()
    (stage_root / "handbook.py").write_text("# health fixture\n", encoding="utf-8")
    monkeypatch.setattr(
        "app.modules.excel_processing.routes.health.get_excel_final_stage_root",
        lambda: stage_root,
    )
    monkeypatch.setattr(
        "app.modules.excel_processing.routes.health.excel_final_dependencies_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.modules.excel_processing.routes.health.handbook_database_available",
        lambda: True,
    )


def test_excel_final_health_reports_actual_database_and_storage_backends(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    client, headers, _admin = _admin_client(db)
    _ready_excel_final_dependencies(monkeypatch, tmp_path)
    storage = LocalFileStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.modules.excel_processing.routes.health.get_storage_backend",
        lambda: storage,
        raising=False,
    )

    response = client.get("/api/v1/excel-final/health", headers=headers)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["database_backend"] == "sqlite"
    assert data["database_available"] is True
    assert data["storage_backend"] == "local"
    assert data["storage_available"] is True
    assert data["storage_bucket"] == "dwg-reports"
    assert data["degraded_components"] == []
    assert data["ready"] is True


def test_excel_final_health_degrades_safely_when_storage_fails(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    class FailingHealthStorage(LocalFileStorage):
        def check_health(self) -> None:
            raise StorageError("minio-secret-host.internal")

    client, headers, _admin = _admin_client(db)
    _ready_excel_final_dependencies(monkeypatch, tmp_path)
    storage = FailingHealthStorage(tmp_path / "storage")
    monkeypatch.setattr(
        "app.modules.excel_processing.routes.health.get_storage_backend",
        lambda: storage,
        raising=False,
    )
    monkeypatch.setattr(
        "app.modules.excel_processing.routes.health.settings.storage_backend",
        "minio",
    )

    response = client.get("/api/v1/excel-final/health", headers=headers)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["storage_backend"] == "minio"
    assert data["storage_available"] is False
    assert data["degraded_components"] == ["object_storage"]
    assert data["ready"] is False
    assert "minio-secret-host.internal" not in response.text


def test_weight_lookup_requires_category(db: Session):
    client, headers, _admin = _admin_client(db)

    response = client.get(
        "/api/v1/excel-final/weights/lookup",
        params={"spec": "6*30"},
        headers=headers,
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("category", "spec", "expected"),
    [
        ("steel_pipe", "PIP219*8", 41.62608),
        ("square_tube", "PD100*4", 9.46944),
    ],
)
def test_weight_lookup_uses_pip_pd_formula_before_handbook(
    db: Session,
    category: str,
    spec: str,
    expected: float,
):
    client, headers, _admin = _admin_client(db)

    response = client.get(
        "/api/v1/excel-final/weights/lookup",
        params={
            "category": category,
            "spec": spec,
            "material": "Q355B",
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "hit"
    assert data["weight_kg_per_m"] == pytest.approx(expected)
    assert data["source"] == "circular_hollow_formula:0.02466"


@pytest.mark.parametrize(
    "params",
    [
        {"category": "round_bar", "spec": "D24"},
        {"category": "round_bar", "spec": "D24", "material": "HRB400"},
        {"category": "rebar", "spec": "D24", "material": "Q355B"},
        {"category": "flat_steel", "spec": "D24", "material": "Q355B"},
    ],
)
def test_weight_lookup_rejects_d_series_material_category_conflicts(
    db: Session,
    params: dict[str, str],
):
    client, headers, _admin = _admin_client(db)

    response = client.get(
        "/api/v1/excel-final/weights/lookup",
        params=params,
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_HANDBOOK_LOOKUP"


def test_weight_lookup_exposes_category_aware_result(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    client, headers, _admin = _admin_client(db)
    captured: dict[str, object] = {}

    def fake_lookup(**kwargs):
        captured.update(kwargs)
        return ExcelFinalLookupResult(
            protocol_version=1,
            category="round_bar",
            normalized_spec="24",
            material="Q355B",
            weight_kg_per_m=3.55,
            source="round_square_bar:round_bar",
            status="hit",
        )

    monkeypatch.setattr(
        "app.modules.excel_processing.routes.tools.lookup_excel_final_weight",
        fake_lookup,
    )

    response = client.get(
        "/api/v1/excel-final/weights/lookup",
        params={"category": "round_bar", "spec": "D24", "material": "Q355B"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert captured == {"category": "round_bar", "spec": "D24", "material": "Q355B"}
    assert response.json()["data"] == {
        "category": "round_bar",
        "spec": "D24",
        "normalized_spec": "24",
        "material": "Q355B",
        "weight_kg_per_m": 3.55,
        "source": "round_square_bar:round_bar",
        "status": "hit",
    }
