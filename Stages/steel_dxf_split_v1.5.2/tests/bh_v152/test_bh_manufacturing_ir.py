from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ezdxf.math import Matrix44
import pytest

from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_frames import infer_member_frames
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.bh_manufacturing_ir import (
    EvidenceState,
    ManufacturingPlateRole,
    build_bh_manufacturing_ir,
)
import steel_dxf_split.bh_provenance as bh_provenance
from steel_dxf_split.bh_provenance import build_plate_feature_evidence
from steel_dxf_split.bh_proofs import ProofReport
from steel_dxf_split.bh_source import SourceContainer, decode_source_document
from steel_dxf_split.bh_validator import validate_bh_manufacturing_ir
from steel_dxf_split.dxf_io import load_document

from bh_transform_fixtures import explode_top_level_inserts, transform_modelspace


ROOT = Path(__file__).resolve().parents[2]
PAIR_DIR = ROOT / "samples" / "bh_pairs"


def _compile_manufacturing_ir(stem: str):
    path = PAIR_DIR / f"{stem}_拆板前.dxf"
    doc = load_document(path)
    source = decode_source_document(doc)
    frame = infer_member_frames(source).selected
    compiled = compile_bh_document(
        doc,
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=path,
    )
    manufacturing = build_bh_manufacturing_ir(
        compiled.assembly,
        source,
        frame,
        compiled.proof_report,
    )
    return compiled, source, frame, manufacturing


def test_manufacturing_ir_has_physical_roles_and_feature_evidence() -> None:
    compiled, _, _, manufacturing = _compile_manufacturing_ir("3b2-cb-86")

    assert [plate.role for plate in manufacturing.plates] == [
        ManufacturingPlateRole.WEB,
        ManufacturingPlateRole.UPPER_FLANGE,
        ManufacturingPlateRole.LOWER_FLANGE,
    ]
    assert all(plate.quantity == 1 for plate in manufacturing.plates)
    assert all(plate.material == "Q390B" for plate in manufacturing.plates)
    assert [plate.thickness_mm for plate in manufacturing.plates] == [16.0, 20.0, 20.0]
    assert sum(len(plate.circular_cuts) for plate in manufacturing.plates) == 34
    assert sorted(
        len(plate.circular_cuts)
        for plate in manufacturing.plates
        if plate.role != ManufacturingPlateRole.WEB
    ) == [0, 2]
    features = [
        segment
        for plate in manufacturing.plates
        for segment in (
            *plate.outer_segments,
            *(segment for contour in plate.inner_contours for segment in contour.segments),
        )
    ]
    cuts = [cut for plate in manufacturing.plates for cut in plate.circular_cuts]
    assert features and cuts
    assert all(item.evidence.state != EvidenceState.MISSING for item in features)
    assert all(
        item.evidence.source_ids or item.evidence.rule_ids
        for item in (*features, *cuts)
    )
    assert all(item.evidence.state == EvidenceState.DIRECT for item in cuts)

    validation = validate_bh_manufacturing_ir(manufacturing, compiled.assembly)
    assert validation.ok, validation.to_dict()


def test_circular_cut_lineage_rows_are_parallel_and_exact() -> None:
    compiled, _, _, manufacturing = _compile_manufacturing_ir("3b2-cb-86")

    for plate in compiled.assembly.plates:
        source_rows = plate.provenance["circular_cut_source_ids"]
        assert len(source_rows) == len(plate.circular_cuts)
        assert all(source_row for source_row in source_rows)

    for plate in manufacturing.plates:
        source_plate = compiled.assembly.plates[plate.source_assembly_plate_index]
        expected_rows = source_plate.provenance["circular_cut_source_ids"]
        assert [cut.evidence.source_ids for cut in plate.circular_cuts] == [
            tuple(sorted(map(str, source_row))) for source_row in expected_rows
        ]


def test_equal_coordinate_circle_from_other_container_cannot_enter_cut_evidence() -> None:
    compiled, source, frame, _ = _compile_manufacturing_ir("3b2-cb-86")
    expected_source_id = str(
        compiled.assembly.web_plate.provenance["circular_cut_source_ids"][0][0]
    )
    original = next(
        entity for entity in source.entities if entity.source_id == expected_source_id
    )
    distractor_id = "unselected-view-equal-coordinate-circle"
    distractor = replace(
        original,
        source_id=distractor_id,
        container_id="distractor-view",
        path=replace(original.path, entity_ordinal=original.path.entity_ordinal + 1_000_000),
    )
    source_with_distractor = replace(
        source,
        entities=(*source.entities, distractor),
        containers=(
            *source.containers,
            SourceContainer(
                container_id="distractor-view",
                explicit_block=True,
                source_ids=(distractor_id,),
                block_name="DISTRACTOR_VIEW",
            ),
        ),
    )

    manufacturing = build_bh_manufacturing_ir(
        compiled.assembly,
        source_with_distractor,
        frame,
        compiled.proof_report,
    )
    web = next(
        plate for plate in manufacturing.plates if plate.role == ManufacturingPlateRole.WEB
    )

    assert web.circular_cuts[0].evidence.source_ids == (expected_source_id,)
    assert distractor_id not in web.circular_cuts[0].evidence.source_ids


def test_cut_provenance_uses_the_recorded_plate_normalization_translation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiled, source, frame, _ = _compile_manufacturing_ir("3b2-cb-86")
    web = compiled.assembly.web_plate
    assert web.provenance["normalization_translation_mm"]

    monkeypatch.setattr(
        bh_provenance,
        "_registration_offset",
        lambda *_args, **_kwargs: (1_000_000.0, 1_000_000.0),
    )
    evidence = build_plate_feature_evidence(
        web,
        assembly_index=0,
        source=source,
        frame=frame,
        proof_report=compiled.proof_report,
        tolerance_mm=0.15,
    )

    assert evidence.cuts
    assert all(item.state == EvidenceState.DIRECT for item in evidence.cuts)


