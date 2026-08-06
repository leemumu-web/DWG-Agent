from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from math import hypot

from shapely import normalize, set_precision

from .equivalence import BOX_DRAFTING_RESOLUTION_MM
from .manufacturing_ir import (
    ContourSegmentIR,
    contour_polygon,
)
from .metadata import BoxMetadata
from .projection_geometry import (
    ProjectionFaceCandidate,
    enumerate_endpoint_cap_path_cycles,
    enumerate_projection_course_virtual_cycles,
    enumerate_source_backed_straight_overlay_cycles,
    enumerate_straight_inner_band_faces,
    polygonize_part_projection,
    search_connected_inner_course_cycles,
    search_source_conserving_face_unions,
)
from .projection_lowering import lower_projection_face_to_contour
from .view_solver import ViewAssignmentCandidate


class WebDerivation(StrEnum):
    SOURCE_FACE_UNION = "source_face_union"
    INNER_COURSE_BAND = "inner_course_band"
    CONNECTED_COURSE_CYCLE = "connected_course_cycle"
    ENDPOINT_CAP_PATH_CYCLE = "endpoint_cap_path_cycle"
    BOUNDED_VIRTUAL_COURSE_CYCLE = "bounded_virtual_course_cycle"
    SOURCE_BACKED_STRAIGHT_OVERLAY_CYCLE = "source_backed_straight_overlay_cycle"


_WEB_DERIVATION_AUTHORITY = {
    WebDerivation.BOUNDED_VIRTUAL_COURSE_CYCLE: 95.0,
    WebDerivation.SOURCE_BACKED_STRAIGHT_OVERLAY_CYCLE: 92.0,
    WebDerivation.CONNECTED_COURSE_CYCLE: 90.0,
    WebDerivation.INNER_COURSE_BAND: 85.0,
    WebDerivation.SOURCE_FACE_UNION: 80.0,
    WebDerivation.ENDPOINT_CAP_PATH_CYCLE: 75.0,
}


def web_derivation_authority(
    derivations: tuple[WebDerivation, ...],
) -> float:
    """Return the source-topology authority of one web representation."""

    return max(
        (_WEB_DERIVATION_AUTHORITY[derivation] for derivation in derivations),
        default=0.0,
    )


@dataclass(frozen=True, slots=True)
class WebOutlineCandidate:
    candidate_id: str
    contour: tuple[ContourSegmentIR, ...]
    projection: ProjectionFaceCandidate
    derivations: tuple[WebDerivation, ...]
    source_ids: tuple[str, ...]

    @property
    def longitudinal_span(self) -> float:
        bounds = contour_polygon(self.contour).bounds
        return float(bounds[2] - bounds[0])

    @property
    def transverse_span(self) -> float:
        bounds = contour_polygon(self.contour).bounds
        return float(bounds[3] - bounds[1])

    @property
    def area(self) -> float:
        return float(contour_polygon(self.contour).area)


@dataclass(frozen=True, slots=True)
class WebCandidateSearchResult:
    candidates: tuple[WebOutlineCandidate, ...]
    direct_face_search_pruned: bool
    direct_face_search_complete: bool
    connected_course_search_complete: bool
    diagnostics: tuple[str, ...]


def _candidate_representation_key(projection: ProjectionFaceCandidate) -> str:
    """Keep physical placement until role assignment is complete.

    Manufacturing contours are origin-normalized, so using only their shape as
    a candidate key collapses two equal opposite webs into one instance.  The
    projection key retains placement while merging sub-resolution spellings of
    the same source face.
    """

    return normalize(
        set_precision(
            projection.polygon,
            grid_size=BOX_DRAFTING_RESOLUTION_MM,
        )
    ).wkb_hex


def _candidate_id(key: str) -> str:
    return f"web:{sha256(key.encode('ascii')).hexdigest()[:16]}"


