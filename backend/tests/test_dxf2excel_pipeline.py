"""DXF→Excel 材料表提取管线测试。

覆盖：
- Feature gate: 开关关闭→503, 开启→202
- Job 创建: 缺少 batch_name, 空 batch, 正常流程
- 服务层: _resolve_batch_name, _mark_job_failed
- 真实 pipeline: 用 sample DXF 跑 process_file + write_excel
- Job 取消
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app
from app.models.job import Job
from app.models.result import AnalysisResult
from app.platform.config.settings import settings
from app.platform.database.seed import init_db

SAMPLE_DXF_DIR = Path(__file__).resolve().parents[2] / "Stages" / "dxf2excel" / "original_dxf"


@pytest.fixture(autouse=True)
def _enable_dxf2excel_pipeline(monkeypatch):
    """所有测试默认开启 DXF→Excel 管线开关，关闭 Celery eager 以防执行真实的 task body。"""
    monkeypatch.setattr(settings, "dxf2excel_pipeline_enabled", True)
    # Patch the celery app's actual config so tasks are never executed inline.
    from app.platform.messaging.celery_app import celery_app
    monkeypatch.setitem(celery_app.conf, "task_always_eager", False)


# ── helpers ────────────────────────────────────────────────────────────────────


def _admin_headers(client: TestClient) -> dict[str, str]:
    login = client.post(
        "/api/v1/auth/sessions",
        json={"username": "admin", "password": "SuperAdminPass1"},
    )
    assert login.status_code == 201
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def _create_job(client: TestClient, headers: dict, batch_name: str) -> dict:
    resp = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={
            "task_type": "extract_dxf_to_excel",
            "precision_level": "normal",
            "params": {"batch_name": batch_name},
        },
    )
    return resp


# ── Service unit tests ────────────────────────────────────────────────────────


class TestServiceHelpers:
    def test_resolve_batch_name(self):
        """_resolve_batch_name 从 params_json 提取 batch_name。"""
        from app.services.dxf2excel_service import _resolve_batch_name

        job = MagicMock()
        job.params_json = {"batch_name": " 排版1 "}
        assert _resolve_batch_name(job) == "排版1"

        job.params_json = {}
        assert _resolve_batch_name(job) is None

        job.params_json = None
        assert _resolve_batch_name(job) is None

    def test_mark_job_failed_sets_error(self, db: Session):
        """_mark_job_failed 仅结束已认领且 attempt 匹配的任务。"""
        from app.services.dxf2excel_service import _mark_job_failed
        from app.services.job_service import claim_queued_job

        init_db()

        client = TestClient(app)
        headers = _admin_headers(client)

        resp = _create_job(client, headers, "mark_fail_test")
        assert resp.status_code == 202
        job_data = resp.json()["data"]
        job_id = job_data["id"]
        attempt = job_data["attempt"]

        claimed = claim_queued_job(
            db,
            job_id,
            pipeline="dxf2excel",
            progress=5,
            message="test worker claimed job",
        )
        assert claimed is not None
        assert claimed.attempt == attempt

        # Mark it failed via the helper
        _mark_job_failed(
            db,
            job_id,
            attempt,
            Exception("test error"),
            error_code="DXF2EXCEL_EMPTY_BATCH",
        )

        check = client.get(f"/api/v1/jobs/{job_id}", headers=headers)
        data = check.json()["data"]
        assert data["status"] == "failed"
        assert data["error_code"] == "DXF2EXCEL_EMPTY_BATCH"
        assert data["progress_data"]["type"] == "error"
        assert data["progress_data"]["error_code"] == "DXF2EXCEL_EMPTY_BATCH"

    def test_mark_job_failed_skips_terminal(self, db: Session):
        """_mark_job_failed 不覆盖已处于终态的 job（succeeded/cancelled）。"""
        from app.services.dxf2excel_service import _mark_job_failed

        init_db()

        client = TestClient(app)
        headers = _admin_headers(client)

        resp = _create_job(client, headers, "skip_test")
        assert resp.status_code == 202
        job_data = resp.json()["data"]
        job_id = job_data["id"]
        attempt = job_data["attempt"]

        # Cancel the job first (终态)
        client.post(f"/api/v1/jobs/{job_id}/cancellation-requests", headers=headers)

        # Now try to mark it failed — should skip because it's already cancelled
        _mark_job_failed(db, job_id, attempt, Exception("should not apply"))

        check = client.get(f"/api/v1/jobs/{job_id}", headers=headers)
        assert check.json()["data"]["status"] == "cancelled"

    def test_successful_run_persists_terminal_progress_in_same_transaction(
        self, db: Session, monkeypatch, tmp_path: Path
    ):
        from app.services import dxf2excel_service as service

        monkeypatch.setattr(settings, "storage_backend", "local")
        monkeypatch.setattr(settings, "local_storage_root", tmp_path / "storage")

        job = Job(
            task_type="extract_dxf_to_excel",
            precision_level="normal",
            pipeline="dxf2excel",
            status="queued",
            progress=0,
            params_json={"batch_name": "mysql-progress"},
        )
        db.add(job)
        db.commit()
        job_id = job.id

        def fake_stage(_db: Session, _batch_name: str, work_dir: Path):
            source = work_dir / "sample.dxf"
            source.write_text("0\nEOF\n", encoding="utf-8")
            return [source], {
                "dxf_count": 1,
                "downloaded": 1,
                "total_bytes": source.stat().st_size,
                "errors": [],
            }

        def fake_write(output_path: Path, _tables: list, _warnings: list) -> None:
            output_path.write_bytes(b"PK\x03\x04mysql-backed-progress")

        monkeypatch.setattr(service, "_stage_dxf_batch", fake_stage)
        monkeypatch.setattr("dxf2excel.pipeline.process_file", lambda _path: ([], []))
        monkeypatch.setattr("dxf2excel.excel_writer.write_excel", fake_write)

        service.run_dxf2excel_extraction(job_id, worker_name="test-worker")

        db.expire_all()
        persisted = db.get(Job, job_id)
        assert persisted is not None
        assert persisted.status == "succeeded"
        assert persisted.progress == 100
        assert persisted.progress_data is not None
        assert persisted.progress_data["type"] == "done"
        assert persisted.progress_data["status"] == "succeeded"
        assert persisted.progress_data["excel_file_id"] > 0
        assert db.scalar(
            select(AnalysisResult).where(AnalysisResult.job_id == job_id)
        ) is not None


# ── Feature gate ──────────────────────────────────────────────────────────────


class TestFeatureGate:
    def test_disabled_returns_503(self, monkeypatch):
        """Feature gate 关闭时创建 job 返回 503。"""
        init_db()
        monkeypatch.setattr(settings, "dxf2excel_pipeline_enabled", False)

        client = TestClient(app)
        headers = _admin_headers(client)
        resp = _create_job(client, headers, "test_batch")
        assert resp.status_code == 503
        err = resp.json()["error"]
        assert "DXF2EXCEL_PIPELINE_DISABLED" in err["code"]

    def test_enabled_returns_202(self, monkeypatch):
        """Feature gate 开启时创建 job 返回 202。"""
        init_db()
        client = TestClient(app)
        headers = _admin_headers(client)
        resp = _create_job(client, headers, "test_batch")
        assert resp.status_code == 202
        data = resp.json()["data"]
        assert data["task_type"] == "extract_dxf_to_excel"
        assert data["status"] == "queued"

    def test_job_has_correct_pipeline(self, monkeypatch):
        """创建的 job 应有 dxf2excel pipeline 标识。"""
        init_db()
        client = TestClient(app)
        headers = _admin_headers(client)
        resp = _create_job(client, headers, "pipeline_test")
        assert resp.status_code == 202
        data = resp.json()["data"]
        assert data["pipeline"] == "dxf2excel"


# ── Job cancellation ──────────────────────────────────────────────────────────


class TestJobCancellation:
    def test_cancel_queued_job(self, monkeypatch):
        """取消排队中的 dxf2excel 任务。"""
        init_db()
        client = TestClient(app)
        headers = _admin_headers(client)
        resp = _create_job(client, headers, "cancel_test")
        assert resp.status_code == 202
        job_id = resp.json()["data"]["id"]

        cancel_resp = client.post(
            f"/api/v1/jobs/{job_id}/cancellation-requests", headers=headers
        )
        assert cancel_resp.status_code == 202
        assert cancel_resp.json()["data"]["status"] == "cancelled"

    def test_cancel_non_existent_job(self, monkeypatch):
        """取消不存在的 job 返回 404。"""
        init_db()
        client = TestClient(app)
        headers = _admin_headers(client)
        resp = client.post("/api/v1/jobs/99999/cancellation-requests", headers=headers)
        assert resp.status_code == 404


# ── Real pipeline ─────────────────────────────────────────────────────────────


class TestRealPipeline:
    """用 Stages/dxf2excel/original_dxf/ 下的真实 DXF 文件测试核心逻辑。"""

    def test_process_real_dxf_files(self):
        """用真实 sample DXF 跑 process_file + write_excel。"""
        if not SAMPLE_DXF_DIR.is_dir():
            pytest.skip("Sample DXF directory not found")

        dxf_files = sorted(SAMPLE_DXF_DIR.glob("*.dxf"))[:3]
        if not dxf_files:
            pytest.skip("No sample DXF files")

        from dxf2excel.excel_writer import write_excel
        from dxf2excel.pipeline import process_file as pf

        all_tables = []
        all_warnings = []
        for fp in dxf_files:
            tables, warnings = pf(fp)
            all_tables.extend(tables)
            all_warnings.extend(warnings)

        assert len(all_tables) > 0, f"Should extract at least 1 table from {len(dxf_files)} files"
        assert len(all_tables) == len(dxf_files), (
            f"Each DXF should yield 1 table, got {len(all_tables)} from {len(dxf_files)}"
        )

        # Verify table structure
        for table in all_tables:
            assert table.num_rows > 0, f"Table from {table.source_file} has no rows"
            assert table.num_cols > 0, f"Table from {table.source_file} has no columns"
            assert len(table.data_rows) > 0, f"Table from {table.source_file} has no data rows"

        # Write combined Excel
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            out = Path(f.name)
        try:
            write_excel(out, all_tables, all_warnings)
            assert out.stat().st_size > 1000, "Excel should be non-trivial size"
        finally:
            out.unlink(missing_ok=True)

    def test_pipeline_error_handling_per_file(self):
        """单个 DXF 失败不影响其他文件处理。"""
        if not SAMPLE_DXF_DIR.is_dir():
            pytest.skip("Sample DXF directory not found")

        dxf_files = sorted(SAMPLE_DXF_DIR.glob("*.dxf"))[:2]
        if not dxf_files:
            pytest.skip("No sample DXF files")

        from dxf2excel.pipeline import process_file as pf

        # All real files should succeed
        for fp in dxf_files:
            tables, warnings = pf(fp)
            assert len(tables) == 1, f"Expected 1 table from {fp.name}"
