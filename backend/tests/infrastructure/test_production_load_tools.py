from __future__ import annotations

import asyncio
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.production.resource_sampler import (  # noqa: E402
    DockerStat,
    clamp_sampling_interval,
    parse_docker_stat,
    parse_size_bytes,
    summarize_samples,
)
from scripts.production.workflow_load import (  # noqa: E402
    CountConservationError,
    LoadFixture,
    ProjectCounts,
    ProjectRunResult,
    ScenarioResult,
    WorkflowRunner,
    inspect_split_archive,
    parse_positive_int_list,
    percentile,
    redact_secrets,
    report_exit_code,
    select_dwg_files,
    summarize_project_results,
    validate_project_counts,
)


def test_positive_integer_list_is_ordered_and_deduplicated() -> None:
    assert parse_positive_int_list("1,4,4,8") == [1, 4, 8]


@pytest.mark.parametrize("value", ["", "0", "1,-2", "one,2"])
def test_positive_integer_list_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError, match="正整数"):
        parse_positive_int_list(value)


def test_percentile_uses_linear_interpolation_and_rejects_empty_samples() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == pytest.approx(2.5)
    assert percentile([1.0, 2.0, 3.0, 4.0], 95) == pytest.approx(3.85)
    with pytest.raises(ValueError, match="不能为空"):
        percentile([], 95)


def test_secret_redaction_is_recursive_and_keeps_safe_business_fields() -> None:
    payload = {
        "username": "operator01",
        "password": "secret",
        "headers": {"Authorization": "Bearer token", "X-Request-ID": "req-1"},
        "nested": [{"access_token": "jwt", "status": "succeeded"}],
        "dsn": "mysql+pymysql://user:pass@mysql/db",
    }

    assert redact_secrets(payload) == {
        "username": "operator01",
        "password": "[REDACTED]",
        "headers": {
            "Authorization": "[REDACTED]",
            "X-Request-ID": "req-1",
        },
        "nested": [{"access_token": "[REDACTED]", "status": "succeeded"}],
        "dsn": "[REDACTED]",
    }


def test_project_counts_require_full_conversion_and_classification_conservation() -> None:
    counts = ProjectCounts(
        source_dwg=30,
        converted_dxf=30,
        classification_input=30,
        classified=28,
        review_required=1,
        unreadable=1,
        split_input=20,
        split_auto_accepted=17,
        split_manual_review=3,
        split_failed=1,
    )

    validate_project_counts(counts)


@pytest.mark.parametrize(
    "counts, expected",
    [
        (
            ProjectCounts(
                source_dwg=30,
                converted_dxf=29,
                classification_input=29,
                classified=29,
                review_required=0,
                unreadable=0,
                split_input=20,
                split_auto_accepted=20,
                split_manual_review=0,
                split_failed=0,
            ),
            "DWG 与服务器派生 DXF",
        ),
        (
            ProjectCounts(
                source_dwg=30,
                converted_dxf=30,
                classification_input=30,
                classified=29,
                review_required=0,
                unreadable=0,
                split_input=20,
                split_auto_accepted=20,
                split_manual_review=0,
                split_failed=0,
            ),
            "分类结果",
        ),
        (
            ProjectCounts(
                source_dwg=30,
                converted_dxf=30,
                classification_input=30,
                classified=30,
                review_required=0,
                unreadable=0,
                split_input=20,
                split_auto_accepted=18,
                split_manual_review=1,
                split_failed=0,
            ),
            "拆板结果",
        ),
        (
            ProjectCounts(
                source_dwg=30,
                converted_dxf=30,
                classification_input=30,
                classified=30,
                review_required=0,
                unreadable=0,
                split_input=20,
                split_auto_accepted=17,
                split_manual_review=3,
                split_failed=4,
            ),
            "失败数",
        ),
    ],
)
def test_project_counts_reject_any_silent_loss(
    counts: ProjectCounts,
    expected: str,
) -> None:
    with pytest.raises(CountConservationError, match=expected):
        validate_project_counts(counts)