def _merge_coincident_source_representations(
    candidates: tuple[WebOutlineCandidate, ...],
) -> tuple[WebOutlineCandidate, ...]:
    """Merge derivation spellings, never translated physical instances."""

    by_sources: dict[tuple[str, ...], list[WebOutlineCandidate]] = {}
    for candidate in candidates:
        by_sources.setdefault(candidate.source_ids, []).append(candidate)
    merged: list[WebOutlineCandidate] = []
    tolerance = BOX_DRAFTING_RESOLUTION_MM
    for source_ids in sorted(by_sources):
        representations: list[WebOutlineCandidate] = []
        for candidate in sorted(
            by_sources[source_ids],
            key=lambda item: item.candidate_id,
        ):
            polygon = candidate.projection.polygon
            existing_index = next(
                (
                    index
                    for index, existing in enumerate(representations)
                    if polygon.hausdorff_distance(existing.projection.polygon)
                    <= tolerance
                    and polygon.symmetric_difference(
                        existing.projection.polygon
                    ).area
                    <= tolerance
                    * max(polygon.length, existing.projection.polygon.length, 1.0)
                ),
                None,
            )
            if existing_index is None:
                representations.append(candidate)
                continue
            existing = representations[existing_index]
            representations[existing_index] = replace(
                existing,
                derivations=tuple(
                    sorted(
                        {*existing.derivations, *candidate.derivations},
                        key=str,
                    )
                ),
                projection=replace(
                    existing.projection,
                    rule_ids=tuple(
                        sorted(
                            set(existing.projection.rule_ids)
                            | set(candidate.projection.rule_ids)
                        )
                    ),
                ),
            )
        merged.extend(representations)
    return tuple(merged)


def _has_negligible_course_backtrack(
    contour: tuple[ContourSegmentIR, ...],
    *,
    maximum_projection_detail_mm: float,
) -> bool:
    """Detect zero-area Tekla overlay spikes along one straight course."""

    for current, following in zip(
        contour,
        (*contour[1:], contour[0]),
        strict=True,
    ):
        if abs(current.bulge) > 1e-12 or abs(following.bulge) > 1e-12:
            continue
        first = (
            current.end[0] - current.start[0],
            current.end[1] - current.start[1],
        )
        second = (
            following.end[0] - following.start[0],
            following.end[1] - following.start[1],
        )
        first_length = hypot(*first)
        second_length = hypot(*second)
        if min(first_length, second_length) <= 1e-9:
            continue
        cross = abs(first[0] * second[1] - first[1] * second[0]) / (
            first_length * second_length
        )
        dot = (first[0] * second[0] + first[1] * second[1]) / (
            first_length * second_length
        )
        if (
            cross <= 0.0001
            and dot <= -0.9999
            and min(first_length, second_length) > maximum_projection_detail_mm
        ):
            return True
    return False


