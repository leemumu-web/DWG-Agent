"""Manufacturing delta verdict against a frozen historical BOX result."""

from __future__ import annotations

from dataclasses import dataclass, replace

from steel_dxf_split.box.equivalence import PlateOutputGroup
from steel_dxf_split.box.weld_allowance import stretch_outer_segments

from .geometry import (
    ComparisonTolerance,
    WholeDrawingComparison,
    compare_groups_to_reference,
)
from .historical_result import HistoricalPlateSet
from .manual_reference import ManualReference


HISTORICAL_DELTA_TOLERANCE = ComparisonTolerance(
    contour_hausdorff_mm=0.1,
    symmetric_difference_fraction=0.0001,
    area_relative=0.0001,
    zero_area_overlay_fraction=0.00001,
    hole_center_mm=0.05,
    hole_radius_mm=0.01,
    inner_contour_hausdorff_mm=0.05,
    inner_contour_symmetric_fraction=0.0001,
)


@dataclass(frozen=True, slots=True)
class HistoricalDeltaVerdict:
    ok: bool
    allowed_merges: tuple[str, ...]
    forbidden_changes: tuple[str, ...]
    comparison: WholeDrawingComparison


def _family(group: PlateOutputGroup) -> str:
    return "web" if "web" in group.roles[0].value else "flange"


def _materialized_group(group: PlateOutputGroup) -> PlateOutputGroup:
    materialized = tuple(
        replace(
            plate,
            outer_segments=(
                stretch_outer_segments(
                    plate.outer_segments,
                    plate.weld_allowance_contract,
                )
                if plate.weld_allowance_contract is not None
                else plate.outer_segments
            ),
            weld_allowance_contract=None,
        )
        for plate in group.physical_plates
    )
    representative_index = group.physical_plates.index(group.representative)
    return replace(
        group,
        physical_plates=materialized,
        representative=materialized[representative_index],
    )


def compare_historical_delta(
    groups: tuple[PlateOutputGroup, ...],
    historical: HistoricalPlateSet,
    *,
    part_number: str,
    allowed_merge_families: frozenset[str],
) -> HistoricalDeltaVerdict:
    historical_merged_families = {
        plate.family
        for plate in historical.plates
        if plate.quantity == 2 and plate.side is None
    }
    unexpected_merges = tuple(
        f"unexpected_{_family(group)}_merge"
        for group in groups
        if group.quantity > 1
        and _family(group) not in allowed_merge_families
        and _family(group) not in historical_merged_families
    )
    missing_merges = tuple(
        f"missing_{family}_merge"
        for family in sorted(allowed_merge_families)
        if not any(
            _family(group) == family and group.quantity == 2
            for group in groups
        )
    )
    reference = ManualReference(
        path=historical.path,
        member_mark=historical.member_mark,
        plates=historical.plates,
        evidence_warnings=historical.evidence_warnings,
    )
    comparison = compare_groups_to_reference(
        tuple(_materialized_group(group) for group in groups),
        reference,
        part_number=part_number,
        tolerance=HISTORICAL_DELTA_TOLERANCE,
    )
    forbidden = [*unexpected_merges, *missing_merges]
    forbidden.extend(
        key
        for key in comparison.failed_check_keys
        if key not in {"web_group_count", "flange_group_count"}
    )
    for family in ("web", "flange"):
        count_key = f"{family}_group_count"
        if count_key not in comparison.failed_check_keys:
            continue
        if family not in allowed_merge_families:
            forbidden.append(count_key)
    allowed = tuple(
        family
        for family in sorted(allowed_merge_families)
        if any(
            _family(group) == family and group.quantity == 2
            for group in groups
        )
    )
    unique_forbidden = tuple(dict.fromkeys(forbidden))
    return HistoricalDeltaVerdict(
        ok=not unique_forbidden,
        allowed_merges=allowed,
        forbidden_changes=unique_forbidden,
        comparison=comparison,
    )
