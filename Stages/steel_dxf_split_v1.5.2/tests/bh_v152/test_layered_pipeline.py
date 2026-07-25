import os
from pathlib import Path
from xml.etree import ElementTree

import ezdxf
import pytest

import steel_dxf_split.layered_pipeline as layered_pipeline
from steel_dxf_split.bh_trace import STAGE_REGISTRY
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.layered_pipeline import inspect_bh_pair
from steel_dxf_split.layered_site import build_site, validate_site_links
from steel_dxf_split.pipeline import SplitOptions, split_dxf


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / "samples" / "bh_pairs"
REFERENCE_DIR = ROOT / "samples" / "bh_pairs"

CAPABILITY_CASES = {
    "3b1-cb-15": "holeless_web_selection",
    "3b2-cb-86": "flange_cut_ownership",
    "h-3-cb-53": "web_boundary_completion",
    "h-6-cb-9": "web_micro_regularization",
    "z-4-cb-42": "arc_chain_recovery",
    "2b1-cb-40": "flange_development",
}


def test_inspect_pair_emits_all_fixed_stages_and_supervision(tmp_path: Path) -> None:
    result = inspect_bh_pair(
        SOURCE_DIR / "2b1-cb-18_拆板前.dxf",
        REFERENCE_DIR / "2b1-cb-18_拆板后.dxf",
        tmp_path,
    )
    assert result.ok
    assert result.supervision["ok"]
    assert set(result.stage_status) == {
        item.stage_id
        for item in STAGE_REGISTRY
        if item.stage_id != "13_corpus_summary"
    }
    assert result.manifest_validation.ok
    assert (tmp_path / result.final_dxf_path).exists()
    assert (tmp_path / result.final_svg_path).exists()
    assert (tmp_path / result.reference_dxf_path).exists()
    assert (tmp_path / result.reference_svg_path).exists()
    assert result.final_dxf_path.parts[:2] == ("dxf", "final")
    assert result.final_svg_path.parts[:2] == ("svg", "final")
    assert result.reference_dxf_path.parts[:2] == ("dxf", "reference")
    assert result.reference_svg_path.parts[:2] == ("svg", "reference")
    assert all(not path.is_absolute() for path in result.all_paths())
    build_site({"samples": [result.to_manifest()]}, tmp_path / "site")
    sample_page = (tmp_path / "site/samples/2b1-cb-18/index.html").read_text(
        encoding="utf-8"
    )
    assert sample_page.count('class="artifact-card') == len(result.artifacts)
    assert validate_site_links(tmp_path / "site", tmp_path).ok


@pytest.mark.skipif(os.name == "nt", reason="Production preview rendering is Linux-only")
def test_layered_observation_has_zero_byte_and_metric_impact(tmp_path: Path) -> None:
    source = SOURCE_DIR / "2b1-cb-18_拆板前.dxf"
    reference = REFERENCE_DIR / "2b1-cb-18_拆板后.dxf"
    baseline = split_dxf(
        source,
        tmp_path / "baseline",
        SplitOptions(
            manual_reference_path=reference,
            source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        ),
    )
    layered = inspect_bh_pair(source, reference, tmp_path / "layered")
    assert baseline.production_path is not None
    assert baseline.review_candidate_path is None
    assert baseline.production_path.read_bytes() == (
        tmp_path / "layered" / layered.final_dxf_path
    ).read_bytes()
    assert (
        baseline.report["semantic_fingerprints"]["manufacturing_ir"]
        == layered.manufacturing_fingerprint
    )
    assert (
        baseline.report["hypothesis_solver"]["selected_hypothesis_id"]
        == layered.selected_hypothesis
    )
    baseline_supervision = baseline.report["supervised_comparison"]
    assert baseline_supervision["checks"] == layered.supervision["checks"]
    baseline_values = dict(baseline_supervision["values"])
    layered_values = dict(layered.supervision["values"])
    baseline_values.pop("manual_reference")
    layered_values.pop("manual_reference")
    assert baseline_values == layered_values


def test_profile_authorized_development_matches_manual_after_freeze(
    tmp_path: Path,
) -> None:
    result = inspect_bh_pair(
        SOURCE_DIR / "2b1-cb-40_拆板前.dxf",
        REFERENCE_DIR / "2b1-cb-40_拆板后.dxf",
        tmp_path,
    )

    assert result.ok
    assert result.proof_disposition == "auto_accept"
    assert result.supervision_gate_applicable is True
    assert result.supervision_gate_passed is True
    assert result.supervision["ok"] is True
    assert "自动拆板" in result.final_dxf_path.name
    assert "清洁" in result.final_dxf_path.name


def test_layered_tool_reads_manual_bytes_only_after_manufacturing_ir_freeze(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    original_compile = layered_pipeline.compile_bh_document
    original_hash = layered_pipeline._file_sha256

    def tracked_compile(*args, **kwargs):
        order.append("compile_started")
        result = original_compile(*args, **kwargs)
        order.append("manufacturing_ir_frozen")
        return result

    def tracked_hash(path: Path) -> str:
        if path.name.endswith("_拆板后.dxf"):
            order.append("manual_hash_read")
        return original_hash(path)

    monkeypatch.setattr(layered_pipeline, "compile_bh_document", tracked_compile)
    monkeypatch.setattr(layered_pipeline, "_file_sha256", tracked_hash)

    result = inspect_bh_pair(
        SOURCE_DIR / "2b1-cb-18_拆板前.dxf",
        REFERENCE_DIR / "2b1-cb-18_拆板后.dxf",
        tmp_path,
    )

    assert order.index("manufacturing_ir_frozen") < order.index("manual_hash_read")
    assert result.supervision["manual_read_phase"] == (
        "after_manufacturing_ir_freeze"
    )


@pytest.mark.parametrize(("stem", "artifact_id"), CAPABILITY_CASES.items())
def test_layered_artifacts_cover_semantic_capability(
    stem: str, artifact_id: str, tmp_path: Path
) -> None:
    output_root = tmp_path / stem
    result = inspect_bh_pair(
        SOURCE_DIR / f"{stem}_拆板前.dxf",
        REFERENCE_DIR / f"{stem}_拆板后.dxf",
        output_root,
    )
    item = next(
        item
        for item in result.artifacts
        if item.artifact_id == artifact_id
        and item.status not in {"not_applicable", "failed"}
    )
    assert not ezdxf.readfile(output_root / item.dxf_path).audit().errors
    assert ElementTree.parse(output_root / item.svg_path).getroot().tag.endswith("svg")
    build_site({"samples": [result.to_manifest()]}, output_root / "site")
    page = (output_root / "site/samples" / stem / "index.html").read_text(
        encoding="utf-8"
    )
    assert artifact_id in page
    assert validate_site_links(output_root / "site", output_root).ok