def enumerate_web_outline_candidates(
    assignment: ViewAssignmentCandidate,
    metadata: BoxMetadata,
    *,
    maximum_face_union_states: int = 10_000,
    maximum_direct_faces: int = 40,
) -> WebCandidateSearchResult:
    """Combine all source-backed BOX web interpretations without local commitment."""

    entities = assignment.h_view.entities
    frame = assignment.h_view.frame
    profile = metadata.profile.value
    target = profile.web_clear_width
    # Tekla sometimes leaves the faceted/ARC end-cap chain a small distance
    # from the projected inner course.  The admissible clustering distance is
    # derived from plate thickness and capped at one drafting-scale micro-gap;
    # it is neither a drawing-specific coordinate nor an output-fit tolerance.
    endpoint_tolerance = min(
        3.1,
        max(
            0.15,
            0.1 * min(profile.web_thickness, profile.flange_thickness),
        ),
    )
    band = enumerate_straight_inner_band_faces(
        entities,
        frame,
        target_transverse_mm=target,
    )
    cycle_search = search_connected_inner_course_cycles(
        entities,
        frame,
        target_transverse_mm=target,
        endpoint_tolerance_mm=endpoint_tolerance,
    )
    cycles = cycle_search.candidates
    endpoint_cycles = enumerate_endpoint_cap_path_cycles(
        entities,
        frame,
        target_transverse_mm=target,
    )
    straight_overlay_cycles = (
        enumerate_source_backed_straight_overlay_cycles(
            entities,
            frame,
            target_transverse_mm=target,
        )
        if frame.transverse_span <= profile.height * 1.5
        else ()
    )
    virtual_cycles = (
        enumerate_projection_course_virtual_cycles(
            entities,
            frame,
            target_transverse_mm=target,
        )
        if frame.transverse_span > profile.height * 1.5
        else ()
    )
    source_faces = polygonize_part_projection(
        entities,
        frame,
        include_hidden=True,
    )
    direct_face_search_pruned = len(source_faces) > maximum_direct_faces and bool(
        band or cycles
    )
    direct_search = search_source_conserving_face_unions(
        entities,
        frame,
        target_transverse_mm=target,
        maximum_states=maximum_face_union_states,
        run_subset_search=not direct_face_search_pruned,
    )
    direct = direct_search.candidates
    channels = (
        (WebDerivation.SOURCE_FACE_UNION, direct),
        (WebDerivation.INNER_COURSE_BAND, band),
        (WebDerivation.CONNECTED_COURSE_CYCLE, cycles),
        (WebDerivation.ENDPOINT_CAP_PATH_CYCLE, endpoint_cycles),
        (
            WebDerivation.SOURCE_BACKED_STRAIGHT_OVERLAY_CYCLE,
            straight_overlay_cycles,
        ),
        (WebDerivation.BOUNDED_VIRTUAL_COURSE_CYCLE, virtual_cycles),
    )
    by_key: dict[str, WebOutlineCandidate] = {}
    for derivation, projections in channels:
        for projection in projections:
            contour = lower_projection_face_to_contour(
                projection,
                entities,
                frame,
                profile,
            )
            polygon = contour_polygon(contour)
            transverse_span = float(polygon.bounds[3] - polygon.bounds[1])
            if (
                not polygon.is_valid
                or not polygon.exterior.is_simple
                or (
                    frame.transverse_span <= profile.height * 1.5
                    and _has_negligible_course_backtrack(
                        contour,
                        maximum_projection_detail_mm=2.0
                        * max(profile.web_thickness, profile.flange_thickness),
                    )
                )
                or polygon.area <= 0.0
                or (
                    frame.transverse_span <= profile.height * 1.5
                    and abs(transverse_span - target) > max(1.1, target * 0.005)
                )
            ):
                continue
            key = _candidate_representation_key(projection)
            source_ids = tuple(
                sorted(
                    set(projection.boundary_source_ids)
                    | set(projection.vertex_source_ids)
                )
            )
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = WebOutlineCandidate(
                    candidate_id=_candidate_id(key),
                    contour=contour,
                    projection=projection,
                    derivations=(derivation,),
                    source_ids=source_ids,
                )
                continue
            representative = (
                existing
                if len(existing.source_ids) <= len(source_ids)
                else WebOutlineCandidate(
                    candidate_id=existing.candidate_id,
                    contour=contour,
                    projection=projection,
                    derivations=existing.derivations,
                    source_ids=source_ids,
                )
            )
            by_key[key] = replace(
                representative,
                derivations=tuple(
                    sorted({*existing.derivations, derivation}, key=str)
                ),
                projection=replace(
                    representative.projection,
                    rule_ids=tuple(
                        sorted(
                            set(existing.projection.rule_ids)
                            | set(projection.rule_ids)
                        )
                    ),
                ),
            )
    candidates = list(
        _merge_coincident_source_representations(tuple(by_key.values()))
    )
    candidates.sort(
        key=lambda candidate: (
            -candidate.area,
            -candidate.longitudinal_span,
            candidate.candidate_id,
        )
    )
    diagnostics = tuple(
        dict.fromkeys(
            (
                *(
                    ("BOX.WEB.DIRECT_FACE_SUBSET_SEARCH.PRUNED",)
                    if direct_face_search_pruned
                    else ()
                ),
                *direct_search.diagnostics,
            )
        )
    )
    return WebCandidateSearchResult(
        candidates=tuple(candidates),
        direct_face_search_pruned=direct_face_search_pruned,
        direct_face_search_complete=direct_search.subset_search_complete,
        connected_course_search_complete=cycle_search.complete,
        diagnostics=diagnostics,
    )