def test_report_exit_code_fails_when_any_scenario_fails() -> None:
    assert report_exit_code([ScenarioResult(name="a", succeeded=True)]) == 0
    assert (
        report_exit_code(
            [
                ScenarioResult(name="a", succeeded=True),
                ScenarioResult(name="b", succeeded=False, error_code="HTTP_500"),
            ]
        )
        == 1
    )


def test_cli_rejects_missing_environment_without_leaking_secret_values(
    tmp_path: Path,
) -> None:
    report = tmp_path / "report.json"
    script = PROJECT_ROOT / "scripts/production/workflow_load.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--base-url",
            "http://127.0.0.1:1",
            "--accounts",
            "operator01",
            "--dwg-dir",
            str(tmp_path),
            "--excel",
            str(tmp_path / "missing.xlsx"),
            "--report",
            str(report),
        ],
        cwd=PROJECT_ROOT,
        env={},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "环境变量" in result.stderr
    assert "password" not in result.stderr.casefold()
    assert not report.exists() or "access_token" not in json.dumps(
        json.loads(report.read_text(encoding="utf-8"))
    )


def _envelope(data: object, request_id: str) -> dict[str, object]:
    return {
        "data": data,
        "meta": {
            "request_id": request_id,
            "timestamp": "2026-07-27T01:00:00Z",
        },
    }


def _split_archive() -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("原长/panel-a.dxf", "DXF-A")
        archive.writestr("原长/panel-b.dxf", "DXF-B")
        archive.writestr("余量增长后短文件/panel-a.dxf", "DXF-A+")
        archive.writestr("余量增长后短文件/panel-b.dxf", "DXF-B+")
    return payload.getvalue()


