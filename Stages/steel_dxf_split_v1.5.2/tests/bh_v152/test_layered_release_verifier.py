import json
from pathlib import Path

from scripts.bh.verify_layered_release import SAMPLE_STAGE_IDS, verify_layered_release


def test_independent_release_stage_contract_matches_fixed_public_ids() -> None:
    assert SAMPLE_STAGE_IDS == (
        "00_input_provenance",
        "01_frontend_fact_ir",
        "02_annotation_facts",
        "03_metadata_semantics",
        "04_view_hypothesis_frontier",
        "05_candidate_lowering",
        "06_constraints_and_selection",
        "07_assembly_validation",
        "08_manufacturing_ir",
        "09_quality_route",
        "10_codegen_layout",
        "11_saved_output_validation",
        "12_manual_supervision",
    )


def test_release_verifier_reports_every_incomplete_dimension(tmp_path: Path) -> None:
    pair_dir = tmp_path / "pairs"
    run_dir = tmp_path / "run"
    pair_dir.mkdir()
    run_dir.mkdir()
    (pair_dir / "beam-a_拆板前.dxf").write_bytes(b"0\nEOF\n")
    (pair_dir / "beam-b_拆板前.dxf").write_bytes(b"0\nEOF\n")
    manifest = {
        "samples": [
            {
                "sample_id": "beam-a",
                "stage_status": {"00_input_provenance": "observed"},
                "artifacts": [
                    {"dxf_path": "dxf/intermediate/beam-a/00_input/a.dxf"}
                ],
                "supervision": {"ok": False},
                "saved_validation": {"ok": False},
            }
        ]
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    report = verify_layered_release(pair_dir, run_dir)
    codes = {item["code"] for item in report["errors"]}
    assert {
        "MISSING_SAMPLE",
        "MISSING_STAGE",
        "MISSING_MIRROR",
        "DISPOSITION_INVALID",
        "FINAL_VALIDATION_FAILED",
    }.issubset(codes)
    assert report["release_ready"] is False


def test_review_only_manual_difference_is_not_a_supervision_gate_failure(
    tmp_path: Path,
) -> None:
    pair_dir = tmp_path / "pairs"
    run_dir = tmp_path / "run"
    pair_dir.mkdir()
    run_dir.mkdir()
    (pair_dir / "beam-a_拆板前.dxf").write_bytes(b"0\nEOF\n")
    manifest = {
        "samples": [
            {
                "sample_id": "beam-a",
                "ok": True,
                "stage_status": {
                    stage_id: "observed" for stage_id in SAMPLE_STAGE_IDS
                },
                "artifacts": [],
                "proof_disposition": "review_required",
                "output_purpose": "review",
                "supervision_gate_applicable": False,
                "supervision_gate_passed": None,
                "supervision": {"ok": False},
                "saved_validation": {
                    "ok": True,
                    "generated_line_count": 0,
                    "checks": {"no_cross_or_helper_lines": True},
                },
                "final_dxf_path": "dxf/final/beam-a/beam-a_复核候选.dxf",
            }
        ]
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    report = verify_layered_release(pair_dir, run_dir)
    codes = {item["code"] for item in report["errors"]}

    assert "SUPERVISION_FAILED" not in codes
    assert "DISPOSITION_INVALID" not in codes
    assert "OUTPUT_ROUTE_INVALID" not in codes
    assert report["disposition_counts"]["review_required"] == 1


def test_release_verifier_reports_corrupt_formats_orphans_site_and_helper_lines(
    tmp_path: Path,
) -> None:
    pair_dir = tmp_path / "pairs"
    run_dir = tmp_path / "run"
    pair_dir.mkdir()
    run_dir.mkdir()
    (pair_dir / "beam-a_拆板前.dxf").write_bytes(b"0\nEOF\n")
    dxf = run_dir / "dxf/intermediate/beam-a/00_input/a.dxf"
    svg = run_dir / "svg/intermediate/beam-a/00_input/a.svg"
    dxf.parent.mkdir(parents=True)
    svg.parent.mkdir(parents=True)
    dxf.write_text("not a dxf", encoding="utf-8")
    svg.write_text("not an svg", encoding="utf-8")
    orphan = run_dir / "json/orphan.json"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("{}", encoding="utf-8")
    site = run_dir / "site"
    site.mkdir()
    (site / "index.html").write_text(
        '<a href="missing.html">broken</a>', encoding="utf-8"
    )
    manifest = {
        "samples": [
            {
                "sample_id": "beam-a",
                "stage_status": {},
                "artifacts": [
                    {
                        "artifact_id": "bad",
                        "category": "intermediate",
                        "dxf_path": str(dxf.relative_to(run_dir)),
                        "svg_path": str(svg.relative_to(run_dir)),
                    }
                ],
                "supervision": {"ok": True},
                "saved_validation": {
                    "ok": True,
                    "generated_line_count": 1,
                    "checks": {"no_cross_or_helper_lines": False},
                },
            }
        ]
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    report = verify_layered_release(pair_dir, run_dir)
    codes = {item["code"] for item in report["errors"]}
    assert {
        "DXF_PARSE_FAILED",
        "SVG_PARSE_FAILED",
        "ORPHAN_FILE",
        "MISSING_CORPUS_STAGE",
        "BROKEN_SITE_LINK",
        "FINAL_HELPER_LINE",
    }.issubset(codes)
