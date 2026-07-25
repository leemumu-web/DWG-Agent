from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from math import hypot

from .manufacturing_ir import (
    ContourSegmentIR,
    contour_polygon,
    contour_semantic_key,
)
from .metadata import BoxMetadata
from .projection_geometry import (
    ProjectionFaceCandidate,
    enumerate_endpoint_cap_path_cycles,
    enumerate_projection_course_virtual_cycles,
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


def _contour_key(contour: tuple[ContourSegmentIR, ...]) -> str:
    return contour_semantic_key(contour)


def _candidate_id(key: str) -> str:
    return f"web:{sha256(key.encode('ascii')).hexdigest()[:16]}"


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
            key = _contour_key(contour)
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
            by_key[key] = replace(
                existing,
                derivations=tuple(sorted({*existing.derivations, derivation}, key=str)),
                source_ids=tuple(sorted(set(existing.source_ids) | set(source_ids))),
            )
    candidates = list(by_key.values())
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
