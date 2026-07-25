from __future__ import annotations

from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "steel_dxf_split" / "box"


def test_internal_box_core_declares_v1_release_and_current_patchset() -> None:
    provenance = import_module("steel_dxf_split.box.provenance")

    assert PACKAGE.is_dir()
    assert provenance.BOX_CORE_VERSION == "1.0.0"
    assert provenance.BOX_CORE_TAG == "v1.0.0"
    assert (
        provenance.BOX_CORE_COMMIT
        == "5a2be1a82eb7235bcff62d97a13d2937f9ad026b"
    )
    assert (
        provenance.BOX_CORE_PATCHSET_ID
        == "box-paired-output-and-hole-color-2026-07-23"
    )
    assert provenance.BOX_CORE_PATCHED_FILES == (
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
    )


def test_internal_box_core_has_no_external_distribution_import() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in PACKAGE.rglob("*.py")
    )

    assert "from box_dxf_split" not in source
    assert "import box_dxf_split" not in source
