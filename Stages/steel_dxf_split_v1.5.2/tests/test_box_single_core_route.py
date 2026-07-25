from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

import steel_dxf_split.pipeline as pipeline
from steel_dxf_split import cli
from steel_dxf_split.box import compiler
from steel_dxf_split.box.contracts import BoxSourceContract
from steel_dxf_split.pipeline import SplitOptions, split_dxf

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "samples"
    / "box_pairs"
    / "BOX_拆板前_dxf"
    / "2b1-cb-56_拆板前.dxf"
)


def _option_strings(parser) -> set[str]:
    return {
        option
        for action in parser._actions
        for option in action.option_strings
    }


def test_split_options_has_one_box_core_contract() -> None:
    fields = SplitOptions.__dataclass_fields__
    assert "box_backend" not in fields
    assert "box_supervised_gate_proof" not in fields
    assert {"box_source_contract", "box_release_attestation"} <= set(fields)


def test_unified_cli_has_no_backend_or_legacy_gate_flag() -> None:
    options = _option_strings(cli.build_parser())
    assert "--box-backend" not in options
    assert "--box-supervised-gate-proof" not in options
    assert "--box-release-attestation" in options
    assert "--authorize-tekla-box-single-part-profile" in options


def test_box_route_imports_only_internal_compiler() -> None:
    source = inspect.getsource(pipeline)
    assert "box_v2_pipeline" not in source
    assert "box_pipeline" not in source
    assert "box_backend" not in source
    assert "from .box.compiler import" in source
    assert "compile_box" in source


def test_box_route_propagates_contract_release_and_auto_accept(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_path = tmp_path / "release.json"
    captured: dict[str, object] = {}
    expected_report = {
        "profile_family": "BOX",
        "automation_route": "auto_accepted",
        "single_file_disposition": "auto_accept",
        "proof_report": {"disposition": "auto_accept", "obligations": []},
        "outputs": {"previews": None},
        "search_status": {"search_complete": True},
        "manufacturing_ir": {"fingerprint": "a" * 64},
        "timing": {"preview_render_seconds": 0.0},
    }
    expected_report_path = tmp_path / "report.json"

    def fake_compile_box(input_path: Path, *, config):
        captured["input_path"] = input_path
        captured["config"] = config
        return SimpleNamespace(
            production_path=tmp_path / "clean.dxf",
            review_path=None,
            report_path=expected_report_path,
            report=expected_report,
        )

    monkeypatch.setattr(compiler, "compile_box", fake_compile_box)
    monkeypatch.setattr(
        pipeline,
        "_publish_successful_pair",
        lambda result, **_kwargs: result,
    )
    contract = BoxSourceContract()
    result = split_dxf(
        SOURCE,
        tmp_path / "output",
        SplitOptions(
            box_source_contract=contract,
            box_release_attestation=release_path,
        ),
    )

    config = captured["config"]
    assert captured["input_path"] == SOURCE
    assert config.source_contract is contract
    assert config.release_attestation_path == release_path
    assert config.require_auto_accept is False
    assert result.clean_path == tmp_path / "clean.dxf"
    assert result.review_path is None
    assert result.sheet_path is None
    assert result.report is expected_report


def test_box_route_requires_explicit_source_contract(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"

    with pytest.raises(ValueError, match="source contract"):
        split_dxf(SOURCE, output, SplitOptions())

    assert not list(output.rglob("*"))
