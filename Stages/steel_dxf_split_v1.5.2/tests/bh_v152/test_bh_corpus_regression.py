from __future__ import annotations

import json
import os
from pathlib import Path

import ezdxf
import pytest

from steel_dxf_split.bh_compare import compare_bh_to_manual
from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_corpus import load_corpus_manifest
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.bh_pipeline import split_bh_dxf
from steel_dxf_split.dxf_io import load_document


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / "samples" / "bh_pairs"
REFERENCE_DIR = ROOT / "samples" / "bh_pairs"
MANIFEST = ROOT / "tests" / "fixtures" / "bh_corpus.json"
CORPUS = load_corpus_manifest(
    MANIFEST,
    source_dir=SOURCE_DIR,
    reference_dir=REFERENCE_DIR,
)


def test_corpus_manifest_covers_all_twenty_pairs() -> None:
    manifest = CORPUS

    assert manifest.schema_version == "BH-CORPUS-1.0"
    assert len(manifest.cases) == 20
    assert {case.disposition for case in manifest.cases} == {"auto_accept"}
    assert sum(case.disposition == "auto_accept" for case in manifest.cases) == 20
    assert all(case.source_sha256 and case.manual_sha256 for case in manifest.cases)
    assert all(
        [plate.role for plate in case.physical_plates]
        == ["web", "upper_flange", "lower_flange"]
        for case in manifest.cases
    )


def _plate_bbox(plate: dict[str, object]) -> tuple[float, float]:
    segments = plate["outer_segments"]
    assert isinstance(segments, list) and segments
    xs = [
        float(point[0])
        for segment in segments
        for point in (segment["start"], segment["end"])
    ]
    ys = [
        float(point[1])
        for segment in segments
        for point in (segment["start"], segment["end"])
    ]
    return max(xs) - min(xs), max(ys) - min(ys)


@pytest.mark.parametrize("case", CORPUS.cases, ids=lambda case: case.sample_id)
def test_source_only_compiler_and_offline_manual_gate_match_frozen_corpus(
    case,
) -> None:
    source = case.source_path(SOURCE_DIR)
    compiled = compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )

    assert compiled.assessment.disposition.value == case.disposition
    assert list(compiled.proof_report.blocking_obligation_ids) == list(
        case.blocking_proof_ids
    )
    assert compiled.manufacturing_validation.ok is True
    comparison = compare_bh_to_manual(
        compiled.assembly,
        case.manual_path(REFERENCE_DIR),
    )
    assert comparison.ok is True


@pytest.mark.parametrize("case", CORPUS.cases, ids=lambda case: case.sample_id)
@pytest.mark.skipif(os.name == "nt", reason="Production preview rendering is Linux-only")
def test_source_first_compilation_matches_the_frozen_engineering_corpus(
    case,
    tmp_path: Path,
) -> None:
    production_path, review_candidate_path, _report_path, report = split_bh_dxf(
        case.source_path(SOURCE_DIR),
        tmp_path / case.sample_id,
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        manual_reference_path=case.manual_path(REFERENCE_DIR),
    )
    manufacturing = report["manufacturing_ir"]

    assert manufacturing["profile"] == case.profile
    assert manufacturing["material"] == case.material
    assert report["automation_assessment"]["disposition"] == case.disposition
    assert report["proof_report"]["blocking_obligation_ids"] == list(
        case.blocking_proof_ids
    )
    assert report["manufacturing_ir_validation"]["ok"] is True
    assert report["saved_dxf"]["ok"] is True
    assert report["supervised_comparison_used_for_decision"] is False

    actual_plates = manufacturing["plates"]
    assert len(actual_plates) == len(case.physical_plates) == 3
    for actual, expected in zip(actual_plates, case.physical_plates):
        assert actual["role"] == expected.role
        assert actual["thickness_mm"] == pytest.approx(expected.thickness_mm)
        assert _plate_bbox(actual) == pytest.approx(expected.bbox_mm, abs=0.01)
        assert len(actual["circular_cuts"]) == expected.circular_cut_count
        assert len(actual["inner_contours"]) == expected.inner_contour_count

    comparison = report["supervised_comparison"]
    assert comparison is not None
    assert comparison["ok"] is (case.disposition == "auto_accept")
    assert comparison["values"][
        "max_circular_cut_center_difference_mm"
    ] <= 0.01
    assert comparison["values"][
        "max_circular_cut_radius_difference_mm"
    ] <= 0.01

    if case.disposition == "auto_accept":
        assert production_path is not None
        assert review_candidate_path is None
        artifact = production_path
        assert report["automation_route"] == "production"
    else:
        assert production_path is None
        assert review_candidate_path is not None
        artifact = review_candidate_path
        assert report["automation_route"] == "review_required"

    saved = ezdxf.readfile(artifact)
    assert not list(saved.modelspace().query("LINE"))
    assert not list(saved.modelspace().query("XLINE"))
    assert not list(saved.modelspace().query("RAY"))


def test_manifest_loader_rejects_unknown_fields_and_duplicate_ids(
    tmp_path: Path,
) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    bad = tmp_path / "unknown.json"
    bad.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown fields"):
        load_corpus_manifest(
            bad,
            source_dir=SOURCE_DIR,
            reference_dir=REFERENCE_DIR,
        )

    payload.pop("unexpected")
    payload["cases"].append(dict(payload["cases"][0]))
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate sample_id"):
        load_corpus_manifest(
            duplicate,
            source_dir=SOURCE_DIR,
            reference_dir=REFERENCE_DIR,
        )


def test_manifest_loader_rejects_missing_files_and_hash_drift(
    tmp_path: Path,
) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["cases"] = [payload["cases"][0]]

    missing = tmp_path / "missing.json"
    payload["cases"][0]["sample_id"] = "missing"
    payload["cases"][0]["source_file"] = "missing_拆板前.dxf"
    payload["cases"][0]["manual_file"] = "missing_拆板后.dxf"
    missing.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="missing source DXF"):
        load_corpus_manifest(
            missing,
            source_dir=SOURCE_DIR,
            reference_dir=REFERENCE_DIR,
        )

    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["cases"] = [payload["cases"][0]]
    payload["cases"][0]["source_sha256"] = "0" * 64
    drift = tmp_path / "drift.json"
    drift.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="source hash mismatch"):
        load_corpus_manifest(
            drift,
            source_dir=SOURCE_DIR,
            reference_dir=REFERENCE_DIR,
        )


def test_manifest_loader_requires_both_file_roots() -> None:
    with pytest.raises(ValueError, match="must be provided together"):
        load_corpus_manifest(MANIFEST, source_dir=SOURCE_DIR)


def test_manifest_is_not_imported_by_the_production_package() -> None:
    production = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src" / "steel_dxf_split").glob("bh_*.py")
    )
    assert "tests/fixtures/bh_corpus.json" not in production
    assert "bh_corpus.json" not in production
