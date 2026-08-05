from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Iterable
from typing import Any

from .bh_dimensions import displayed_dimension_tolerance
from .bh_knowledge import BHFlangeDevelopmentPolicy


@dataclass(frozen=True, slots=True)
class CrankedCandidateSelection:
    authorized: bool
    selected_raw_length_mm: float | None
    quantized_length_mm: float | None
    candidate_count: int
    match_count: int
    tolerance_mm: float


def quantize_derived_flange_length(
    value: float,
    policy: BHFlangeDevelopmentPolicy,
) -> float:
    quantum = float(policy.derived_length_quantum_mm)
    if (
        not policy.enabled
        or policy.derived_length_rounding != "floor"
        or quantum <= 0.0
    ):
        raise ValueError("BH flange development policy is not authorized")
    return floor(float(value) / quantum + 1e-9) * quantum


def select_profile_authorized_cranked_candidate(
    candidates: Iterable[float],
    *,
    nominal_length_mm: float,
    nominal_text: str,
    policy: BHFlangeDevelopmentPolicy,
    geometric_tolerance_mm: float,
) -> CrankedCandidateSelection:
    values = tuple(float(value) for value in candidates)
    tolerance = displayed_dimension_tolerance(
        nominal_text,
        geometric_tolerance_mm=geometric_tolerance_mm,
    )
    matches = tuple(
        value
        for value in values
        if abs(value - float(nominal_length_mm)) <= tolerance
    )
    authorized = policy.enabled and (
        len(matches) == 1
        if policy.require_unique_cranked_candidate
        else bool(matches)
    )
    selected = matches[0] if authorized else None
    quantized = (
        quantize_derived_flange_length(selected, policy)
        if selected is not None
        else None
    )
    return CrankedCandidateSelection(
        authorized=authorized,
        selected_raw_length_mm=selected,
        quantized_length_mm=quantized,
        candidate_count=len(values),
        match_count=len(matches),
        tolerance_mm=tolerance,
    )


def assess_flange_development_semantics(
    development: dict[str, Any],
    *,
    nominal_part_length_mm: float,
    flange_thickness_mm: float,
    geometric_tolerance_mm: float,
) -> dict[str, Any]:
    """Classify what a source projection proves about a developed flange.

    A straight strip seen edge-on is a rigid projection: its axial extent is a
    geometric observation, but a newly generated fabrication length still
    needs a bound total dimension to authorize displayed precision/rounding.
    A kinked strip is weaker because Tekla polybeam length and unfolding may
    depend on model-level reference-line and unfolding settings that are not
    encoded in the exported outline.

    The material-table part length is therefore used as a consistency channel,
    never as an implicit replacement for a flange development dimension.
    """

    mode = str(development.get("mode") or "")
    targets = tuple(
        float(value) for value in development.get("target_lengths_mm", ())
    )
    details = tuple(development.get("details", ()) or ())
    certificate = development.get("certificate", {}) or {}
    certificate_kind = str(certificate.get("certificate_kind") or "")
    profile_authorized = bool(certificate.get("authorized"))
    strip_tolerance = max(
        float(geometric_tolerance_mm),
        0.02 * float(flange_thickness_mm),
    )
    valid_straight_strips = bool(details) and all(
        bool(item.get("straight"))
        and item.get("method") == "straight_strip_projection"
        and abs(
            float(item.get("observed_strip_thickness_mm", float("inf")))
            - float(flange_thickness_mm)
        )
        <= strip_tolerance
        and float(item.get("rectangular_fill_ratio", 0.0)) >= 0.98
        for item in details
    )

    if mode in {"projection_view", "projection_only"}:
        determinacy = "direct_projection"
        requires_unfolding_policy = False
        fabrication_authority = "source_projection"
    elif mode in {
        "variable_height_two_paths",
        "constant_height_two_flange_paths",
    } and valid_straight_strips:
        determinacy = "rigid_projection_determined"
        requires_unfolding_policy = False
        fabrication_authority = "bound_total_length_required"
    elif mode == "constant_height_cranked_path" or any(
        item.get("method") == "kinked_strip_boundary_paths" for item in details
    ):
        determinacy = "unfolding_policy_required"
        requires_unfolding_policy = True
        fabrication_authority = "bound_total_length_required"
    else:
        determinacy = "incomplete_geometry_observation"
        requires_unfolding_policy = mode not in {"projection_view", "projection_only"}
        fabrication_authority = "bound_total_length_required"

    if profile_authorized and mode in {
        "variable_height_two_paths",
        "constant_height_cranked_path",
        "constant_height_two_flange_paths",
    }:
        requires_unfolding_policy = False
        fabrication_authority = "profile_authorized_source_geometry"

    nominal_text = f"{float(nominal_part_length_mm):g}"
    nominal_tolerance = displayed_dimension_tolerance(
        nominal_text,
        geometric_tolerance_mm=geometric_tolerance_mm,
    )
    consistency_targets = tuple(
        float(value)
        for value in (
            certificate.get("raw_lengths_mm", ())
            if profile_authorized
            else targets
        )
    ) or targets
    match_count = sum(
        abs(target - nominal_part_length_mm) <= nominal_tolerance
        for target in consistency_targets
    )
    if not consistency_targets or match_count == 0:
        part_length_consistency = "not_consistent_supporting_only"
    elif match_count == len(consistency_targets):
        part_length_consistency = "consistent_supporting_only"
    else:
        part_length_consistency = "partial_supporting_only"

    return {
        "geometry_determinacy": determinacy,
        "valid_straight_strip_count": sum(
            bool(item.get("straight")) for item in details
        ),
        "observed_strip_count": len(details),
        "requires_unfolding_policy": requires_unfolding_policy,
        "fabrication_authority": fabrication_authority,
        "part_length_consistency": part_length_consistency,
        "part_length_match_count": match_count,
        "target_length_count": len(consistency_targets),
        "part_length_compared_lengths_mm": consistency_targets,
        "part_length_tolerance_mm": nominal_tolerance,
        "part_length_authority": "supporting_only_model_configuration_dependent",
        "profile_authorized": profile_authorized,
        "certificate_kind": certificate_kind,
        "raw_lengths_mm": tuple(certificate.get("raw_lengths_mm", ()) or ()),
        "quantized_lengths_mm": tuple(
            certificate.get("quantized_lengths_mm", ()) or ()
        ),
        "candidate_count": int(certificate.get("candidate_count", 0) or 0),
        "match_count": int(certificate.get("match_count", 0) or 0),
        "quantization_policy": dict(certificate.get("policy", {}) or {}),
    }
