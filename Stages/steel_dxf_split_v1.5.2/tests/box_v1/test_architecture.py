from __future__ import annotations

from pathlib import Path

from tests.box_v1.paths import ROOT


FORBIDDEN = (
    "SplitAssembly",
    "steel_dxf_split.models.PlateRole",
    "box_v2_backend",
    "box_v2_pipeline",
)


def test_internal_v1_core_has_no_legacy_main_project_dependencies() -> None:
    roots = (
        ROOT / "src/steel_dxf_split/box",
        ROOT / "tests/box_v1",
    )
    current = Path(__file__).resolve()
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for root in roots
        for path in sorted(root.rglob("*.py"))
        if path.resolve() != current
    )

    assert {token for token in FORBIDDEN if token in source} == set()
