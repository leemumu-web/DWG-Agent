"""DXF→DWG 管线测试（spec §14, Stage 3）—— 全程 mock dxf_converter，不真跑 ODA。

覆盖：
- pipeline 选择 + 特性开关 gate
- 转换成功/失败/源缺失
- 版本解析优先级：AnalysisResult 反查 > $ACADVER 头解析 > 配置默认
- _KNOWN_ODA_VERSIONS 防护：损坏的 tool_version 不传入 ODA
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.bootstrap.seed import init_db
from app.main import app
from app.platform.config.constants import (
    JOB_FAILED,
    JOB_SUCCEEDED,
    PIPELINE_DXF2DWG,
    TASK_DWG_TO_DXF,
    TASK_DXF_TO_DWG,
)
from app.platform.config.settings import settings


@pytest.fixture(autouse=True)
def _enable_dxf2dwg_pipeline(monkeypatch):
    """所有测试默认开启 DXF→DWG 管线开关。"""
    monkeypatch.setattr(settings, "dxf2dwg_pipeline_enabled", True)


# ── helpers ────────────────────────────────────────────────────────────────────


def _make_fake_convert_result(*, success: bool, target_exists: bool = True):
    """构造假的 ConvertResult（不依赖 dxf_converter 导入）。"""
    from unittest.mock import MagicMock

    fake = MagicMock()
    fake.success = success
    fake.duration = 1.234
    fake.error = None if success else "ODA 转换失败"
    fake.to_dict.return_value = {
        "source": "/tmp/fake/test.dxf",
        "target": "/tmp/fake_output/test.dwg",
        "success": success,
        "duration": 1.234,
        "error": fake.error,
    }
    if target_exists:
        fake_target = MagicMock()
        fake_target.is_file.return_value = True
        fake_target.read_bytes.return_value = b"FAKE_DWG_CONTENT"
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


def _create_project(client: TestClient, headers: dict, code: str) -> int:
    resp = client.post(
        "/api/v1/projects", headers=headers, json={"code": code, "name": code},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _upload_dxf(client: TestClient, headers: dict, name: str = "test.dxf", content: bytes | None = None) -> int:
    """上传一个 DXF 文件，返回 file_id。

    DXF 是文本格式，可直接造一个最小合法 DXF（含 $ACADVER 头）。
    """
    if content is None:
        # 默认带 $ACADVER=AC1015（AutoCAD 2000）的最小 DXF
        content = _minimal_dxf(acadver="AC1015")
    resp = client.post(
        "/api/v1/files",
        headers=headers,
        files={"upload": (name, content, "application/dxf")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _minimal_dxf(acadver: str = "AC1015") -> bytes:
    """构造一个带 HEADER+ENTITIES 的最小 DXF 文本。"""
    return (
        "  0\nSECTION\n  2\nHEADER\n  9\n$ACADVER\n  1\n"
        f"{acadver}\n  0\nENDSEC\n  0\nSECTION\n  2\nENTITIES\n"
        "  0\nLINE\n  8\n0\n 10\n0.0\n 20\n0.0\n 30\n0.0\n 11\n1.0\n 21\n1.0\n 31\n0.0\n"
        "  0\nENDSEC\n  0\nEOF\n"
    ).encode()


def _create_dxf2dwg_job(client: TestClient, headers: dict, project_id: int, file_id: int) -> tuple[int, dict]:
    resp = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={
            "project_id": project_id,
            "task_type": TASK_DXF_TO_DWG,
            "precision_level": "normal",
            "params": {"file_id": file_id},
        },
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()["data"]
    return data["id"], data


# ── pipeline routing + gate ────────────────────────────────────────────────────


def test_create_dxf2dwg_job_gets_dxf2dwg_pipeline():
    """POST /jobs task_type=convert_dxf_to_dwg → pipeline=dxf2dwg_open_source。"""
    init_db()
    client = TestClient(app)
    headers = _admin_headers(client)
    pid = _create_project(client, headers, "D2D-ROUTE")
    fid = _upload_dxf(client, headers)

    fake = _make_fake_convert_result(success=True)
    with patch("dxf_converter.convert_file", return_value=fake):
        job_id, data = _create_dxf2dwg_job(client, headers, pid, fid)
    assert data["pipeline"] == PIPELINE_DXF2DWG


def test_dxf2dwg_pipeline_disabled_returns_503(monkeypatch):
    """特性开关关闭 → 503。"""
    monkeypatch.setattr(settings, "dxf2dwg_pipeline_enabled", False)
    init_db()
    client = TestClient(app)
    headers = _admin_headers(client)
    pid = _create_project(client, headers, "D2D-GATE")
    fid = _upload_dxf(client, headers)

    resp = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={
            "project_id": pid,
            "task_type": TASK_DXF_TO_DWG,
            "precision_level": "normal",
            "params": {"file_id": fid},
        },
    )
    assert resp.status_code == 503


# ── conversion success ─────────────────────────────────────────────────────────


def test_run_dxf2dwg_conversion_success():
    """Mock ODA 成功 → job succeeded, 3 条 JobStep, AnalysisResult + DWG 文件。"""
    init_db()
    client = TestClient(app)
    headers = _admin_headers(client)
    pid = _create_project(client, headers, "D2D-OK")
    fid = _upload_dxf(client, headers)

    fake = _make_fake_convert_result(success=True)
    from app.services import dxf2dwg_service

    with (
        patch("dxf_converter.convert_file", return_value=fake) as mock_conv,
        patch(
            "app.services.dxf2dwg_service.save_bytes_as_file",
            wraps=dxf2dwg_service.save_bytes_as_file,
        ) as save_result,
    ):
        job_id, _ = _create_dxf2dwg_job(client, headers, pid, fid)

        # 源 DXF 的 $ACADVER=AC1015 → 期望 ODA 收到 version=ACAD2000
        call_kwargs = mock_conv.call_args.kwargs
        assert call_kwargs["version"] == "ACAD2000", call_kwargs

    assert save_result.call_args.kwargs["transfer_uid"]

    jobv = client.get(f"/api/v1/jobs/{job_id}", headers=headers)
    jd = jobv.json()["data"]
    assert jd["status"] == JOB_SUCCEEDED, f"status={jd['status']} err={jd.get('error_message')}"

    stepsv = client.get(f"/api/v1/jobs/{job_id}/steps", headers=headers)
    steps_data = stepsv.json()["data"]
    step_names = [s["step_name"] for s in steps_data]
    assert "download_source_dxf" in step_names
    assert "run_oda_convert_dxf" in step_names
    assert "persist_dwg_result" in step_names
    assert all(s["status"] == "succeeded" for s in steps_data)

    resultsv = client.get(f"/api/v1/jobs/{job_id}/results", headers=headers)
    results = resultsv.json()["data"]
    assert len(results) >= 1
    res = results[0]
    assert res["result_type"] == TASK_DXF_TO_DWG
    assert res["tool_version"] == "ACAD2000"  # 检测到的版本被记录


# ── version resolution priority ────────────────────────────────────────────────


def _seed_dwg_to_dxf_analysis_result(client: TestClient, headers: dict, dxf_file_id: int, tool_version: str) -> None:
    """模拟 DWG→DXF 管线产物：在测试 DB 里插入一条 AnalysisResult，
    result_file_id 指向给定 DXF 文件，tool_version 记录原始 DWG 版本。

    job_id NOT NULL 约束要求先有一条 Job 行；通过 API 创建一个 DWG→DXF
    job（mock ODA 成功）拿到 job_id，再插入 AnalysisResult 关联它。
    """
    from unittest.mock import MagicMock
    # 上传一个 DWG 文件用于创建 DWG→DXF job
    dwg_bytes = b"AC1027\x00" + b"\x00" * 1024
    resp = client.post(
        "/api/v1/files", headers=headers,
        files={"upload": ("seed.dwg", dwg_bytes, "application/acad")},
    )
    assert resp.status_code == 201
    dwg_file_id = resp.json()["data"]["id"]

    resp = client.post(
        "/api/v1/projects", headers=headers, json={"code": "SEED", "name": "SEED"},
    )
    seed_pid = resp.json()["data"]["id"]

    fake = MagicMock()
    fake.success = True
    fake.duration = 0.1
    fake.error = None
    fake.to_dict.return_value = {"success": True}
    fake_target = MagicMock()
    fake_target.is_file.return_value = True
    fake_target.read_bytes.return_value = b"DXF"
    fake.target = fake_target

    with patch("dwg_converter.convert_file", return_value=fake):
        resp = client.post(
            "/api/v1/jobs", headers=headers,
            json={
                "project_id": seed_pid,
                "task_type": TASK_DWG_TO_DXF,
                "precision_level": "normal",
                "params": {"file_id": dwg_file_id},
            },
        )
        seed_job_id = resp.json()["data"]["id"]

    from decimal import Decimal

    import app.services.dxf2dwg_service as dxf2dwg_svc
    from app.modules.jobs.interface import AnalysisResult

    db = dxf2dwg_svc.SessionLocal()
    try:
        db.add(AnalysisResult(
            job_id=seed_job_id,
            result_type=TASK_DWG_TO_DXF,
            result_json={"source": "test"},
            confidence=Decimal("1.0000"),
            result_file_id=dxf_file_id,
            algorithm_version="oda-file-converter",
            tool_version=tool_version,
            status="succeeded",
        ))
        db.commit()
    finally:
        db.close()


def test_version_resolution_prefers_analysis_result_lookup():
    """有 AnalysisResult（DWG→DXF 产物）→ 优先用其 tool_version，而非 $ACADVER。

    场景：源 DXF 的 $ACADVER=AC1032（ACAD2018），但 AnalysisResult 记录的原始
    DWG 版本是 ACAD2000（即 DXF 是从 AC1015 DWG 转来的）。反向查找应返回 ACAD2000。
    """
    init_db()
    client = TestClient(app)
    headers = _admin_headers(client)
    pid = _create_project(client, headers, "D2D-RT")

    # 上传一个 $ACADVER=AC1032 的 DXF（模拟 ODA 输出）
    fid = _upload_dxf(client, headers, content=_minimal_dxf(acadver="AC1032"))

    _seed_dwg_to_dxf_analysis_result(client, headers, fid, "ACAD2000")

    fake = _make_fake_convert_result(success=True)
    with patch("dxf_converter.convert_file", return_value=fake) as mock_conv:
        _create_dxf2dwg_job(client, headers, pid, fid)
        call_kwargs = mock_conv.call_args.kwargs
        # 反查优先 → ACAD2000，而非 $ACADVER 推出的 ACAD2018
        assert call_kwargs["version"] == "ACAD2000", call_kwargs


def test_version_resolution_falls_back_to_acadver_when_no_analysis_result():
    """无 AnalysisResult → 用 $ACADVER 头解析（外部上传的 DXF）。"""
    init_db()
    client = TestClient(app)
    headers = _admin_headers(client)
    pid = _create_project(client, headers, "D2D-EXT")

    # 直接上传 $ACADVER=AC1024 的 DXF，无 AnalysisResult
    fid = _upload_dxf(client, headers, content=_minimal_dxf(acadver="AC1024"))

    fake = _make_fake_convert_result(success=True)
    with patch("dxf_converter.convert_file", return_value=fake) as mock_conv:
        _create_dxf2dwg_job(client, headers, pid, fid)
        call_kwargs = mock_conv.call_args.kwargs
        assert call_kwargs["version"] == "ACAD2010", call_kwargs  # AC1024 → ACAD2010


def test_version_resolution_ignores_corrupted_tool_version():
    """AnalysisResult.tool_version 是未知值 → 忽略，回退到 $ACADVER 解析。"""
    init_db()
    client = TestClient(app)
    headers = _admin_headers(client)
    pid = _create_project(client, headers, "D2D-CORRUPT")

    fid = _upload_dxf(client, headers, content=_minimal_dxf(acadver="AC1018"))

    _seed_dwg_to_dxf_analysis_result(client, headers, fid, "GARBAGE_VERSION")

    fake = _make_fake_convert_result(success=True)
    with patch("dxf_converter.convert_file", return_value=fake) as mock_conv:
        _create_dxf2dwg_job(client, headers, pid, fid)
        call_kwargs = mock_conv.call_args.kwargs
        # 损坏值被忽略 → 回退到 $ACADVER=AC1018 → ACAD2004
        assert call_kwargs["version"] == "ACAD2004", call_kwargs


def test_version_resolution_missing_acadver_uses_default():
    """DXF 无 $ACADVER → 用配置默认版本。"""
    init_db()
    client = TestClient(app)
    headers = _admin_headers(client)
    pid = _create_project(client, headers, "D2D-NOHDR")

    # 构造一个无 $ACADVER 的 DXF
    no_header = (
        b"  0\nSECTION\n  2\nENTITIES\n"
        b"  0\nLINE\n  8\n0\n 10\n0.0\n 20\n0.0\n 11\n1.0\n 21\n1.0\n"
        b"  0\nENDSEC\n  0\nEOF\n"
    )
    fid = _upload_dxf(client, headers, content=no_header)

    fake = _make_fake_convert_result(success=True)
    with patch("dxf_converter.convert_file", return_value=fake) as mock_conv:
        _create_dxf2dwg_job(client, headers, pid, fid)
        call_kwargs = mock_conv.call_args.kwargs
        assert call_kwargs["version"] == settings.dxf2dwg_converter_version


# ── conversion failure ─────────────────────────────────────────────────────────


def test_run_dxf2dwg_conversion_oda_failure():
    """Mock ODA success=False → job failed, error_code=DWG_CONVERSION_FAILED。"""
    init_db()
    client = TestClient(app)
    headers = _admin_headers(client)
    pid = _create_project(client, headers, "D2D-FAIL")
    fid = _upload_dxf(client, headers)

    fake = _make_fake_convert_result(success=False)
    with patch("dxf_converter.convert_file", return_value=fake):
        job_id, _ = _create_dxf2dwg_job(client, headers, pid, fid)

    jobv = client.get(f"/api/v1/jobs/{job_id}", headers=headers)
    jd = jobv.json()["data"]
    assert jd["status"] == JOB_FAILED
    assert jd["error_code"] == "DWG_CONVERSION_FAILED"


def test_run_dxf2dwg_conversion_source_missing():
    """源文件不存在 → job failed, error_code=DXF_SOURCE_FILE_MISSING。"""
    init_db()
    client = TestClient(app)
    headers = _admin_headers(client)
    pid = _create_project(client, headers, "D2D-MISS")

    # 先上传一个 DXF 拿到合法 file_id，再把它从 storage 删掉模拟缺失
    fid = _upload_dxf(client, headers)
    import app.services.dxf2dwg_service as dxf2dwg_svc
    from app.modules.files.interface import StoredFile, get_storage_backend

    db = dxf2dwg_svc.SessionLocal()
    try:
        stored = db.get(StoredFile, fid)
        backend = get_storage_backend()
        path = backend.local_path(stored.bucket, stored.storage_key)
        if path and path.exists():
            path.unlink()
    finally:
        db.close()

    fake = _make_fake_convert_result(success=True)
    with patch("dxf_converter.convert_file", return_value=fake):
        job_id, _ = _create_dxf2dwg_job(client, headers, pid, fid)

    jobv = client.get(f"/api/v1/jobs/{job_id}", headers=headers)
    jd = jobv.json()["data"]
    assert jd["status"] == JOB_FAILED
    assert jd["error_code"] == "DXF_SOURCE_FILE_MISSING"
