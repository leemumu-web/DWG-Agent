from __future__ import annotations

import ast
from pathlib import Path
import tomllib

import steel_dxf_split.box as box_package


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "steel_dxf_split"
BOX_PACKAGE = PACKAGE / "box"

RETIRED_BOX_ORCHESTRATION_FILES = (
    "batch_cli.py",
    "cli.py",
    "pipeline.py",
)

LEGACY_BOX_FILES = (
    "box_compiler.py",
    "box_delivery_ir.py",
    "box_delivery_validator.py",
    "box_delivery_writer.py",
    "box_facts.py",
    "box_geometry_ir.py",
    "box_geometry_roles.py",
    "box_manufacturing.py",
    "box_metadata.py",
    "box_pipeline.py",
    "box_projection.py",
    "box_reconstruction.py",
    "box_region.py",
    "box_release.py",
    "box_solver.py",
    "box_supervision.py",
    "box_supervision_cli.py",
    "box_text_evidence.py",
    "box_v2_backend.py",
    "box_v2_pipeline.py",
    "box_validator.py",
    "box_view_ir.py",
    "box_writer.py",
)

BOX_INTEGRATION_FILES = (
    PACKAGE / "pipeline.py",
    BOX_PACKAGE / "analysis.py",
    BOX_PACKAGE / "compiler.py",
    BOX_PACKAGE / "contracts.py",
    BOX_PACKAGE / "delivery.py",
    BOX_PACKAGE / "frontend.py",
    BOX_PACKAGE / "manufacturing.py",
    BOX_PACKAGE / "release.py",
    BOX_PACKAGE / "solve.py",
    BOX_PACKAGE / "validation.py",
)

FORBIDDEN_BOX_INTEGRATION_TOKENS = (
    "SplitAssembly",
    "box_backend",
    "box_v2_backend",
    "box_v2_pipeline",
    "box_pipeline",
    "box_compiler",
    "box_solver",
    "box_reconstruction",
    "box_dxf_split",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_only_internal_box_package_remains() -> None:
    assert BOX_PACKAGE.is_dir()
    assert {
        name for name in LEGACY_BOX_FILES if (PACKAGE / name).exists()
    } == set()


def test_box_is_an_internal_core_without_a_second_public_split_entrypoint() -> None:
    assert {
        name
        for name in RETIRED_BOX_ORCHESTRATION_FILES
        if (BOX_PACKAGE / name).exists()
    } == set()
    assert not hasattr(box_package, "split_dxf")
    assert not hasattr(box_package, "SplitOptions")
    assert not hasattr(box_package, "SplitResult")


def test_production_box_call_graph_contains_no_legacy_nodes() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in BOX_INTEGRATION_FILES
    )

    assert {
        token
        for token in FORBIDDEN_BOX_INTEGRATION_TOKENS
        if token in sources
    } == set()
    assert "from .box.compiler import BoxCompileConfig, compile_box" in sources


def test_internal_box_package_does_not_import_main_project_legacy_domains() -> None:
    violations: dict[str, list[str]] = {}
    for path in sorted(BOX_PACKAGE.glob("*.py")):
        forbidden = sorted(
            module
            for module in _imports(path)
            if module.startswith("steel_dxf_split.models")
            or module.startswith("steel_dxf_split.extractor")
            or module.startswith("box_dxf_split")
        )
        if forbidden:
            violations[path.name] = forbidden

    assert violations == {}


def test_bh_domain_modules_do_not_import_box_domains() -> None:
    violations: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.glob("bh_*.py")):
        forbidden = sorted(
            module
            for module in _imports(path)
            if module == "box" or module.startswith("box.")
            or ".box." in module
        )
        if forbidden:
            violations[path.name] = forbidden

    assert violations == {}


def test_packaging_has_no_external_box_distribution_or_legacy_entrypoint() -> None:
    project = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]

    assert not any(
        dependency.lower().startswith("box-dxf-split")
        for dependency in project["dependencies"]
    )
    assert "steel-dxf-box-supervision" not in project["scripts"]
