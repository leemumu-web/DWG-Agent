from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
import shutil

import ezdxf
import pytest

from steel_dxf_split.bh_compiler import BHCompilationRejected, compile_bh_document
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.bh_manufacturing_ir import EvidenceState
from steel_dxf_split.dxf_io import load_document
from steel_dxf_split.pipeline import SplitOptions, split_dxf


ROOT = Path(__file__).resolve().parents[2]
PAIR_DIR = ROOT / "samples" / "bh_pairs"


def _install_missing_manufacturing_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import steel_dxf_split.bh_passes as passes

    original_build = passes.build_bh_manufacturing_ir

    def build_with_missing_provenance(*args, **kwargs):
        manufacturing = original_build(*args, **kwargs)
        web = manufacturing.plates[0]
        segment = web.outer_segments[0]
        changed_evidence = replace(
            segment.evidence,
            state=EvidenceState.MISSING,
            source_ids=(),
            rule_ids=(),
        )
        changed_segment = replace(segment, evidence=changed_evidence)
        changed_web = replace(
            web,
            outer_segments=(changed_segment, *web.outer_segments[1:]),
        )
        return replace(
            manufacturing,
            plates=(changed_web, *manufacturing.plates[1:]),
        )

    monkeypatch.setattr(
        passes,
        "build_bh_manufacturing_ir",
        build_with_missing_provenance,
    )


def test_compile_result_exposes_the_evidence_backed_manufacturing_contract() -> None:
    source = PAIR_DIR / "3b2-cb-86_拆板前.dxf"

    result = compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )

    assert result.source_ir.entities
    assert result.frame_result.selected.canonical_signature
    assert result.drawing_graph.nodes
    assert result.manufacturing_ir.plates
    assert result.manufacturing_validation.ok
    assert (
        result.fingerprints["manufacturing_ir"]
        == result.manufacturing_ir.fingerprint
    )
    assert result.fingerprints["algorithm"] == "sha256-canonical-json-v3"
    assert (
        result.manufacturing_validation.values["fingerprint"]
        == result.manufacturing_ir.fingerprint
    )
    obligation = next(
        item
        for item in result.proof_report.obligations
        if item.obligation_id == "BH.PROOF.MANUFACTURING_IR.PROVENANCE"
    )
    assert obligation.status.value == "pass"
    json.dumps(
        {
            "source_ir": result.source_ir.to_dict(),
            "canonical_frames": result.frame_result.to_dict(),
            "drawing_graph": result.drawing_graph.to_dict(),
            "proof_report": result.proof_report.to_dict(),
            "manufacturing_ir": result.manufacturing_ir.to_dict(),
        },
        ensure_ascii=False,
    )
    stages = [item.name for item in result.trace.stages]
    assert stages.index("manufacturing.validate_assembly") < stages.index(
        "manufacturing.freeze_ir_and_prove"
    ) < stages.index("quality.route")


def test_validation_and_proofs_cover_the_frozen_manufacturing_ir() -> None:
    source = PAIR_DIR / "3b2-cb-86_拆板前.dxf"

    result = compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )

    checks = result.manufacturing_validation.checks
    assert checks["feature_provenance_complete"] is True
    assert checks["role_provenance_complete"] is True
    assert checks["geometry_matches_writer_assembly"] is True
    proof = next(
        item
        for item in result.proof_report.obligations
        if item.obligation_id == "BH.PROOF.MANUFACTURING_IR.PROVENANCE"
    )
    assert proof.status.value == "pass"
    assert proof.evidence[0].source_ids


