"""DXF 管线测试（spec §14, Stage 3）—— 全程 mock dwg_converter，不真跑 ODA。

覆盖: pipeline 选择、特性开关 gate、转换成功/失败、源文件缺失、SSE 事件流。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.constants import (
    JOB_FAILED,
    JOB_QUEUED,
    JOB_SUCCEEDED,
    PIPELINE_DXF,
    TASK_DWG_TO_DXF,
)
from app.db.init_db import init_db
from app.main import app


@pytest.fixture(autouse=True)
def _enable_dxf_pipeline(monkeypatch):
    """所有 DXF 测试默认开启管线开关。"""
    monkeypatch.setattr(settings, "dxf_pipeline_enabled", True)


# ── pipeline routing ───────────────────────────────────────────────────────────


def test_create_dxf_job_gets_dxf_pipeline():
    """POST /jobs task_type=convert_dwg_to_dxf → pipeline=dxf_open_source。"""
    init_db()
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert login.status_code == 201
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    resp = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={"task_type": TASK_DWG_TO_DXF, "precision_level": "normal", "params": {"file_id": 1}},
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()["data"]
    assert data["pipeline"] == PIPELINE_DXF
    assert data["status"] == JOB_QUEUED


def test_framework_smoke_job_still_gets_stub_pipeline():
    """兼容：非 DXF 任务保持 local_stub pipeline。"""
    init_db()
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert login.status_code == 201
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    resp = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={"task_type": "framework_smoke_test", "precision_level": "normal"},
    )
    assert resp.status_code == 202
    assert resp.json()["data"]["pipeline"] == "local_stub"


# ── feature gate ───────────────────────────────────────────────────────────────


def test_dxf_pipeline_disabled_returns_503(monkeypatch):
    """DXF_PIPELINE_ENABLED=false → POST convert_dwg_to_dxf → 503。"""
    monkeypatch.setattr(settings, "dxf_pipeline_enabled", False)
    init_db()
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert login.status_code == 201
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    resp = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={"task_type": TASK_DWG_TO_DXF, "precision_level": "normal"},
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "DXF_PIPELINE_DISABLED"


# ── conversion — success ──────────────────────────────────────────────────────


def _make_fake_convert_result(*, success: bool, target_exists: bool = True):
    """构造假的 ConvertResult（不依赖 dwg_converter 导入）。"""
    from unittest.mock import MagicMock

    fake = MagicMock()
    fake.success = success
    fake.duration = 1.234
    fake.error = None if success else "ODA 转换失败"
    fake.to_dict.return_value = {
        "source": "/tmp/fake/test.dwg",
        "target": "/tmp/fake_output/test.dxf",
        "success": success,
        "duration": 1.234,
        "error": fake.error,
    }
    if target_exists:
        fake_target = MagicMock()
        fake_target.is_file.return_value = True
        fake_target.read_bytes.return_value = b"FAKE_DXF_CONTENT"
        fake.target = fake_target
    else:
        fake_target = MagicMock()
        fake_target.is_file.return_value = False
        fake.target = fake_target
    return fake


def _admin_headers(client: TestClient) -> dict[str, str]:
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert login.status_code == 201
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def _upload_dwg(client: TestClient, headers: dict, name: str = "test.dwg") -> int:
    """通过 API 上传一个合法的 DWG 文件，返回 file_id。"""
    dwg_bytes = b"AC1027\x00" + b"\x00" * 1024
    resp = client.post(
        "/api/v1/files",
        headers=headers,
        files={"upload": (name, dwg_bytes, "application/acad")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _create_project(client: TestClient, headers: dict, code: str, name: str = "") -> int:
    """创建项目并返回 project_id。"""
    resp = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"code": code, "name": name or code},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def test_run_dxf_conversion_success():
    """Mock ODA 成功 → job succeeded, 3 条 JobStep, AnalysisResult + DXF 文件。"""
    init_db()
    client = TestClient(app)
    headers = _admin_headers(client)

    pid = _create_project(client, headers, "DXF-SUCCESS")
    file_id = _upload_dwg(client, headers)

    fake = _make_fake_convert_result(success=True)
    with patch("dwg_converter.convert_file", return_value=fake):
        resp = client.post(
            "/api/v1/jobs",
            headers=headers,
            json={
                "project_id": pid,
                "task_type": TASK_DWG_TO_DXF,
                "precision_level": "normal",
                "params": {"file_id": file_id},
            },
        )
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["data"]["id"]
        assert resp.json()["data"]["pipeline"] == PIPELINE_DXF

    # 通过 API 验证 DB 状态（避免 SessionLocal 引擎隔离问题）
    jobv = client.get(f"/api/v1/jobs/{job_id}", headers=headers)
    assert jobv.status_code == 200, jobv.text
    jd = jobv.json()["data"]
    assert jd["status"] == JOB_SUCCEEDED, f"status={jd['status']} error={jd.get('error_message')}"
    assert jd["progress"] == 100

    stepsv = client.get(f"/api/v1/jobs/{job_id}/steps", headers=headers)
    assert stepsv.status_code == 200
    steps_data = stepsv.json()["data"]
    step_names = [s["step_name"] for s in steps_data]
    assert "download_source_dwg" in step_names
    assert "run_oda_convert" in step_names
    assert "persist_dxf_result" in step_names
    assert all(s["status"] == "succeeded" for s in steps_data), step_names

    resultsv = client.get(f"/api/v1/jobs/{job_id}/results", headers=headers)
    assert resultsv.status_code == 200
    results = resultsv.json()["data"]
    assert len(results) >= 1
    res = results[0]
    assert res["result_type"] == TASK_DWG_TO_DXF
    assert res["result_file_id"] is not None

    # 验证 DXF 下载 URL 可获取
    dlv = client.get(f"/api/v1/results/{res['id']}/download-url", headers=headers)
    assert dlv.status_code == 200
    url = dlv.json()["data"]["url"]
    assert "/download?" in url


# ── conversion — failure ──────────────────────────────────────────────────────


def test_run_dxf_conversion_oda_failure():
    """ODA 返回 success=False → job failed, error_code=DXF_CONVERSION_FAILED。"""
    init_db()
    client = TestClient(app)
    headers = _admin_headers(client)

    pid = _create_project(client, headers, "DXF-FAIL")
    file_id = _upload_dwg(client, headers)

    fake = _make_fake_convert_result(success=False)
    with patch("dwg_converter.convert_file", return_value=fake):
        resp = client.post(
            "/api/v1/jobs",
            headers=headers,
            json={
                "project_id": pid,
                "task_type": TASK_DWG_TO_DXF,
                "precision_level": "normal",
                "params": {"file_id": file_id},
            },
        )
        assert resp.status_code == 202

    job_id = resp.json()["data"]["id"]
    jobv = client.get(f"/api/v1/jobs/{job_id}", headers=headers)
    assert jobv.status_code == 200
    jd = jobv.json()["data"]
    assert jd["status"] == JOB_FAILED, f"status={jd['status']}"
    assert jd["error_code"] == "DXF_CONVERSION_FAILED"


def test_run_dxf_conversion_source_missing():
    """源文件不存在 → job failed, error_code=DXF_SOURCE_FILE_MISSING。"""
    init_db()
    client = TestClient(app)
    headers = _admin_headers(client)

    pid = _create_project(client, headers, "DXF-MISSING")

    resp = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={
            "project_id": pid,
            "task_type": TASK_DWG_TO_DXF,
            "precision_level": "normal",
            "params": {"file_id": 99999},
        },
    )
    assert resp.status_code == 202

    job_id = resp.json()["data"]["id"]
    jobv = client.get(f"/api/v1/jobs/{job_id}", headers=headers)
    assert jobv.status_code == 200
    jd = jobv.json()["data"]
    assert jd["status"] == JOB_FAILED
    assert jd["error_code"] == "DXF_SOURCE_FILE_MISSING"


# ── job events ────────────────────────────────────────────────────────────────


def test_job_events_sse_endpoint_headers():
    """SSE 端点返回 200 + text/event-stream Content-Type。"""
    init_db()
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert resp.status_code == 201
    headers = {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}

    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"code": "SSE-TEST", "name": "SSE Test"},
    )
    assert project.status_code == 201
    pid = project.json()["data"]["id"]

    job_resp = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={"project_id": pid, "task_type": "framework_smoke_test", "precision_level": "normal"},
    )
    assert job_resp.status_code == 202
    job_id = job_resp.json()["data"]["id"]

    # SSE 验证 headers（不流式读取 body — TestClient 会缓冲到生成器耗尽）
    # 用 raise_server_exceptions=False 避免 StreamingResponse 内异常导致 500
    client_no_raise = TestClient(app, raise_server_exceptions=False)
    resp = client_no_raise.get(f"/api/v1/jobs/{job_id}/events", headers=headers)
    assert resp.status_code == 200, f"got {resp.status_code}: {resp.text[:200]}"
    assert "text/event-stream" in resp.headers.get("content-type", "")
    # 响应体以 data: 开头（snapshot 帧）
    assert resp.text.strip().startswith("data: "), f"body={resp.text[:300]}"