def test_identical_flange_merge_is_explicit_but_physical_roles_remain_separate() -> None:
    _, _, _, manufacturing = _compile_manufacturing_ir("2b1-cb-26")

    flanges = [
        plate
        for plate in manufacturing.plates
        if plate.role != ManufacturingPlateRole.WEB
    ]
    assert len(flanges) == 2
    assert {plate.role for plate in flanges} == {
        ManufacturingPlateRole.UPPER_FLANGE,
        ManufacturingPlateRole.LOWER_FLANGE,
    }
    assert all(plate.quantity == 1 for plate in flanges)
    assert flanges[0].merge_group_id == flanges[1].merge_group_id
    assert flanges[0].merge_group_id is not None
    assert all(plate.merge_authorized for plate in flanges)
    assert all(
        "BH.PROOF.FLANGE.IDENTICAL_MERGE" in plate.role_evidence.proof_ids
        for plate in flanges
    )


def test_manufacturing_ir_is_canonical_and_deterministic() -> None:
    _, _, _, first = _compile_manufacturing_ir("3b2-cb-86")
    _, _, _, second = _compile_manufacturing_ir("3b2-cb-86")

    assert first.to_canonical_json() == second.to_canonical_json()
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64


def test_manufacturing_fingerprint_excludes_diagnostic_fit_residual() -> None:
    """Representation noise is auditable, but is not manufacturing identity."""

    _, _, _, manufacturing = _compile_manufacturing_ir("3b2-cb-86")
    web = manufacturing.plates[0]
    first = web.outer_segments[0]
    changed_evidence = replace(
        first.evidence,
        residual_mm=(first.evidence.residual_mm or 0.0) + 0.025,
    )
    changed_segment = replace(first, evidence=changed_evidence)
    changed_web = replace(
        web,
        outer_segments=(changed_segment, *web.outer_segments[1:]),
    )
    changed_ir = replace(
        manufacturing,
        plates=(changed_web, *manufacturing.plates[1:]),
    )

    assert changed_ir.to_canonical_json() != manufacturing.to_canonical_json()
    assert changed_ir.fingerprint == manufacturing.fingerprint


def test_missing_source_geometry_is_visible_in_feature_provenance() -> None:
    compiled, source, frame, _ = _compile_manufacturing_ir("3b2-cb-86")
    empty_source = replace(source, entities=(), containers=())

    manufacturing = build_bh_manufacturing_ir(
        compiled.assembly,
        empty_source,
        frame,
        compiled.proof_report,
    )

    assert any(
        segment.evidence.state == EvidenceState.MISSING
        for plate in manufacturing.plates
        for segment in plate.outer_segments
    )
    validation = validate_bh_manufacturing_ir(manufacturing, compiled.assembly)
    assert not validation.ok
    assert "FEATURE.PROVENANCE.MISSING" in validation.diagnostic_codes


def test_source_match_cannot_claim_a_proof_that_was_not_emitted() -> None:
    compiled, source, frame, _ = _compile_manufacturing_ir("3b2-cb-86")

    manufacturing = build_bh_manufacturing_ir(
        compiled.assembly,
        source,
        frame,
        ProofReport(obligations=(), search_complete=True),
    )

    assert any(
        segment.evidence.state == EvidenceState.MISSING
        for plate in manufacturing.plates
        for segment in plate.outer_segments
    )
    validation = validate_bh_manufacturing_ir(manufacturing, compiled.assembly)
    assert not validation.ok
    assert "FEATURE.PROVENANCE.MISSING" in validation.diagnostic_codes


def test_validator_detects_ir_geometry_drift_from_writer_assembly() -> None:
    compiled, _, _, manufacturing = _compile_manufacturing_ir("3b2-cb-86")
    web = manufacturing.plates[0]
    first = web.outer_segments[0]
    changed = replace(
        first,
        end=(first.end[0] + 10.0, first.end[1]),
    )
    changed_web = replace(
        web,
        outer_segments=(changed, *web.outer_segments[1:]),
    )
    changed_ir = replace(
        manufacturing,
        plates=(changed_web, *manufacturing.plates[1:]),
    )

    validation = validate_bh_manufacturing_ir(changed_ir, compiled.assembly)

    assert not validation.ok
    assert validation.checks["geometry_matches_writer_assembly"] is False
    assert "MANUFACTURING.IR.CONTRACT.MISMATCH" in validation.diagnostic_codes


@pytest.mark.parametrize(
    "mutation",
    [
        lambda doc: transform_modelspace(
            doc,
            Matrix44.translate(1234.5, -678.25, 0.0),
        ),
        lambda doc: transform_modelspace(doc, Matrix44.scale(-1.0, 1.0, 1.0)),
        lambda doc: transform_modelspace(doc, Matrix44.scale(1.0, -1.0, 1.0)),
        explode_top_level_inserts,
    ],
    ids=["translate", "mirror_x", "mirror_y", "explode"],
)
def test_manufacturing_ir_fingerprint_is_representation_invariant(mutation) -> None:
    source_path = PAIR_DIR / "3b2-cb-86_拆板前.dxf"
    _, _, _, baseline = _compile_manufacturing_ir("3b2-cb-86")
    doc = mutation(load_document(source_path))
    source = decode_source_document(doc)
    frame = infer_member_frames(source).selected
    compiled = compile_bh_document(
        doc,
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source_path,
    )
    mutated = build_bh_manufacturing_ir(
        compiled.assembly,
        source,
        frame,
        compiled.proof_report,
    )

    assert mutated.fingerprint == baseline.fingerprint
