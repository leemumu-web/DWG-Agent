from __future__ import annotations

import subprocess
import sys

from tests.support.paths import REPO_ROOT


def test_documentation_consistency_gate_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/docs/check.py")],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_frontend_env_example_uses_fixed_local_backend_port() -> None:
    content = (REPO_ROOT / "frontend/.env.example").read_text(encoding="utf-8")

    assert "http://127.0.0.1:8010" in content
    assert "http://127.0.0.1:8000" not in content


def test_linux_production_workflow_documentation_matches_public_routes() -> None:
    workflow = (REPO_ROOT / "docs/architecture/workflow.md").read_text(encoding="utf-8")
    architecture = (REPO_ROOT / "docs/architecture/overview.md").read_text(encoding="utf-8")
    pipelines = (REPO_ROOT / "docs/architecture/implementation-status.md").read_text(
        encoding="utf-8"
    )
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    combined = "\n".join((workflow, architecture, pipelines, readme))

    assert "linux_production" in workflow
    assert "POST /api/v1/workflows/{workflow_id}/artifacts" in workflow
    assert (
        "POST /api/v1/workflows/{workflow_id}/stages/{stage_code}/executions"
        in workflow
    )
    for stage in (
        "source_intake",
        "dxf_classification",
        "drawing_processing",
        "excel_stage1",
        "design_barrier",
        "cam_packaging",
        "windows_cam",
        "result_acceptance",
        "delivery_archive",
    ):
        assert stage in workflow
    assert "| 6 | `excel_final`" not in workflow
    assert "DXF→Excel 只保留为独立转换工具" in workflow
    assert "WORKFLOW_STAGE_NOT_IMPLEMENTED" in workflow
    assert "不会自动创建 Excel Final Job" not in combined
    assert "公开 workflow route 没有调用 Job 绑定或产物挂接函数" not in combined
