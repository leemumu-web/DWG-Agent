from __future__ import annotations

from dataclasses import fields
import inspect

from steel_dxf_split.box.compiler import BoxCompileConfig
from steel_dxf_split.box import compiler
from tests.box_v1.paths import ROOT


def test_production_api_has_no_manual_reference_channel() -> None:
    assert {field.name for field in fields(BoxCompileConfig)} == {
        "output_dir",
        "source_contract",
        "report_path",
        "require_auto_accept",
        "release_attestation_path",
        "source_limits",
    }


def test_box_compiler_has_no_manual_reference_code_path() -> None:
    source = inspect.getsource(compiler).lower()
    assert "manual_reference" not in source
    assert "reference_dir" not in source


def test_production_package_contains_no_manual_reference_knowledge() -> None:
    source_files = tuple((ROOT / "src/steel_dxf_split/box").glob("*.py"))
    assert source_files
    forbidden = ("manual_reference", "manual_references")
    violations = {
        path.name: token
        for path in source_files
        for token in forbidden
        if token in path.read_text(encoding="utf-8").lower()
    }
    assert violations == {}