@pytest.mark.skipif(
    os.name == "nt",
    reason="BH v1.5.2 production preview requires the Linux Worker CJK font set",
)
def test_report_schema_12_exports_source_search_proofs_and_manufacturing_ir(
    tmp_path: Path,
) -> None:
    from steel_dxf_split import __version__

    source = PAIR_DIR / "2b1-cb-26_拆板前.dxf"

    result = split_dxf(
        source,
        tmp_path,
        SplitOptions(source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT),
    )
    report = result.report

    assert __version__ == "1.5.2"
    assert report["version"] == "1.5.2"
    assert report["report_schema"] == "BH-COMPILATION-REPORT-1.4"
    assert report["source_ir"]["entities"]
    assert report["canonical_frames"]["candidates"]
    assert report["drawing_graph"]["nodes"]
    assert report["search_status"]["search_complete"] is True
    assert report["proof_report"]["obligations"]
    assert report["manufacturing_ir"]["fingerprint"] == report[
        "semantic_fingerprints"
    ]["manufacturing_ir"]
    assert report["manufacturing_ir_validation"]["ok"] is True
    assert all(
        plate["weld_allowance_contract"]["coordinate_unit"] == "mm"
        for plate in report["manufacturing_ir"]["plates"]
    )
    assert report["capabilities"]["production_member_axis"] == "horizontal_x"
    assert report["capabilities"]["ground_truth_used_for_decision"] is False
    json.dumps(report, ensure_ascii=False)