def test_workflow_runner_uses_real_public_sequence_and_conserves_counts(
    tmp_path: Path,
) -> None:
    excel = tmp_path / "parts.xlsx"
    excel.write_bytes(b"PK\x03\x04-xlsx")
    dwg_dir = tmp_path / "生产图纸"
    dwg_dir.mkdir()
    dwg_files = [dwg_dir / "panel-a.dwg", dwg_dir / "panel-b.dwg"]
    for index, path in enumerate(dwg_files, start=1):
        path.write_bytes(f"AC1027-DWG-{index}".encode())
    fixture = LoadFixture(excel=excel, dwg_files=tuple(dwg_files))
    requests: list[tuple[str, str]] = []
    batch_poll = 0
    classification_poll = 0
    split_poll = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal batch_poll, classification_poll, split_poll
        requests.append((request.method, request.url.path))
        request_id = f"req-{len(requests)}"
        if request.url.path == "/api/v1/auth/sessions":
            assert request.method == "POST"
            credentials = json.loads(request.content)
            assert credentials == {"username": "operator01", "password": "secret"}
            return httpx.Response(
                201,
                json=_envelope(
                    {
                        "access_token": "not-written-to-report",
                        "user": {
                            "username": "operator01",
                            "roles": [{"code": "operator"}],
                        },
                    },
                    request_id,
                ),
            )
        assert request.headers["authorization"] == "Bearer not-written-to-report"
        if request.url.path == "/api/v1/workflows/production-projects":
            return httpx.Response(
                201,
                json=_envelope(
                    {
                        "project": {"id": 70, "code": "LOAD-001"},
                        "workflow": {"id": 41, "current_stage": "source_intake"},
                    },
                    request_id,
                ),
            )
        if request.url.path == "/api/v1/workflows/41/input-batch" and request.method == "POST":
            return httpx.Response(201, json=_envelope({"id": 501}, request_id))
        if request.url.path == "/api/v1/workflows/41/input-excel":
            assert b'filename="parts.xlsx"' in request.content
            return httpx.Response(201, json=_envelope({"id": 501}, request_id))
        if request.url.path == "/api/v1/workflows/41/input-dwg-folder":
            assert b'filename="panel-a.dwg"' in request.content
            assert b'filename="panel-b.dwg"' in request.content
            assert "生产图纸/panel-a.dwg".encode() in request.content
            return httpx.Response(201, json=_envelope({"id": 501}, request_id))
        if request.url.path == "/api/v1/workflows/41/input-batch/conversion-requests":
            return httpx.Response(
                202,
                json=_envelope(
                    {
                        "batch": {"id": 501},
                        "jobs": [{"id": 801}, {"id": 802}],
                        "dispatched_count": 2,
                    },
                    request_id,
                ),
            )
        if request.url.path == "/api/v1/workflows/41/input-batch" and request.method == "GET":
            batch_poll += 1
            paired = 2 if batch_poll >= 2 else 0
            return httpx.Response(
                200,
                json=_envelope(
                    {
                        "id": 501,
                        "status": "ready_to_freeze" if paired else "converting",
                        "counts": {
                            "dwg": 2,
                            "excel": 1,
                            "paired": paired,
                            "converting": 0 if paired else 2,
                            "failed": 0,
                        },
                        "issues": [],
                        "freeze_ready": paired == 2,
                    },
                    request_id,
                ),
            )
        if request.url.path == "/api/v1/workflows/41/input-batch/freeze":
            return httpx.Response(
                200,
                json=_envelope({"id": 501, "status": "frozen"}, request_id),
            )
        if request.url.path.endswith("/stages/dxf_classification/executions"):
            return httpx.Response(
                202,
                json=_envelope(
                    {
                        "job": {"id": 901, "attempt": 1, "status": "queued"},
                        "reused": False,
                        "retried": False,
                    },
                    request_id,
                ),
            )
        if request.url.path == "/api/v1/workflows/41/dxf-classification":
            classification_poll += 1
            status = "running" if classification_poll == 1 else "completed"
            return httpx.Response(
                200,
                json=_envelope(
                    {
                        "id": 77,
                        "status": status,
                        "input_count": 2,
                        "classified_count": 2 if status == "completed" else 0,
                        "review_required_count": 0,
                        "unreadable_count": 0,
                        "type_counts": {"BH": 2} if status == "completed" else {},
                        "job": {"id": 901, "attempt": 1, "status": "succeeded"},
                    },
                    request_id,
                ),
            )
        if request.url.path.endswith("/stages/drawing_processing/executions"):
            return httpx.Response(
                202,
                json=_envelope(
                    {
                        "job": {"id": 902, "attempt": 1, "status": "queued"},
                        "reused": False,
                        "retried": False,
                    },
                    request_id,
                ),
            )
        if request.url.path == "/api/v1/workflows/41/drawing-processing":
            split_poll += 1
            status = "running" if split_poll == 1 else "completed"
            return httpx.Response(
                200,
                json=_envelope(
                    {
                        "id": 88,
                        "status": status,
                        "input_count": 2,
                        "processed_count": 2 if status == "completed" else 0,
                        "auto_accepted_count": 2 if status == "completed" else 0,
                        "manual_review_count": 0,
                        "failed_count": 0,
                        "job": {"id": 902, "attempt": 1, "status": "succeeded"},
                    },
                    request_id,
                ),
            )
        if request.url.path.endswith("/stages/drawing_processing/download-archive"):
            return httpx.Response(
                200,
                content=_split_archive(),
                headers={"content-type": "application/zip"},
            )
        return httpx.Response(404, json={"unexpected": request.url.path})

    async def run() -> ProjectRunResult:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://testserver",
        ) as client:
            runner = WorkflowRunner(
                client=client,
                poll_interval_seconds=0,
                stage_timeout_seconds=2,
            )
            return await runner.run_project(
                name="operator01-project-1",
                username="operator01",
                password="secret",
                fixture=fixture,
                project_code="LOAD-001",
            )

    result = asyncio.run(run())

    assert result.succeeded is True
    assert result.counts == ProjectCounts(
        source_dwg=2,
        converted_dxf=2,
        classification_input=2,
        classified=2,
        review_required=0,
        unreadable=0,
        split_input=2,
        split_auto_accepted=2,
        split_manual_review=0,
        split_failed=0,
    )
    assert result.archive_dxf_count == 4
    assert result.workflow_id == 41
    assert result.job_attempts == {
        "dxf_classification": {"job_id": 901, "attempt": 1},
        "drawing_processing": {"job_id": 902, "attempt": 1},
    }
    assert result.request_ids
    report_payload = redact_secrets(result.to_report())
    assert "not-written-to-report" not in json.dumps(report_payload)
    assert requests == [
        ("POST", "/api/v1/auth/sessions"),
        ("POST", "/api/v1/workflows/production-projects"),
        ("POST", "/api/v1/workflows/41/input-batch"),
        ("POST", "/api/v1/workflows/41/input-excel"),
        ("POST", "/api/v1/workflows/41/input-dwg-folder"),
        ("POST", "/api/v1/workflows/41/input-batch/conversion-requests"),
        ("GET", "/api/v1/workflows/41/input-batch"),
        ("GET", "/api/v1/workflows/41/input-batch"),
        ("POST", "/api/v1/workflows/41/input-batch/freeze"),
        ("POST", "/api/v1/workflows/41/stages/dxf_classification/executions"),
        ("GET", "/api/v1/workflows/41/dxf-classification"),
        ("GET", "/api/v1/workflows/41/dxf-classification"),
        ("POST", "/api/v1/workflows/41/stages/drawing_processing/executions"),
        ("GET", "/api/v1/workflows/41/drawing-processing"),
        ("GET", "/api/v1/workflows/41/drawing-processing"),
        ("GET", "/api/v1/workflows/41/stages/drawing_processing/download-archive"),
    ]


