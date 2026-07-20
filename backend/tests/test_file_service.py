"""Unit tests for build_result_map() and build_zip() — the core
file resolution and ZIP assembly functions in file_service.py.

Covers both DWG→DXF and DXF→DWG pipeline directions, edge cases
(missing files, dedup), and the direction-agnostic format selection.
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

import app.services.job_service as job_service
from app.models.file import StoredFile
from app.models.job import Job
from app.models.result import AnalysisResult
from app.platform.config.constants import JOB_SUCCEEDED, TASK_DWG_TO_DXF, TASK_DXF_TO_DWG
from app.platform.http.exceptions import AppHTTPException
from app.services.file_service import (
    build_result_map,
    build_zip,
    build_zip_to_path,
    preview_zip_availability,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _file(db, **kw) -> StoredFile:
    """Create a StoredFile row with sensible defaults."""
    f = StoredFile(
        bucket=kw.pop("bucket", "dwg-original"),
        storage_key=kw.pop("storage_key", f"test/{hash(str(kw))}"),
        original_name=kw.pop("original_name", "test.dwg"),
        file_ext=kw.pop("file_ext", ".dwg"),
        content_type=kw.pop("content_type", "application/acad"),
        size_bytes=kw.pop("size_bytes", 1024),
        sha256=kw.pop("sha256", "a" * 64),
        md5=kw.pop("md5", "b" * 32),
        batch_name=kw.pop("batch_name", None),
        uploaded_by=kw.pop("uploaded_by", None),
        status=kw.pop("status", "available"),
        **kw,
    )
    db.add(f)
    db.flush()
    return f


def _job(db, **kw) -> Job:
    """Create a Job row with sensible defaults."""
    j = Job(
        task_type=kw.pop("task_type", TASK_DWG_TO_DXF),
        precision_level=kw.pop("precision_level", "normal"),
        pipeline=kw.pop("pipeline", "dxf_open_source"),
        status=kw.pop("status", JOB_SUCCEEDED),
        params_json=kw.pop("params_json", None),
        progress=kw.pop("progress", 100),
        **kw,
    )
    db.add(j)
    db.flush()
    return j


def _result(db, **kw) -> AnalysisResult:
    """Create an AnalysisResult row with sensible defaults."""
    r = AnalysisResult(
        result_type=kw.pop("result_type", TASK_DWG_TO_DXF),
        status=kw.pop("status", "succeeded"),
        **kw,
    )
    db.add(r)
    db.flush()
    return r


# ── fake storage for build_zip ───────────────────────────────────────────────


class FakeStorage:
    """In-memory storage backend for build_zip tests.

    Only implements iter_file() — the only method build_zip calls.
    """

    def __init__(self, files: dict[tuple[str, str], bytes]):
        self._files = files

    def iter_file(self, bucket: str, key: str, chunk_size=1024 * 1024):
        data = self._files.get((bucket, key))
        if data is None:
            raise FileNotFoundError(f"{bucket}/{key}")
        yield data


# ── build_result_map ─────────────────────────────────────────────────────────


class TestBuildResultMap:
    def test_empty_input_returns_empty_dict(self):
        db = job_service.SessionLocal()
        result = build_result_map(db, [])
        assert result == {}
        db.close()

    def test_no_matching_job_returns_all_none(self):
        db = job_service.SessionLocal()
        f1 = _file(db, original_name="a.dwg", file_ext=".dwg")
        f2 = _file(db, original_name="b.dwg", file_ext=".dwg")
        result = build_result_map(db, [f1.id, f2.id])
        assert result == {f1.id: None, f2.id: None}
        db.rollback()
        db.close()

    def test_dwg_to_dxf_succeeded_returns_dxf_result(self):
        db = job_service.SessionLocal()
        src = _file(db, original_name="plan.dwg", file_ext=".dwg", bucket="dwg-original")
        res = _file(db, original_name="plan.dxf", file_ext=".dxf", bucket="dxf-derived")
        job = _job(db, task_type=TASK_DWG_TO_DXF, status="succeeded",
                   params_json={"file_id": src.id})
        _result(db, job_id=job.id, result_type=TASK_DWG_TO_DXF,
                result_file_id=res.id, status="succeeded")
        db.commit()
        result = build_result_map(db, [src.id])
        assert result[src.id] is not None
        assert result[src.id].id == res.id
        assert result[src.id].file_ext == ".dxf"
        db.rollback()
        db.close()

    def test_dxf_to_dwg_succeeded_returns_dwg_result(self):
        db = job_service.SessionLocal()
        src = _file(db, original_name="export.dxf", file_ext=".dxf", bucket="dxf-original")
        res = _file(db, original_name="export.dwg", file_ext=".dwg", bucket="dwg-derived")
        job = _job(db, task_type=TASK_DXF_TO_DWG, status="succeeded",
                   params_json={"file_id": src.id})
        _result(db, job_id=job.id, result_type=TASK_DXF_TO_DWG,
                result_file_id=res.id, status="succeeded")
        db.commit()
        result = build_result_map(db, [src.id])
        assert result[src.id] is not None
        assert result[src.id].id == res.id
        assert result[src.id].file_ext == ".dwg"
        db.rollback()
        db.close()

    def test_job_not_succeeded_returns_none(self):
        db = job_service.SessionLocal()
        src = _file(db, original_name="fail.dwg", file_ext=".dwg")
        job = _job(db, task_type=TASK_DWG_TO_DXF, status="failed",
                   params_json={"file_id": src.id})
        res = _file(db, original_name="fail.dxf", file_ext=".dxf")
        _result(db, job_id=job.id, result_type=TASK_DWG_TO_DXF,
                result_file_id=res.id, status="succeeded")
        db.commit()
        result = build_result_map(db, [src.id])
        assert result[src.id] is None  # job not succeeded → not matched
        db.rollback()
        db.close()

    def test_mixed_results(self):
        db = job_service.SessionLocal()
        src1 = _file(db, original_name="ok.dwg", file_ext=".dwg")
        src2 = _file(db, original_name="nojob.dwg", file_ext=".dwg")
        src3 = _file(db, original_name="also_nojob.dwg", file_ext=".dwg")

        res = _file(db, original_name="ok.dxf", file_ext=".dxf", bucket="dxf-derived")
        job = _job(db, task_type=TASK_DWG_TO_DXF, status="succeeded",
                   params_json={"file_id": src1.id})
        _result(db, job_id=job.id, result_type=TASK_DWG_TO_DXF,
                result_file_id=res.id, status="succeeded")
        db.commit()
        result = build_result_map(db, [src1.id, src2.id, src3.id])
        assert result[src1.id] is not None
        assert result[src2.id] is None
        assert result[src3.id] is None
        db.rollback()
        db.close()

    def test_result_file_soft_deleted_returns_none(self):
        """A deleted result file should not be returned."""
        db = job_service.SessionLocal()
        src = _file(db, original_name="src.dwg", file_ext=".dwg", bucket="dwg-original")
        res = _file(db, original_name="res.dxf", file_ext=".dxf", bucket="dxf-derived",
                    status="deleted")
        job = _job(db, task_type=TASK_DWG_TO_DXF, status="succeeded",
                   params_json={"file_id": src.id})
        _result(db, job_id=job.id, result_type=TASK_DWG_TO_DXF,
                result_file_id=res.id, status="succeeded")
        db.commit()
        result = build_result_map(db, [src.id])
        assert result[src.id] is None  # deleted file excluded
        db.rollback()
        db.close()

    def test_multiple_successful_jobs_return_latest_job_result(self):
        """A retry/new conversion must replace the older downloadable result."""
        db = job_service.SessionLocal()
        src = _file(db, original_name="retry.dwg", file_ext=".dwg", bucket="dwg-original")
        old_result = _file(
            db,
            original_name="retry-old.dxf",
            file_ext=".dxf",
            bucket="dxf-derived",
        )
        new_result = _file(
            db,
            original_name="retry-new.dxf",
            file_ext=".dxf",
            bucket="dxf-derived",
        )
        old_job = _job(
            db,
            task_type=TASK_DWG_TO_DXF,
            status="succeeded",
            params_json={"file_id": src.id},
        )
        _result(
            db,
            job_id=old_job.id,
            result_type=TASK_DWG_TO_DXF,
            result_file_id=old_result.id,
            status="succeeded",
        )
        new_job = _job(
            db,
            task_type=TASK_DWG_TO_DXF,
            status="succeeded",
            params_json={"file_id": src.id},
        )
        _result(
            db,
            job_id=new_job.id,
            result_type=TASK_DWG_TO_DXF,
            result_file_id=new_result.id,
            status="succeeded",
        )
        db.commit()

        result = build_result_map(db, [src.id])

        assert result[src.id] is not None
        assert result[src.id].id == new_result.id
        db.rollback()
        db.close()

    def test_multiple_results_for_latest_job_return_latest_result(self):
        """Result selection is deterministic even if a job emitted replacement rows."""
        db = job_service.SessionLocal()
        src = _file(db, original_name="replace.dwg", file_ext=".dwg", bucket="dwg-original")
        first_result = _file(
            db,
            original_name="replace-first.dxf",
            file_ext=".dxf",
            bucket="dxf-derived",
        )
        replacement_result = _file(
            db,
            original_name="replace-final.dxf",
            file_ext=".dxf",
            bucket="dxf-derived",
        )
        job = _job(
            db,
            task_type=TASK_DWG_TO_DXF,
            status="succeeded",
            params_json={"file_id": src.id},
        )
        _result(
            db,
            job_id=job.id,
            result_type=TASK_DWG_TO_DXF,
            result_file_id=first_result.id,
            status="succeeded",
        )
        _result(
            db,
            job_id=job.id,
            result_type=TASK_DWG_TO_DXF,
            result_file_id=replacement_result.id,
            status="succeeded",
        )
        db.commit()

        result = build_result_map(db, [src.id])

        assert result[src.id] is not None
        assert result[src.id].id == replacement_result.id
        db.rollback()
        db.close()


# ── build_zip ────────────────────────────────────────────────────────────────


class TestBuildZip:
    def test_preview_reports_partial_format_coverage(self):
        db = job_service.SessionLocal()
        source_with_result = _file(
            db,
            original_name="converted.dwg",
            file_ext=".dwg",
            bucket="dwg-original",
        )
        source_without_result = _file(
            db,
            original_name="waiting.dwg",
            file_ext=".dwg",
            bucket="dwg-original",
        )
        result = _file(
            db,
            original_name="converted.dxf",
            file_ext=".dxf",
            bucket="dxf-derived",
        )
        job = _job(
            db,
            task_type=TASK_DWG_TO_DXF,
            status="succeeded",
            params_json={"file_id": source_with_result.id},
        )
        _result(
            db,
            job_id=job.id,
            result_type=TASK_DWG_TO_DXF,
            result_file_id=result.id,
            status="succeeded",
        )
        db.commit()

        preview = preview_zip_availability(
            db,
            [source_with_result.id, source_without_result.id],
            ["dwg", "dxf"],
        )

        assert preview.file_count == 2
        assert preview.can_download is False
        by_format = {item.format: item for item in preview.formats}
        assert by_format["dwg"].available_count == 2
        assert by_format["dwg"].missing_count == 0
        assert by_format["dwg"].complete is True
        assert by_format["dxf"].available_count == 1
        assert by_format["dxf"].missing_count == 1
        assert by_format["dxf"].missing_file_ids == [source_without_result.id]
        assert by_format["dxf"].complete is False
        db.rollback()
        db.close()

    def test_preview_deduplicates_file_ids(self):
        db = job_service.SessionLocal()
        source = _file(db, original_name="only.dwg", file_ext=".dwg")
        db.commit()

        preview = preview_zip_availability(db, [source.id, source.id], ["dwg"])

        assert preview.file_count == 1
        assert preview.can_download is True
        assert preview.formats[0].available_count == 1
        db.rollback()
        db.close()

    def test_dwg_source_want_dwg_includes_source(self, monkeypatch):
        db = job_service.SessionLocal()
        src = _file(db, original_name="plan.dwg", file_ext=".dwg",
                    bucket="dwg-original", storage_key="k1")
        db.commit()
        storage = FakeStorage({("dwg-original", "k1"): b"DWGCONTENT"})
        monkeypatch.setattr("app.services.storage_service.get_storage_backend", lambda: storage)
        zip_bytes, name = build_zip(db, [src.id], ["dwg"], "export")
        assert name == "export.zip"
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            assert "export/plan.dwg" in zf.namelist()
            assert zf.read("export/plan.dwg") == b"DWGCONTENT"
        db.rollback()
        db.close()

    def test_dwg_source_with_dxf_result_want_dxf_includes_result(self, monkeypatch):
        db = job_service.SessionLocal()
        src = _file(db, original_name="plan.dwg", file_ext=".dwg",
                    bucket="dwg-original", storage_key="sk1")
        res = _file(db, original_name="plan.dxf", file_ext=".dxf",
                    bucket="dxf-derived", storage_key="rk1")
        job = _job(db, task_type=TASK_DWG_TO_DXF, status="succeeded",
                   params_json={"file_id": src.id})
        _result(db, job_id=job.id, result_type=TASK_DWG_TO_DXF,
                result_file_id=res.id, status="succeeded")
        db.commit()
        storage = FakeStorage({
            ("dwg-original", "sk1"): b"SRC_DWG",
            ("dxf-derived", "rk1"): b"RESULT_DXF",
        })
        monkeypatch.setattr("app.services.storage_service.get_storage_backend", lambda: storage)
        zip_bytes, _ = build_zip(db, [src.id], ["dxf"], "export")
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            assert "export/plan.dxf" in zf.namelist()
            assert zf.read("export/plan.dxf") == b"RESULT_DXF"
        db.rollback()
        db.close()

    def test_dxf_source_with_dwg_result_want_dwg_includes_result(self, monkeypatch):
        db = job_service.SessionLocal()
        src = _file(db, original_name="data.dxf", file_ext=".dxf",
                    bucket="dxf-original", storage_key="sk2")
        res = _file(db, original_name="data.dwg", file_ext=".dwg",
                    bucket="dwg-derived", storage_key="rk2")
        job = _job(db, task_type=TASK_DXF_TO_DWG, status="succeeded",
                   params_json={"file_id": src.id})
        _result(db, job_id=job.id, result_type=TASK_DXF_TO_DWG,
                result_file_id=res.id, status="succeeded")
        db.commit()
        storage = FakeStorage({
            ("dxf-original", "sk2"): b"SRC_DXF",
            ("dwg-derived", "rk2"): b"RESULT_DWG",
        })
        monkeypatch.setattr("app.services.storage_service.get_storage_backend", lambda: storage)
        zip_bytes, _ = build_zip(db, [src.id], ["dwg"], "export")
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            assert "export/data.dwg" in zf.namelist()
            assert zf.read("export/data.dwg") == b"RESULT_DWG"
        db.rollback()
        db.close()

    def test_dxf_source_want_dxf_includes_source(self, monkeypatch):
        db = job_service.SessionLocal()
        src = _file(db, original_name="layout.dxf", file_ext=".dxf",
                    bucket="dxf-original", storage_key="sk3")
        db.commit()
        storage = FakeStorage({("dxf-original", "sk3"): b"DXF_DATA"})
        monkeypatch.setattr("app.services.storage_service.get_storage_backend", lambda: storage)
        zip_bytes, _ = build_zip(db, [src.id], ["dxf"], "export")
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            assert zf.read("export/layout.dxf") == b"DXF_DATA"
        db.rollback()
        db.close()

    def test_both_formats_all_available_includes_both(self, monkeypatch):
        db = job_service.SessionLocal()
        src = _file(db, original_name="plan.dwg", file_ext=".dwg",
                    bucket="dwg-original", storage_key="sk4")
        res = _file(db, original_name="plan.dxf", file_ext=".dxf",
                    bucket="dxf-derived", storage_key="rk4")
        job = _job(db, task_type=TASK_DWG_TO_DXF, status="succeeded",
                   params_json={"file_id": src.id})
        _result(db, job_id=job.id, result_type=TASK_DWG_TO_DXF,
                result_file_id=res.id, status="succeeded")
        db.commit()
        storage = FakeStorage({
            ("dwg-original", "sk4"): b"SRC_DWG",
            ("dxf-derived", "rk4"): b"RES_DXF",
        })
        monkeypatch.setattr("app.services.storage_service.get_storage_backend", lambda: storage)
        zip_bytes, _ = build_zip(db, [src.id], ["dwg", "dxf"], "export")
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            assert "export/plan.dwg" in names
            assert "export/plan.dxf" in names
            assert zf.read("export/plan.dwg") == b"SRC_DWG"
            assert zf.read("export/plan.dxf") == b"RES_DXF"
        db.rollback()
        db.close()

    def test_missing_source_fails_export(self, monkeypatch):
        db = job_service.SessionLocal()
        storage = FakeStorage({})
        monkeypatch.setattr("app.services.storage_service.get_storage_backend", lambda: storage)
        with pytest.raises(AppHTTPException) as exc:
            build_zip(db, [999], ["dwg"], "export")
        assert exc.value.detail["code"] == "FILE_EXPORT_SOURCE_MISSING"
        db.close()

    def test_stem_deduplication_adds_suffix(self, monkeypatch):
        db = job_service.SessionLocal()
        f1 = _file(db, original_name="plan.dwg", file_ext=".dwg",
                   bucket="dwg-original", storage_key="ka")
        f2 = _file(db, original_name="plan.dwg", file_ext=".dwg",
                   bucket="dwg-original", storage_key="kb")
        db.commit()
        storage = FakeStorage({
            ("dwg-original", "ka"): b"AAA",
            ("dwg-original", "kb"): b"BBB",
        })
        monkeypatch.setattr("app.services.storage_service.get_storage_backend", lambda: storage)
        zip_bytes, _ = build_zip(db, [f1.id, f2.id], ["dwg"], "export")
        # Both files have stem "plan" → both numbered: (1) and (2)
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            assert len(names) == 2
            assert "export/plan(1).dwg" in names
            assert "export/plan(2).dwg" in names
            assert zf.read("export/plan(1).dwg") == b"AAA"
            assert zf.read("export/plan(2).dwg") == b"BBB"
        db.rollback()
        db.close()

    def test_want_format_but_no_result_fails_export(self, monkeypatch):
        db = job_service.SessionLocal()
        src = _file(db, original_name="only.dxf", file_ext=".dxf",
                    bucket="dxf-original", storage_key="sk")
        db.commit()
        storage = FakeStorage({("dxf-original", "sk"): b"DXF_ONLY"})
        monkeypatch.setattr("app.services.storage_service.get_storage_backend", lambda: storage)
        with pytest.raises(AppHTTPException) as exc:
            build_zip(db, [src.id], ["dwg"], "export")
        assert exc.value.detail["code"] == "FILE_EXPORT_FORMAT_UNAVAILABLE"
        db.rollback()
        db.close()

    def test_storage_read_failure_fails_export(self, monkeypatch):
        db = job_service.SessionLocal()
        src = _file(db, original_name="bad.dwg", file_ext=".dwg",
                    bucket="dwg-original", storage_key="badkey")
        db.commit()

        class BadStorage:
            def iter_file(self, bucket, key, chunk_size=1024*1024):
                raise RuntimeError("storage unavailable")

        monkeypatch.setattr("app.services.storage_service.get_storage_backend",
                           lambda: BadStorage())
        with pytest.raises(AppHTTPException) as exc:
            build_zip(db, [src.id], ["dwg"], "export")
        assert exc.value.detail["code"] == "STORAGE_INCONSISTENT"
        db.rollback()
        db.close()

    def test_build_zip_to_path_returns_spooled_export(self, monkeypatch):
        db = job_service.SessionLocal()
        src = _file(
            db,
            original_name="large.dwg",
            file_ext=".dwg",
            bucket="dwg-original",
            storage_key="large-key",
        )
        db.commit()
        storage = FakeStorage({("dwg-original", "large-key"): b"A" * 4096})
        monkeypatch.setattr("app.services.storage_service.get_storage_backend", lambda: storage)

        prepared = build_zip_to_path(db, [src.id], ["dwg"], "export")
        try:
            assert prepared.path.is_file()
            assert prepared.filename == "export.zip"
            assert prepared.size_bytes == prepared.path.stat().st_size
            assert prepared.included_file_ids == (src.id,)
        finally:
            prepared.path.unlink(missing_ok=True)
            db.close()
