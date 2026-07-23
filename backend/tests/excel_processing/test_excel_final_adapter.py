from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.modules.excel_processing import stage_adapter as excel_final
from app.modules.excel_processing.staging import stage_excel_source
from tests.support.paths import BACKEND_ROOT


def _protocol_line(payload: dict[str, object]) -> str:
    return "DWG_EXCEL_FINAL_RESULT=" + json.dumps(payload, ensure_ascii=False)


def _process_payload(output_path: Path) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "operation": "process",
        "output_path": str(output_path.resolve()),
        "quality_status": "warning",
        "warning_count": 1,
        "severe_warning_count": 0,
        "report_summary": {
            "info_count": 0,
            "warning_count": 1,
            "severe_warning_count": 0,
            "category_counts": {"手册查无": 1},
            "representative_messages": ["规格查无"],
        },
    }


def test_excel_final_stage_root_resolves_tracked_standalone_layout():
    stage_root = excel_final.get_excel_final_stage_root()

    assert (stage_root / "main.py").is_file()
    assert (stage_root / "pipeline.py").is_file()
    assert (stage_root / "handbook.py").is_file()


def test_excel_final_stage_root_failure_does_not_disclose_checked_paths(
    monkeypatch,
    tmp_path: Path,
):
    secret_root = tmp_path / "private-deployment-root"
    monkeypatch.setattr(excel_final.settings, "excel_final_stage_root", secret_root)
    monkeypatch.setattr(excel_final, "_REQUIRED_STAGE_FILES", ("never-present.codex",))

    with pytest.raises(excel_final.ExcelFinalUnavailableError) as raised:
        excel_final.get_excel_final_stage_root()

    assert str(secret_root) not in str(raised.value)
    assert "checked:" not in str(raised.value)


def test_handbook_health_log_does_not_disclose_mysql_host(monkeypatch, caplog):
    def fail_connect(**_kwargs):
        raise excel_final.pymysql.OperationalError(
            2003, "Can't connect to MySQL server on 'secret-db.internal'"
        )

    monkeypatch.setattr(excel_final.pymysql, "connect", fail_connect)

    assert excel_final.handbook_database_available() is False
    assert "secret-db.internal" not in caplog.text


def test_excel_final_dependency_probe_includes_legacy_xls_reader(monkeypatch):
    checked: list[str] = []

    def available(module_name: str):
        checked.append(module_name)
        return object()

    monkeypatch.setattr(excel_final.importlib.util, "find_spec", available)

    assert excel_final.excel_final_dependencies_available() is True
    assert "xlrd" in checked


