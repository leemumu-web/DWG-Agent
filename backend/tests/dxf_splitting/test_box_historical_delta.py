from __future__ import annotations

from pathlib import Path

from shapely.geometry import Polygon
from steel_dxf_split.box.equivalence import PlateOutputGroup
from steel_dxf_split.box.manufacturing_ir import (
    EvidenceState,
    FeatureEvidence,
    PhysicalPlateIR,
    PhysicalPlateRole,
    derive_weld_allowance_contract,
    rectangle_contour,
)
from tools.box_acceptance.historical_delta import compare_historical_delta
from tools.box_acceptance.historical_result import HistoricalPlateSet
from tools.box_acceptance.manual_reference import ManualPlate, ManualShape


def _evidence() -> FeatureEvidence:
    return FeatureEvidence(
        state=EvidenceState.DIRECT,
        source_ids=("test/source",),
        rule_ids=("BOX.RULE.TEST",),
        proof_ids=("BOX.PROOF.TEST",),
        description="historical delta fixture",
    )


def _plate(role: PhysicalPlateRole, *, length: float, width: float) -> PhysicalPlateIR:
    evidence = _evidence()
    outer = rectangle_contour(0.0, 0.0, length, width, evidence)
    return PhysicalPlateIR(
        plate_id=role.value,
        role=role,
        material="Q355B",
        thickness_mm=20.0,
        outer_segments=outer,
        circular_cuts=(),
        inner_contours=(),
        role_evidence=evidence,
        weld_allowance_contract=derive_weld_allowance_contract(outer),
    )


def _merged(first: PhysicalPlateIR, second: PhysicalPlateIR) -> PlateOutputGroup:
    return PlateOutputGroup(
        group_id=f"{first.role.value}+{second.role.value}",
        roles=(first.role, second.role),
        physical_plates=(first, second),
        representative=first,
        quantity=2,
        merge_authorized=True,
        equivalence_tolerance_mm=1e-5,
    )


def _single(plate: PhysicalPlateIR) -> PlateOutputGroup:
    return PlateOutputGroup(
        group_id=plate.role.value,
        roles=(plate.role,),
        physical_plates=(plate,),
        representative=plate,
        quantity=1,
        merge_authorized=False,
        equivalence_tolerance_mm=1e-5,
    )


def _manual(label: str, family: str, *, x: float, length: float, width: float) -> ManualPlate:
    polygon = Polygon(((x, 0), (x + length, 0), (x + length, width), (x, width)))
    return ManualPlate(
        label=label,
        family=family,
        side="top" if label.startswith("上") else "bottom" if label.startswith("下") else None,
        quantity=2 if label in {"腹", "翼"} else 1,
        label_position=(float(polygon.centroid.x), float(polygon.centroid.y)),
        shape=ManualShape.from_polygon(
            entity_handle=f"historical:{label}",
            kind="TEST",
            polygon=polygon,
        ),
    )


def _historical(*plates: ManualPlate) -> HistoricalPlateSet:
    return HistoricalPlateSet(
        path=Path("historical.json"),
        sample_id="b4-3-cb-test",
        source_relative_path="old-result.dwg",
        source_sha256="a" * 64,
        member_mark="b4-3-cb-test",
        plates=plates,
    )


def test_historical_delta_allows_only_the_declared_web_pair_to_fold() -> None:
    """Catch a verdict that rejects the one authorized quantity-only merge."""

    web_top = _plate(PhysicalPlateRole.WEB_LEFT, length=6000.0, width=80.0)
    web_bottom = _plate(PhysicalPlateRole.WEB_RIGHT, length=6000.0, width=80.0)
    flange_top = _plate(PhysicalPlateRole.FLANGE_TOP, length=5800.0, width=60.0)
    flange_bottom = _plate(PhysicalPlateRole.FLANGE_BOTTOM, length=5800.0, width=60.0)
    groups = (
        _merged(web_top, web_bottom),
        _merged(flange_top, flange_bottom),
    )
    historical = _historical(
        _manual("上腹", "web", x=0, length=6010.0, width=80.0),
        _manual("下腹", "web", x=7000, length=6010.0, width=80.0),
        _manual("翼", "flange", x=14000, length=5810.0, width=60.0),
    )

    verdict = compare_historical_delta(
        groups,
        historical,
        part_number="b4-3-cb-test",
        allowed_merge_families=frozenset({"web"}),
    )

    assert verdict.ok is True
    assert verdict.allowed_merges == ("web",)
    assert verdict.forbidden_changes == ()


def test_historical_delta_rejects_a_non_target_flange_merge() -> None:
    """Catch a verdict that treats every quantity-two output as authorized."""

    web_top = _plate(PhysicalPlateRole.WEB_LEFT, length=6000.0, width=80.0)
    web_bottom = _plate(PhysicalPlateRole.WEB_RIGHT, length=6000.0, width=80.0)
    flange_top = _plate(PhysicalPlateRole.FLANGE_TOP, length=5800.0, width=60.0)
    flange_bottom = _plate(PhysicalPlateRole.FLANGE_BOTTOM, length=5800.0, width=60.0)
    historical = _historical(
        _manual("上腹", "web", x=0, length=6010.0, width=80.0),
        _manual("下腹", "web", x=7000, length=6010.0, width=80.0),
        _manual("上翼", "flange", x=14000, length=5810.0, width=60.0),
        _manual("下翼", "flange", x=21000, length=5810.0, width=60.0),
    )

    verdict = compare_historical_delta(
        (_merged(web_top, web_bottom), _merged(flange_top, flange_bottom)),
        historical,
        part_number="b4-3-cb-test",
        allowed_merge_families=frozenset({"web"}),
    )

    assert verdict.ok is False
    assert "unexpected_flange_merge" in verdict.forbidden_changes


def test_historical_delta_rejects_contour_change_after_allowance() -> None:
    """Catch a verdict that checks grouping but ignores manufacturing geometry."""

    web_top = _plate(PhysicalPlateRole.WEB_LEFT, length=6000.0, width=80.0)
    web_bottom = _plate(PhysicalPlateRole.WEB_RIGHT, length=6000.0, width=80.0)
    flange_top = _plate(PhysicalPlateRole.FLANGE_TOP, length=5800.0, width=60.0)
    flange_bottom = _plate(PhysicalPlateRole.FLANGE_BOTTOM, length=5900.0, width=60.0)
    historical = _historical(
        _manual("上腹", "web", x=0, length=6011.0, width=80.0),
        _manual("下腹", "web", x=7000, length=6011.0, width=80.0),
        _manual("上翼", "flange", x=14000, length=5810.0, width=60.0),
        _manual("下翼", "flange", x=21000, length=5910.0, width=60.0),
    )

    verdict = compare_historical_delta(
        (
            _merged(web_top, web_bottom),
            _single(flange_top),
            _single(flange_bottom),
        ),
        historical,
        part_number="b4-3-cb-test",
        allowed_merge_families=frozenset({"web"}),
    )

    assert verdict.ok is False
    assert "contour" in verdict.forbidden_changes