def test_split_archive_rejects_path_traversal_and_duplicate_names() -> None:
    traversal = io.BytesIO()
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("../escape.dxf", "bad")
    with pytest.raises(ValueError, match="安全路径"):
        inspect_split_archive(traversal.getvalue())

    duplicate = io.BytesIO()
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("原长/A.dxf", "one")
        archive.writestr("原长/a.dxf", "two")
    with pytest.raises(ValueError, match="重复"):
        inspect_split_archive(duplicate.getvalue())


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("0B", 0),
        ("512B", 512),
        ("1KiB", 1024),
        ("1.5MiB", 1_572_864),
        ("2GB", 2_000_000_000),
    ],
)
def test_resource_sampler_parses_docker_sizes(raw: str, expected: int) -> None:
    assert parse_size_bytes(raw) == expected


def test_resource_sampler_parses_docker_stat_without_locale_assumptions() -> None:
    row = {
        "Name": "worker-dxf-split",
        "CPUPerc": "125.50%",
        "MemUsage": "1.25GiB / 62GiB",
        "MemPerc": "2.02%",
        "NetIO": "14.2MB / 8.1MB",
        "BlockIO": "1.5GB / 250MB",
        "PIDs": "7",
    }

    assert parse_docker_stat(row) == DockerStat(
        name="worker-dxf-split",
        cpu_percent=125.5,
        memory_used_bytes=1_342_177_280,
        memory_limit_bytes=66_571_993_088,
        memory_percent=2.02,
        network_rx_bytes=14_200_000,
        network_tx_bytes=8_100_000,
        block_read_bytes=1_500_000_000,
        block_write_bytes=250_000_000,
        pids=7,
    )


@pytest.mark.parametrize(
    "requested, expected",
    [(0.01, 0.25), (0.25, 0.25), (5.0, 5.0), (120.0, 60.0)],
)
def test_resource_sampler_bounds_sampling_interval(
    requested: float,
    expected: float,
) -> None:
    assert clamp_sampling_interval(requested) == expected