def test_excel_final_fixed_width_probe_rejects_unrecognized_text():
    script = """
from pathlib import Path
from tempfile import TemporaryDirectory
import reader

with TemporaryDirectory() as directory:
    source = Path(directory) / 'not-tekla.xls'
    source.write_text('ordinary text without a production header', encoding='utf-8')
    try:
        reader._decode_fixed_text(source)
    except ValueError as exc:
        assert 'Cannot decode fixed-width Tekla text' in str(exc)
    else:
        raise AssertionError('unrecognized text was accepted as fixed-width Tekla')
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=excel_final.get_excel_final_stage_root(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_excel_final_pipeline_runs_in_isolated_subprocess(monkeypatch, tmp_path: Path):
    source_path = tmp_path / "source.xls"
    output_path = tmp_path / "result.xlsx"
    source_path.write_bytes(b"input")
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        workbook = Workbook()
        workbook.active.title = "整理表"
        workbook.save(output_path)
        internal_output_path = Path(
            command[command.index("--internal-output") + 1]
        )
        workbook.save(internal_output_path)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_protocol_line(_process_payload(output_path)),
            stderr="",
        )

    monkeypatch.setattr(excel_final.subprocess, "run", fake_run)
    monkeypatch.setattr(excel_final.settings, "handbook_mysql_password", "not-on-command-line")

    result = excel_final.run_excel_final_pipeline(
        source_path,
        output_path,
    )

    command = captured["command"]
    assert command[:3] == [
        sys.executable,
        "-m",
        "app.modules.excel_processing.stage_runner",
    ]
    assert "not-on-command-line" not in command
    assert "--format" not in command
    assert captured["cwd"] == excel_final.get_excel_final_stage_root()
    assert captured["env"]["DWG_HANDBOOK_MYSQL_PASSWORD"] == "not-on-command-line"
    assert result.output_path == output_path.resolve()
    assert result.internal_output_path == (
        output_path.with_name(f".{output_path.stem}.internal.xlsx").resolve()
    )
    assert result.internal_output_path.is_file()
    assert result.protocol_version == 1
    assert result.quality_status == "warning"
    assert result.warning_count == 1
    assert result.report_summary["category_counts"] == {
        "手册查无": 1,
    }


def test_excel_final_pipeline_rejects_non_xlsx_output_before_stage(
    monkeypatch, tmp_path: Path
):
    source_path = tmp_path / "source.xlsm"
    output_path = tmp_path / "result.xlsm"
    source_path.write_bytes(b"input")

    def unexpected_run(*_args, **_kwargs):
        pytest.fail("invalid output suffix must not start the Stage")

    monkeypatch.setattr(excel_final.subprocess, "run", unexpected_run)

    with pytest.raises(ValueError, match=r"\.xlsx"):
        excel_final.run_excel_final_pipeline(
            source_path,
            output_path,
        )


def test_excel_final_runner_is_importable_from_stage_working_directory():
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.modules.excel_processing.stage_runner",
            "--help",
        ],
        cwd=excel_final.get_excel_final_stage_root(),
        env=excel_final._stage_environment(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Isolated Excel Final Stage runner" in completed.stdout


def test_excel_final_pipeline_logs_internal_failure_but_raises_safe_message(
    monkeypatch,
    tmp_path: Path,
    caplog,
):
    source_path = tmp_path / "source.xls"
    output_path = tmp_path / "result.xlsx"
    source_path.write_bytes(b"input")
    internal_output_path: Path | None = None

    def fake_run(command, **kwargs):
        nonlocal internal_output_path
        internal_output_path = Path(
            command[command.index("--internal-output") + 1]
        )
        internal_output_path.touch()
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="pipeline exploded")

    monkeypatch.setattr(excel_final.subprocess, "run", fake_run)

    with pytest.raises(excel_final.ExcelFinalProcessError) as failure:
        excel_final.run_excel_final_pipeline(
            source_path,
            output_path,
        )

    assert str(failure.value) == "Excel Final Stage failed while processing the input."
    assert "pipeline exploded" not in str(failure.value)
    assert "pipeline exploded" not in caplog.text
    assert "processing failure" in caplog.text
    assert internal_output_path is not None
    assert not internal_output_path.exists()


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda payload: payload.pop("quality_status"), "invalid process result"),
        (lambda payload: payload.update(protocol_version=2), "invalid process result"),
        (lambda payload: payload.update(quality_status="mystery"), "invalid process result"),
        (lambda payload: payload.update(warning_count=-1), "invalid process result"),
        (
            lambda payload: payload.update(traceback="mysql://user:secret@internal/db"),
            "invalid process result",
        ),
    ],
)
def test_excel_final_process_protocol_rejects_malformed_or_extra_fields(
    monkeypatch,
    tmp_path: Path,
    mutation,
    match: str,
):
    source_path = tmp_path / "source.xlsx"
    output_path = tmp_path / "result.xlsx"
    source_path.write_bytes(b"input")
    payload = _process_payload(output_path)
    mutation(payload)

    def fake_run(command, **kwargs):
        output_path.touch()
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_protocol_line(payload),
            stderr="",
        )

    monkeypatch.setattr(excel_final.subprocess, "run", fake_run)

    with pytest.raises(excel_final.ExcelFinalProcessError, match=match):
        excel_final.run_excel_final_pipeline(
            source_path,
            output_path,
        )


def test_excel_final_lookup_protocol_passes_category_spec_and_material(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_protocol_line(
                {
                    "protocol_version": 1,
                    "operation": "lookup",
                    "category": "round_bar",
                    "normalized_spec": "24",
                    "material": "Q355B",
                    "weight_kg_per_m": 3.55,
                    "source": "round_square_bar:round_bar",
                    "status": "hit",
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(excel_final.subprocess, "run", fake_run)

    result = excel_final.lookup_excel_final_weight(
        category="round_bar",
        spec="D24",
        material="Q355B",
    )

    assert result.weight_kg_per_m == 3.55
    assert result.normalized_spec == "24"
    command = captured["command"]
    assert command[command.index("--category") + 1] == "round_bar"
    assert command[command.index("--spec") + 1] == "24"
    assert command[command.index("--material") + 1] == "Q355B"


def test_retired_post_output_repair_is_absent():
    assert not hasattr(excel_final, "normalize_excel_final_output")


def test_excel_final_staging_accepts_macro_enabled_workbook(
    db,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from app.modules.files.interface import StoredFile

    source = tmp_path / "stored-source.xlsm"
    workbook = Workbook()
    workbook.active.title = "原表"
    workbook.save(source)
    stored = StoredFile(
        bucket="dwg-reports",
        storage_key="uploads/stored-source.xlsm",
        original_name="source.xlsm",
        file_ext=".xlsm",
        content_type="application/vnd.ms-excel.sheet.macroEnabled.12",
        size_bytes=source.stat().st_size,
        sha256="3" * 64,
        status="available",
    )
    db.add(stored)
    db.flush()

    class LocalStorage:
        def local_path(self, _bucket, _storage_key):
            return source

    monkeypatch.setattr(
        "app.modules.excel_processing.staging.get_storage_backend",
        lambda: LocalStorage(),
    )

    work_dir = tmp_path / "attempt"
    work_dir.mkdir()
    staged, returned = stage_excel_source(db, stored.id, work_dir)

    assert returned.id == stored.id
    assert staged.suffix == ".xlsm"
    assert staged.is_file()


def test_excel_final_completion_event_does_not_pass_duplicate_batch_id():
    source = (
        BACKEND_ROOT
        / "app/modules/excel_processing/execution.py"
    ).read_text(encoding="utf-8")

    assert "batch_id=batch.id" not in source