@pytest.mark.skipif(
    os.name == "nt",
    reason="BH v1.5.2 production preview requires the Linux Worker CJK font set",
)
def test_unified_cli_routes_native_review_without_creating_production_output(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from steel_dxf_split.cli import main

    _install_missing_manufacturing_provenance(monkeypatch)
    source = PAIR_DIR / "2b1-cb-26_拆板前.dxf"
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    shutil.copy2(source, input_dir / source.name)
    output = tmp_path / "output"

    code = main(
        [
            str(input_dir),
            "--output-dir",
            str(output),
            "--authorize-tekla-bh-single-part-profile",
            DEFAULT_TEKLA_BH_SOURCE_CONTRACT.export_profile,
        ]
    )

    assert code == 1
    summary = json.loads(capsys.readouterr().out)[0]
    assert summary["automation_route"] == "manual_review"
    assert summary["native_automation_route"] == "review_required"
    assert summary["production_clean"] is None
    assert summary["proof_disposition"] == "review_required"
    assert summary["search_complete"] is True
    assert len(summary["manufacturing_fingerprint"]) == 64
    report = json.loads(Path(summary["report"]).read_text(encoding="utf-8"))
    assert report["outputs"]["production_clean"] is None
    assert Path(report["outputs"]["review_candidate"]).exists()
    assert not list(output.rglob("*_自动拆板_清洁1to1.dxf"))


@pytest.mark.skipif(
    os.name == "nt",
    reason="BH v1.5.2 production preview requires the Linux Worker CJK font set",
)
def test_cli_separates_full_processing_time_from_compiler_pass_time(
    tmp_path: Path,
    capsys,
) -> None:
    from steel_dxf_split.cli import main

    source = PAIR_DIR / "2b1-cb-26_拆板前.dxf"
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    shutil.copy2(source, input_dir / source.name)
    code = main(
        [
            str(input_dir),
            "--output-dir",
            str(tmp_path / "output"),
            "--authorize-tekla-bh-single-part-profile",
            DEFAULT_TEKLA_BH_SOURCE_CONTRACT.export_profile,
        ]
    )

    assert code == 0
    timing = json.loads(capsys.readouterr().out)[0]["timing"]
    assert timing["clock"] == "time.perf_counter"
    assert timing["measurement"] == "monotonic_wall_clock"
    assert 0.0 < timing["compiler_pass_seconds"]
    assert 0.0 < timing["preview_render_seconds"]
    assert timing["preview_render_seconds"] <= timing["processing_seconds"]
    assert timing["compiler_pass_seconds"] <= timing["processing_seconds"]
    assert timing["compiler_pass_scope"] == "eight_compiler_passes"
    assert timing["processing_scope"] == (
        "authorized_split_call_through_persisted_report"
    )


def test_writer_coordinate_drift_cannot_leave_a_trusted_production_dxf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import steel_dxf_split.bh_pipeline as pipeline

    source = PAIR_DIR / "2b1-cb-26_拆板前.dxf"
    output = tmp_path / "output"
    original_write = pipeline.write_bh_clean

    def write_then_corrupt(assembly, output_path, **kwargs):
        layout = original_write(assembly, output_path, **kwargs)
        doc = ezdxf.readfile(output_path)
        contour = doc.modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']")[0]
        contour.set_points(
            [
                (float(point[0]) + 10.0, float(point[1]), float(point[2]))
                for point in contour.get_points("xyb")
            ],
            format="xyb",
        )
        doc.saveas(output_path)
        return layout

    monkeypatch.setattr(pipeline, "write_bh_clean", write_then_corrupt)

    with pytest.raises(ValueError, match="saved DXF validation failed"):
        split_dxf(
            source,
            output,
            SplitOptions(source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT),
        )

    assert not list(output.rglob("*_自动拆板_清洁1to1.dxf"))


def test_rejected_ir_writer_conflict_retains_full_audit_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import steel_dxf_split.bh_passes as passes

    source = PAIR_DIR / "2b1-cb-26_拆板前.dxf"
    original_build = passes.build_bh_manufacturing_ir

    def build_with_drift(*args, **kwargs):
        manufacturing = original_build(*args, **kwargs)
        web = manufacturing.plates[0]
        segment = web.outer_segments[0]
        changed_segment = replace(
            segment,
            end=(segment.end[0] + 10.0, segment.end[1]),
        )
        changed_web = replace(
            web,
            outer_segments=(changed_segment, *web.outer_segments[1:]),
        )
        return replace(
            manufacturing,
            plates=(changed_web, *manufacturing.plates[1:]),
        )

    monkeypatch.setattr(passes, "build_bh_manufacturing_ir", build_with_drift)

    with pytest.raises(BHCompilationRejected) as caught:
        compile_bh_document(
            load_document(source),
            source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
            source_path=source,
        )
    assert caught.value.manufacturing_ir is not None
    assert caught.value.manufacturing_validation is not None
    assert caught.value.manufacturing_validation.ok is False
    assert "BH-MANUFACTURING-IR-CONTRACT-MISMATCH" in (
        caught.value.diagnostic_codes
    )

    result = split_dxf(
        source,
        tmp_path / "output",
        SplitOptions(source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT),
    )
    assert result.report["automation_route"] == "manual_review"
    assert result.report["native_automation_route"] == "rejected"
    assert result.report["source_ir"]["entities"]
    assert result.report["manufacturing_ir"]["plates"]
    assert result.report["manufacturing_ir_validation"]["ok"] is False
    assert result.report["outputs"]["production_clean"] is None


@pytest.mark.skipif(
    os.name == "nt",
    reason="BH v1.5.2 production preview requires the Linux Worker CJK font set",
)
def test_missing_manufacturing_provenance_routes_to_review_not_production(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = PAIR_DIR / "2b1-cb-26_拆板前.dxf"
    _install_missing_manufacturing_provenance(monkeypatch)

    compiled = compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )
    assert compiled.assessment.disposition.value == "review_required"
    assert compiled.manufacturing_validation.checks[
        "geometry_matches_writer_assembly"
    ]
    assert not compiled.manufacturing_validation.checks[
        "feature_provenance_complete"
    ]

    result = split_dxf(
        source,
        tmp_path / "output",
        SplitOptions(source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT),
    )
    assert result.production_path is None
    assert (
        result.review_candidate_path is not None
        and result.review_candidate_path.exists()
    )
    assert result.report["automation_route"] == "manual_review"
    assert result.report["native_automation_route"] == "review_required"
    assert result.report["outputs"]["production_clean"] is None
    assert result.report["proof_report"]["disposition"] == "review_required"
