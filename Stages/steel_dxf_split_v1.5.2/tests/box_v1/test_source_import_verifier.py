from __future__ import annotations

from importlib import import_module
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = Path(
    os.environ.get(
        "BOX_V1_UPSTREAM",
        r"D:\Documents\Codex\worktrees\box-dxf-split\v1.0.0",
    )
)


def test_source_import_matches_v1_baseline_and_declared_hotfix_patchset() -> None:
    verifier = import_module("scripts.verify_box_v1_source")
    report = verifier.verify_source_import(
        upstream_root=UPSTREAM,
        project_root=ROOT,
    )

    assert report["tag"] == "v1.0.0"
    assert report["commit"] == "5a2be1a82eb7235bcff62d97a13d2937f9ad026b"
    assert report["schema"] == "BOX-V1-SOURCE-IMPORT-1.2"
    assert (
        report["patchset_id"]
        == "box-view-preprocessing-hole-color-unified-part-mark-and-role-marks-2026-07-24"
    )
    assert report["matched"] == 13
    assert {item["path"] for item in report["patched"]} == {
        "artifact_io.py",
        "assembly.py",
        "course_graph.py",
        "flange_solver.py",
        "projection_geometry.py",
        "source_ir.py",
        "validator.py",
        "view_solver.py",
        "web_solver.py",
        "weld_allowance.py",
        "writer.py",
    }
    assert report["missing"] == []
    assert report["changed"] == []
    assert {item["path"] for item in report["adapted"]} == {"__init__.py"}
    assert {item["path"] for item in report["retired"]} == {
        "batch_cli.py",
        "cli.py",
        "pipeline.py",
        "weld_allowance_cli.py",
        "weld_allowance_release.py",
    }
    assert "provenance.py" in report["integration_files"]
    assert "view_preprocessing.py" in report["integration_files"]
    assert report["unexpected"] == []
    assert report["ok"]


def test_source_comparison_is_independent_of_windows_line_endings(
    tmp_path: Path,
) -> None:
    verifier = import_module("scripts.verify_box_v1_source")
    lf_source = tmp_path / "lf.py"
    crlf_source = tmp_path / "crlf.py"
    lf_source.write_bytes(b"from __future__ import annotations\n")
    crlf_source.write_bytes(b"from __future__ import annotations\r\n")

    assert verifier._normalized_source(lf_source) == verifier._normalized_source(
        crlf_source
    )
    assert verifier._matches_declared_source_digest(
        crlf_source,
        verifier._sha256(lf_source),
    )