def test_resource_sampler_summary_uses_monotonic_samples_and_peaks() -> None:
    samples = [
        {
            "monotonic_seconds": 10.0,
            "host": {
                "cpu_percent": 20.0,
                "memory_used_bytes": 100,
                "swap_used_bytes": 0,
            },
            "mysql": {"threads_connected": 3},
            "jobs": {"running": 1, "queued": 2},
            "containers": {
                "worker-dxf-split": {
                    "cpu_percent": 110.0,
                    "memory_used_bytes": 50,
                    "restart_count": 0,
                    "oom_killed": False,
                }
            },
        },
        {
            "monotonic_seconds": 12.0,
            "host": {
                "cpu_percent": 85.0,
                "memory_used_bytes": 150,
                "swap_used_bytes": 16,
            },
            "mysql": {"threads_connected": 11},
            "jobs": {"running": 4, "queued": 7},
            "containers": {
                "worker-dxf-split": {
                    "cpu_percent": 245.0,
                    "memory_used_bytes": 90,
                    "restart_count": 1,
                    "oom_killed": False,
                }
            },
        },
    ]

    summary = summarize_samples(samples)

    assert summary["sample_count"] == 2
    assert summary["duration_seconds"] == 2.0
    assert summary["peaks"] == {
        "host_cpu_percent": 85.0,
        "host_memory_used_bytes": 150,
        "host_swap_used_bytes": 16,
        "mysql_threads_connected": 11,
        "jobs_running": 4,
        "jobs_queued": 7,
    }
    assert summary["containers"]["worker-dxf-split"] == {
        "peak_cpu_percent": 245.0,
        "peak_memory_used_bytes": 90,
        "max_restart_count": 1,
        "oom_killed": False,
    }


def test_resource_sampler_summary_rejects_non_monotonic_timestamps() -> None:
    with pytest.raises(ValueError, match="单调"):
        summarize_samples(
            [
                {"monotonic_seconds": 2.0},
                {"monotonic_seconds": 1.0},
            ]
        )


def test_workflow_load_selects_dwg_files_deterministically(tmp_path: Path) -> None:
    for name in ["B.dwg", "a.DWG", "notes.txt", "c.dwg"]:
        (tmp_path / name).write_bytes(name.encode())

    selected = select_dwg_files(tmp_path, limit=2)

    assert [path.name for path in selected] == ["a.DWG", "B.dwg"]
    with pytest.raises(ValueError, match="只有 3"):
        select_dwg_files(tmp_path, limit=4)


def test_workflow_load_summarizes_phase_latency_and_failure() -> None:
    success = ProjectRunResult(
        name="success",
        succeeded=True,
        workflow_id=1,
        counts=None,
        archive_dxf_count=0,
        job_attempts={},
        request_ids=(),
        elapsed_seconds=10,
        phase_seconds={"split": 4, "upload": 2},
    )
    failure = ProjectRunResult(
        name="failure",
        succeeded=False,
        workflow_id=2,
        counts=None,
        archive_dxf_count=0,
        job_attempts={},
        request_ids=("req-2",),
        elapsed_seconds=20,
        phase_seconds={"split": 8, "upload": 4},
        error_code="HTTP_500",
        error_message="服务器错误",
        error_stage="拆板",
        http_status=500,
    )

    summary = summarize_project_results("concurrency-2", [success, failure])

    assert summary["name"] == "concurrency-2"
    assert summary["project_count"] == 2
    assert summary["succeeded_count"] == 1
    assert summary["failed_count"] == 1
    assert summary["elapsed_seconds"]["p50"] == 15
    assert summary["phase_seconds"]["split"] == {"p50": 6, "p95": 7.8}
    assert summary["error_codes"] == {"HTTP_500": 1}
