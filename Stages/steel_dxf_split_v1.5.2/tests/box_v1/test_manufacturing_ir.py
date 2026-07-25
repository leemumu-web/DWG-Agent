from __future__ import annotations

import re
from dataclasses import replace

import pytest

from steel_dxf_split.box.manufacturing_ir import (
    BOX_MIR_SCHEMA,
    BoxManufacturingIR,
    ContourSegmentIR,
    EvidenceState,
    FeatureEvidence,
    ManufacturingIRValidationError,
    PhysicalPlateIR,
    PhysicalPlateRole,
    contour_semantic_key,
    rectangle_contour,
    validate_contour,
)

DIRECT = FeatureEvidence(
    state=EvidenceState.DIRECT,
    source_ids=("insert:1/entity:2",),
    rule_ids=("BOX.RULE.TEST",),
    proof_ids=("BOX.PROOF.TEST",),
)


def _plate(role: PhysicalPlateRole, width: float = 200.0) -> PhysicalPlateIR:
    return PhysicalPlateIR(
        plate_id=role.value,
        role=role,
        material="Q355B",
        thickness_mm=20.0,
        outer_segments=rectangle_contour(0.0, 0.0, width, 100.0, DIRECT),
        circular_cuts=(),
        inner_contours=(),
        role_evidence=DIRECT,
    )


def _mir(plates: tuple[PhysicalPlateIR, ...]) -> BoxManufacturingIR:
    return BoxManufacturingIR.create(
        part_number="box-1",
        profile="BOX140*200*20*20",
        nominal_length_mm=200.0,
        material="Q355B",
        physical_plates=plates,
        proof_disposition="auto_accept",
        proof_ids=("BOX.PROOF.TEST",),
    )


def test_rectangle_contour_is_closed_and_source_backed() -> None:
    contour = rectangle_contour(10.0, 20.0, 210.0, 120.0, DIRECT)

    validate_contour(contour)
    assert len(contour) == 4
    assert contour[-1].end == contour[0].start
    assert all(segment.evidence == DIRECT for segment in contour)


def test_contour_semantic_key_ignores_subgrid_noise_and_collinear_backtrack() -> None:
    reference = rectangle_contour(0.0, 0.0, 10.0, 5.0, DIRECT)
    perturbed = (
        ContourSegmentIR("edge:0", (0.0, 0.0), (10.00024, 0.0), 0.0, DIRECT),
        ContourSegmentIR(
            "edge:1", (10.00024, 0.0), (10.00024, 5.00024), 0.0, DIRECT
        ),
        ContourSegmentIR(
            "edge:2a", (10.00024, 5.00024), (8.0, 5.00024), 0.0, DIRECT
        ),
        ContourSegmentIR(
            "edge:2b", (8.0, 5.00024), (10.0, 5.00024), 0.0, DIRECT
        ),
        ContourSegmentIR(
            "edge:2c", (10.0, 5.00024), (0.0, 5.00024), 0.0, DIRECT
        ),
        ContourSegmentIR("edge:3", (0.0, 5.00024), (0.0, 0.0), 0.0, DIRECT),
    )

    assert contour_semantic_key(perturbed) == contour_semantic_key(reference)


def test_unclosed_or_degenerate_contour_is_rejected() -> None:
    unclosed = (
        ContourSegmentIR("a", (0.0, 0.0), (10.0, 0.0), 0.0, DIRECT),
        ContourSegmentIR("b", (10.0, 0.0), (10.0, 10.0), 0.0, DIRECT),
    )
    degenerate = rectangle_contour(0.0, 0.0, 0.0, 10.0, DIRECT)

    with pytest.raises(ManufacturingIRValidationError, match="closed"):
        validate_contour(unclosed)
    with pytest.raises(ManufacturingIRValidationError, match="positive area"):
        validate_contour(degenerate)


def test_mir_requires_exactly_four_unique_physical_roles() -> None:
    roles = tuple(PhysicalPlateRole)
    valid = tuple(_plate(role) for role in roles)

    assert _mir(valid).schema_version == BOX_MIR_SCHEMA
    with pytest.raises(ManufacturingIRValidationError, match="four physical roles"):
        _mir(valid[:-1])
    with pytest.raises(ManufacturingIRValidationError, match="four physical roles"):
        _mir((*valid[:-1], valid[0]))


def test_mir_fingerprint_ignores_plate_and_contour_enumeration() -> None:
    plates = tuple(_plate(role) for role in PhysicalPlateRole)
    first = _mir(plates)
    rotated = tuple(
        replace(
            plate,
            outer_segments=plate.outer_segments[2:] + plate.outer_segments[:2],
        )
        for plate in reversed(plates)
    )
    second = _mir(rotated)

    assert re.fullmatch(r"[0-9a-f]{64}", first.fingerprint)
    assert first.fingerprint == second.fingerprint


def test_mir_fingerprint_changes_with_manufacturing_geometry() -> None:
    plates = tuple(_plate(role) for role in PhysicalPlateRole)
    changed = tuple(
        _plate(role, width=201.0)
        if role is PhysicalPlateRole.WEB_LEFT
        else _plate(role)
        for role in PhysicalPlateRole
    )

    assert _mir(plates).fingerprint != _mir(changed).fingerprint
