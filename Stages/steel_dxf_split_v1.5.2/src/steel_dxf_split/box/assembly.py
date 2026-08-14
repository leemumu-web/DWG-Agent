from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, replace
from functools import lru_cache
from math import ceil, cos, hypot, radians, sin
import re

from shapely.geometry import LineString, Point

from .equivalence import BOX_DRAFTING_RESOLUTION_MM
from .flange_solver import (
    PAIRED_CAP_THICKNESS_BOUNDED_SOURCE_BOUNDARY_RULE_ID,
    FlangeCandidateSearchResult,
    FlangeDerivation,
    FlangeOutlineCandidate,
    enumerate_flange_outline_candidates,
)
from .manufacturing_ir import (
    BoxManufacturingIR,
    EvidenceState,
    FeatureEvidence,
    InnerContourIR,
    PhysicalPlateIR,
    PhysicalPlateRole,
    contour_polygon,
)
from .metadata import BoxMetadata, resolve_box_metadata
from .openings import (
    CircularOpeningKind,
    InnerContourOpeningInventory,
    OpeningCandidateSearchSnapshot,
    OpeningInventoryRejection,
    OpeningOwnershipRoleCandidate,
    OpeningOwnershipScope,
    OpeningVisibility,
    ProjectedCircularOpening,
    ProjectedInnerContourOpening,
    circular_opening_is_contained,
    lower_circular_openings,
    lower_inner_contour_openings,
    project_inner_contour_openings,
    project_part_arc_openings,
    project_circular_openings,
)
from .projection_geometry import (
    CONNECTED_MAXIMAL_MATERIAL_FACE_RULE_ID,
    ProjectionFaceCandidate,
)
from .proofs import (
    ProofDisposition,
    ProofEvidence,
    ProofObligation,
    ProofReport,
    ProofStatus,
)
from .role_hypotheses import (
    FlangeCourseEvidence,
    RoleHypothesisError,
    enumerate_cranked_flange_role_pairs,
    enumerate_straight_flange_role_pairs,
    enumerate_web_role_pairs,
    role_aligned_exact_flange_pair,
)
from .source_ir import SourceDocumentIR, is_hidden_projection_linetype
from .view_frame import PartViewIR
from .view_preprocessing import preprocess_box_views
from .view_solver import ViewAssignmentCandidate, enumerate_view_assignments
from .web_solver import (
    WebCandidateSearchResult,
    WebDerivation,
    WebOutlineCandidate,
    enumerate_web_outline_candidates,
    web_derivation_authority,
)


VIEW_ASSIGNMENT_SECTION_SPAN_TOLERANCE = 0.01


class AssemblyResolutionError(ValueError):
    """No complete, source-backed four-plate BOX assembly could be proved."""


def _section_span_residual(
    assignment: ViewAssignmentCandidate,
    metadata: BoxMetadata,
) -> float:
    if (
        assignment.h_view.frame.transverse_span
        > metadata.profile.value.height * 1.5
    ):
        return assignment.b_span_error
    return assignment.score


def _section_span_assignment_is_consistent(
    assignment: ViewAssignmentCandidate,
    metadata: BoxMetadata,
) -> bool:
    return (
        _section_span_residual(assignment, metadata)
        <= VIEW_ASSIGNMENT_SECTION_SPAN_TOLERANCE
    )


@dataclass(frozen=True, slots=True)
class AssemblyScoreTerm:
    name: str
    value: float
    description: str


@dataclass(frozen=True, slots=True)
class CompleteBoxHypothesis:
    assignment: ViewAssignmentCandidate
    web_candidates: tuple[WebOutlineCandidate, WebOutlineCandidate]
    flange_candidates: tuple[FlangeOutlineCandidate, FlangeOutlineCandidate]
    mir: BoxManufacturingIR
    proof_report: ProofReport
    score_terms: tuple[AssemblyScoreTerm, ...]
    rank_key: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class AssemblySearchResult:
    hypotheses: tuple[CompleteBoxHypothesis, ...]
    search_complete: bool
    diagnostics: tuple[str, ...]
    enumeration_complete: bool = True

    @property
    def best(self) -> CompleteBoxHypothesis:
        if not self.hypotheses:
            raise AssemblyResolutionError("complete BOX hypothesis set is empty")
        return self.hypotheses[0]


@dataclass(frozen=True, slots=True)
class _AssignmentCompileContext:
    web_search: WebCandidateSearchResult
    flange_search: FlangeCandidateSearchResult
    web_bolt_openings: tuple[ProjectedCircularOpening, ...]
    flange_bolt_openings: tuple[ProjectedCircularOpening, ...]
    web_part_openings: tuple[ProjectedCircularOpening, ...]
    flange_part_openings: tuple[ProjectedCircularOpening, ...]
    web_inner_inventory: InnerContourOpeningInventory
    flange_inner_inventory: InnerContourOpeningInventory


@dataclass(frozen=True, slots=True)
class _PartGeometryEdge:
    source_id: str
    line: LineString
    endpoints: tuple[tuple[float, float], tuple[float, float]]


@dataclass(frozen=True, slots=True)
class _PartGeometryComponent:
    source_ids: tuple[str, ...]
    bounds: tuple[float, float, float, float]


_FLANGE_STRENGTH = {
    FlangeDerivation.NEUTRAL_AXIS_FROM_PAIRED_WEB_COURSES: 100.0,
    FlangeDerivation.PAIRED_COURSE_CAP_DEVELOPMENT: 90.0,
    FlangeDerivation.PARALLEL_COURSE_OFFSET_DEVELOPMENT: 90.0,
    FlangeDerivation.CONNECTED_COURSE_CYCLE: 85.0,
    FlangeDerivation.SOURCE_FACE_UNION: 82.0,
    FlangeDerivation.ENDPOINT_CAP_PATH_CYCLE: 80.0,
    FlangeDerivation.COURSE_STATION_RECTANGLE: 30.0,
}


def _strength(
    derivations: Iterable[WebDerivation | FlangeDerivation],
) -> float:
    values = tuple(derivations)
    if not values:
        return 0.0
    if isinstance(values[0], WebDerivation):
        return web_derivation_authority(tuple(values))  # type: ignore[arg-type]
    return max(_FLANGE_STRENGTH[value] for value in values)  # type: ignore[index]


def _candidate_hidden_fraction(
    candidate: WebOutlineCandidate | FlangeOutlineCandidate,
    view: PartViewIR,
) -> float:
    by_id = {entity.source_id: entity for entity in view.entities}
    relevant = tuple(
        by_id[source_id] for source_id in candidate.source_ids if source_id in by_id
    )
    if not relevant:
        return 0.0
    hidden = sum(is_hidden_projection_linetype(entity.linetype) for entity in relevant)
    return hidden / len(relevant)


def _hidden_fraction(
    candidate: WebOutlineCandidate,
    view: PartViewIR,
) -> float:
    """Backward-compatible web-only alias used by existing selection tests."""

    return _candidate_hidden_fraction(candidate, view)


def _opening_side_score(
    candidate: WebOutlineCandidate | FlangeOutlineCandidate,
    opening: ProjectedCircularOpening,
    view: PartViewIR,
) -> tuple[float, int, str]:
    """Rank a candidate for one opening using visibility before tie order."""

    hidden_fraction = _candidate_hidden_fraction(candidate, view)
    if opening.visibility is OpeningVisibility.HIDDEN:
        visibility_score = hidden_fraction
    elif opening.visibility is OpeningVisibility.VISIBLE:
        visibility_score = -hidden_fraction
    else:
        visibility_score = -abs(hidden_fraction - 0.5)
    return (
        visibility_score,
        -len(candidate.source_ids),
        candidate.candidate_id,
    )


def _select_single_side_candidate(
    candidates: tuple[WebOutlineCandidate | FlangeOutlineCandidate, ...],
    opening: ProjectedCircularOpening,
    view: PartViewIR,
) -> int:
    feasible = tuple(
        index
        for index, candidate in enumerate(candidates)
        if circular_opening_is_contained(candidate.projection, opening)
    )
    if not feasible:
        raise AssemblyResolutionError(
            f"opening {opening.source_ids!r} is outside every selected plate"
        )
    if (
        opening.kind is CircularOpeningKind.BOLT_CIRCLE
        and _candidate_indexes_form_coincident_pair(candidates, feasible)
    ):
        if opening.visibility is OpeningVisibility.VISIBLE:
            return feasible[0]
        if opening.visibility is OpeningVisibility.HIDDEN:
            return feasible[-1]
    # ``max`` is deterministic.  The index is the final tie breaker and keeps
    # the established top/left ordering when the two projected faces are
    # geometrically coincident and have no distinct line-type evidence.
    return max(
        feasible,
        key=lambda index: (
            _opening_side_score(candidates[index], opening, view),
            -index,
        ),
    )


def _candidate_indexes_form_coincident_pair(
    candidates: tuple[WebOutlineCandidate | FlangeOutlineCandidate, ...],
    indexes: tuple[int, ...],
    *,
    tolerance_mm: float = BOX_DRAFTING_RESOLUTION_MM,
) -> bool:
    if len(indexes) != 2:
        return False
    first = candidates[indexes[0]].projection.polygon
    second = candidates[indexes[1]].projection.polygon
    return (
        first.boundary.hausdorff_distance(second.boundary) <= tolerance_mm
        and abs(first.area - second.area)
        <= tolerance_mm * max(first.length, second.length, 1.0)
    )


def _assign_openings_to_pair(
    candidates: tuple[WebOutlineCandidate | FlangeOutlineCandidate, ...],
    openings: tuple[ProjectedCircularOpening, ...],
    view: PartViewIR,
    *,
    duplicate_legacy_bolt_openings: bool,
) -> tuple[tuple[ProjectedCircularOpening, ...], ...]:
    """Assign observations to a physical pair exactly once or by legacy proof.

    Existing Bolt-only cases have an established through-hole interpretation:
    one projected Bolt pattern may legitimately be present on both equivalent
    faces.  A Part ARC observation is different: it is a side/depth-bearing
    manufacturing feature and is assigned to one role unless an explicit
    through-hole rule is added later.
    """

    buckets: list[list[ProjectedCircularOpening]] = [
        [] for _ in candidates
    ]
    for opening in openings:
        feasible = tuple(
            index
            for index, candidate in enumerate(candidates)
            if circular_opening_is_contained(candidate.projection, opening)
        )
        if not feasible:
            raise AssemblyResolutionError(
                f"opening {opening.source_ids!r} is outside every selected plate"
            )
        duplicate = (
            duplicate_legacy_bolt_openings
            and opening.kind is CircularOpeningKind.BOLT_CIRCLE
            and opening.representation_multiplicity >= 2
            and _candidate_indexes_form_coincident_pair(candidates, feasible)
        )
        if duplicate:
            selected = feasible
        else:
            selected = (
                _select_single_side_candidate(candidates, opening, view),
            )
        for index in selected:
            buckets[index].append(opening)
    return tuple(tuple(bucket) for bucket in buckets)


@dataclass(frozen=True, slots=True)
class _OuterFlangeCourse:
    side: str
    length: float
    longitudinal_center: float
    transverse: float
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _CrossViewWebCourseEnvelope:
    reference_min_x: float
    reference_max_x: float
    envelope_min_x: float
    envelope_max_x: float
    source_ids: tuple[str, str]
    match_by_span: bool = False


@dataclass(frozen=True, slots=True)
class _CrossViewLongCourse:
    source_id: str
    hidden: bool
    minimum_x: float
    maximum_x: float
    transverse: float

    @property
    def length(self) -> float:
        return self.maximum_x - self.minimum_x


_CROSS_VIEW_WEB_TOTAL_SPAN_RULE_ID = (
    "BOX.WEB.CROSS_VIEW_VISIBLE_HIDDEN_COURSE_ENVELOPE"
)


def _cross_view_web_course_envelopes(
    assignment: ViewAssignmentCandidate,
    *,
    face_separation_mm: float,
) -> tuple[_CrossViewWebCourseEnvelope, ...]:
    """Return spans proved by translated or nested visible/hidden thickness faces."""

    if face_separation_mm <= 0.0:
        return ()
    frame = assignment.b_view.frame
    tolerance = max(
        BOX_DRAFTING_RESOLUTION_MM,
        frame.transverse_span * 0.0002,
    )
    minimum_length = frame.longitudinal_span * 0.40
    courses: list[_CrossViewLongCourse] = []
    for entity in assignment.b_view.entities:
        if entity.kind != "LINE" or entity.start is None or entity.end is None:
            continue
        start = frame.world_to_local(entity.start)
        end = frame.world_to_local(entity.end)
        if abs(end[1] - start[1]) > tolerance:
            continue
        minimum_x = min(start[0], end[0])
        maximum_x = max(start[0], end[0])
        if maximum_x - minimum_x < minimum_length:
            continue
        courses.append(
            _CrossViewLongCourse(
                source_id=entity.source_id,
                hidden=is_hidden_projection_linetype(entity.linetype),
                minimum_x=minimum_x,
                maximum_x=maximum_x,
                transverse=(start[1] + end[1]) / 2.0,
            )
        )

    envelopes: dict[
        tuple[float, float, float, float, tuple[str, str], bool],
        _CrossViewWebCourseEnvelope,
    ] = {}
    for index, first in enumerate(courses):
        for second in courses[index + 1 :]:
            if first.hidden == second.hidden:
                continue
            if (
                abs(abs(first.transverse - second.transverse) - face_separation_mm)
                > tolerance
            ):
                continue
            minimum_shift = second.minimum_x - first.minimum_x
            maximum_shift = second.maximum_x - first.maximum_x
            longitudinal_shift = (minimum_shift + maximum_shift) / 2.0
            translated_equal_courses = (
                abs(first.length - second.length) <= tolerance
                and abs(minimum_shift - maximum_shift) <= tolerance
                and abs(longitudinal_shift) > tolerance
                and abs(longitudinal_shift) <= face_separation_mm * 2.5
            )
            visible = first if not first.hidden else second
            hidden = second if second.hidden else first
            nested_left_overhang = visible.minimum_x - hidden.minimum_x
            nested_right_overhang = hidden.maximum_x - visible.maximum_x
            hidden_course_contains_visible = (
                nested_left_overhang >= -tolerance
                and nested_right_overhang >= -tolerance
                and max(nested_left_overhang, nested_right_overhang) > tolerance
                and nested_left_overhang + nested_right_overhang
                <= face_separation_mm * 2.5 + tolerance
            )
            if not translated_equal_courses and not hidden_course_contains_visible:
                continue
            envelope_min_x = min(first.minimum_x, second.minimum_x)
            envelope_max_x = max(first.maximum_x, second.maximum_x)
            source_ids = tuple(sorted((first.source_id, second.source_id)))
            references = (
                ((first, False), (second, False))
                if translated_equal_courses
                else ((visible, True),)
            )
            for reference, match_by_span in references:
                if (
                    envelope_max_x - envelope_min_x
                    <= reference.length + tolerance
                ):
                    continue
                envelope = _CrossViewWebCourseEnvelope(
                    reference_min_x=reference.minimum_x,
                    reference_max_x=reference.maximum_x,
                    envelope_min_x=envelope_min_x,
                    envelope_max_x=envelope_max_x,
                    source_ids=source_ids,
                    match_by_span=match_by_span,
                )
                key = (
                    round(envelope.reference_min_x, 6),
                    round(envelope.reference_max_x, 6),
                    round(envelope.envelope_min_x, 6),
                    round(envelope.envelope_max_x, 6),
                    envelope.source_ids,
                    envelope.match_by_span,
                )
                envelopes[key] = envelope
    return tuple(envelopes[key] for key in sorted(envelopes))


def _apply_cross_view_web_total_spans(
    candidates: tuple[WebOutlineCandidate, WebOutlineCandidate],
    assignment: ViewAssignmentCandidate,
    *,
    face_separation_mm: float,
) -> tuple[WebOutlineCandidate, WebOutlineCandidate]:
    """Extend proven web terminals to the complete cross-view source envelope."""

    envelopes = _cross_view_web_course_envelopes(
        assignment,
        face_separation_mm=face_separation_mm,
    )
    if not envelopes:
        return candidates

    adjusted: list[WebOutlineCandidate] = []
    for candidate in candidates:
        polygon = contour_polygon(candidate.contour)
        minimum_x, minimum_y, maximum_x, maximum_y = polygon.bounds
        (
            projection_minimum_x,
            _,
            projection_maximum_x,
            _,
        ) = candidate.projection.polygon.bounds
        tolerance = max(
            BOX_DRAFTING_RESOLUTION_MM,
            candidate.projection.grid_size_mm * 2.0,
        )
        coordinate_matches = tuple(
            envelope
            for envelope in envelopes
            if abs(envelope.reference_min_x - projection_minimum_x) <= tolerance
            and abs(envelope.reference_max_x - projection_maximum_x) <= tolerance
        )
        matches = coordinate_matches or tuple(
            envelope
            for envelope in envelopes
            if envelope.match_by_span
            and abs(
                (envelope.reference_max_x - envelope.reference_min_x)
                - (projection_maximum_x - projection_minimum_x)
            )
            <= tolerance
        )
        adjustments = {
            (
                round(envelope.envelope_min_x - envelope.reference_min_x, 6),
                round(envelope.envelope_max_x - envelope.reference_max_x, 6),
            )
            for envelope in matches
        }
        if len(adjustments) != 1 or any(
            abs(segment.bulge) > 1e-12 for segment in candidate.contour
        ):
            adjusted.append(candidate)
            continue
        minimum_adjustment, maximum_adjustment = next(iter(adjustments))
        envelope_min_x = minimum_x + minimum_adjustment
        envelope_max_x = maximum_x + maximum_adjustment
        if (
            envelope_min_x > minimum_x + tolerance
            or envelope_max_x < maximum_x - tolerance
            or envelope_max_x - envelope_min_x
            > maximum_x - minimum_x + face_separation_mm * 2.5
        ):
            adjusted.append(candidate)
            continue

        def move(point: tuple[float, float]) -> tuple[float, float]:
            if abs(point[0] - minimum_x) <= tolerance:
                return (envelope_min_x, point[1])
            if abs(point[0] - maximum_x) <= tolerance:
                return (envelope_max_x, point[1])
            return point

        contour = tuple(
            replace(
                segment,
                start=move(segment.start),
                end=move(segment.end),
                evidence=replace(
                    segment.evidence,
                    state=EvidenceState.INFERRED,
                    rule_ids=tuple(
                        sorted(
                            set(segment.evidence.rule_ids)
                            | {_CROSS_VIEW_WEB_TOTAL_SPAN_RULE_ID}
                        )
                    ),
                    description=(
                        "web terminal extended to visible/hidden cross-view "
                        "source-course envelope"
                    ),
                ),
            )
            for segment in candidate.contour
        )
        adjusted_polygon = contour_polygon(contour)
        if (
            not adjusted_polygon.is_valid
            or not adjusted_polygon.exterior.is_simple
            or abs(
                (adjusted_polygon.bounds[3] - adjusted_polygon.bounds[1])
                - (maximum_y - minimum_y)
            )
            > tolerance
        ):
            adjusted.append(candidate)
            continue
        cross_view_source_ids = tuple(
            sorted({source_id for match in matches for source_id in match.source_ids})
        )
        adjusted.append(
            replace(
                candidate,
                contour=contour,
                projection=replace(
                    candidate.projection,
                    polygon=adjusted_polygon,
                    boundary_source_ids=tuple(
                        sorted(
                            set(candidate.projection.boundary_source_ids)
                            | set(cross_view_source_ids)
                        )
                    ),
                    vertex_source_ids=tuple(
                        sorted(
                            set(candidate.projection.vertex_source_ids)
                            | set(cross_view_source_ids)
                        )
                    ),
                    rule_ids=tuple(
                        sorted(
                            set(candidate.projection.rule_ids)
                            | {_CROSS_VIEW_WEB_TOTAL_SPAN_RULE_ID}
                        )
                    ),
                ),
            )
        )
    return (adjusted[0], adjusted[1])


def _outer_flange_courses(
    assignment: ViewAssignmentCandidate,
    metadata: BoxMetadata,
) -> tuple[_OuterFlangeCourse, _OuterFlangeCourse]:
    frame = assignment.h_view.frame
    minimum_length = max(
        metadata.profile.value.width * 0.25,
        metadata.nominal_length.value * 0.10,
    )
    courses: list[_OuterFlangeCourse] = []
    for entity in assignment.h_view.entities:
        if entity.kind != "LINE" or entity.start is None or entity.end is None:
            continue
        start = frame.world_to_local(entity.start)
        end = frame.world_to_local(entity.end)
        length = hypot(end[0] - start[0], end[1] - start[1])
        if (
            length < minimum_length
            or abs(end[0] - start[0]) / max(length, 1e-9) < 0.965925826
        ):
            continue
        courses.append(
            _OuterFlangeCourse(
                side="",
                length=length,
                longitudinal_center=(start[0] + end[0]) / 2.0,
                transverse=(start[1] + end[1]) / 2.0,
                source_ids=(entity.source_id,),
            )
        )
    if len(courses) < 2:
        raise AssemblyResolutionError("H-view lacks two outer flange courses")
    # A hidden course one flange thickness inside the visible silhouette is
    # still a physical face edge.  Tekla's transformed coordinates can put a
    # nominal thickness plane a few hundredths outside the exact window.
    window = max(1.0, metadata.profile.value.flange_thickness) + 0.05
    bottom_y = min(course.transverse for course in courses)
    top_y = max(course.transverse for course in courses)
    bottom = max(
        (course for course in courses if course.transverse <= bottom_y + window),
        key=lambda course: (course.length, course.source_ids),
    )
    top = max(
        (course for course in courses if course.transverse >= top_y - window),
        key=lambda course: (course.length, course.source_ids),
    )
    return (
        _OuterFlangeCourse(
            "bottom",
            bottom.length,
            bottom.longitudinal_center,
            bottom.transverse,
            bottom.source_ids,
        ),
        _OuterFlangeCourse(
            "top",
            top.length,
            top.longitudinal_center,
            top.transverse,
            top.source_ids,
        ),
    )


def _is_exact_h_course_maximal_flange_candidate(
    candidate: FlangeOutlineCandidate,
    course: _OuterFlangeCourse,
) -> bool:
    return (
        candidate.projection.source_conserved
        and FlangeDerivation.SOURCE_FACE_UNION in candidate.derivations
        and CONNECTED_MAXIMAL_MATERIAL_FACE_RULE_ID in candidate.rule_ids
        and abs(candidate.longitudinal_span - course.length) <= 0.02
    )


def _exact_h_course_maximal_flange_pair_dominates(
    assignment: ViewAssignmentCandidate,
    metadata: BoxMetadata,
    candidates: tuple[FlangeOutlineCandidate, FlangeOutlineCandidate],
) -> bool:
    """Prove that omitted direct subsets cannot outrank this straight flange pair.

    The selected outlines must be complete connected source-face unions whose
    longitudinal spans exactly cover the two H-view outer courses.  A proper
    subset can then only shorten an observed course (which straight-flange
    assembly rejects) or preserve the span with less material.  The selection
    rank uses this same predicate before any derived-channel strength so the
    certificate and the actual winner order cannot disagree.  This certificate
    is deliberately flange-only; web selection has different ranking semantics.
    """

    if assignment.h_view.frame.transverse_span > metadata.profile.value.height * 1.5:
        return False
    try:
        bottom_course, top_course = _outer_flange_courses(assignment, metadata)
    except AssemblyResolutionError:
        return False
    top_candidate, bottom_candidate = candidates
    return all(
        _is_exact_h_course_maximal_flange_candidate(candidate, course)
        for candidate, course in (
            (bottom_candidate, bottom_course),
            (top_candidate, top_course),
        )
    )


def _flange_course_authority_conflicts(
    assignment: ViewAssignmentCandidate,
    metadata: BoxMetadata,
    search: FlangeCandidateSearchResult,
    selected: tuple[FlangeOutlineCandidate, FlangeOutlineCandidate],
) -> tuple[FlangeOutlineCandidate, ...]:
    """Return selected derived roles displaced by exact maximal source faces."""

    if assignment.h_view.frame.transverse_span > metadata.profile.value.height * 1.5:
        return ()
    try:
        bottom_course, top_course = _outer_flange_courses(assignment, metadata)
    except AssemblyResolutionError:
        return ()
    bottom_evidence = FlangeCourseEvidence(
        side="bottom",
        length_mm=bottom_course.length,
        longitudinal_center_mm=bottom_course.longitudinal_center,
        source_ids=bottom_course.source_ids,
    )
    top_evidence = FlangeCourseEvidence(
        side="top",
        length_mm=top_course.length,
        longitudinal_center_mm=top_course.longitudinal_center,
        source_ids=top_course.source_ids,
    )
    bottom_exact = tuple(
        candidate
        for candidate in search.candidates
        if (
            candidate.projection.source_conserved
            and bool(candidate.source_ids)
            and abs(candidate.longitudinal_span - bottom_course.length) <= 0.02
        )
    )
    top_exact = tuple(
        candidate
        for candidate in search.candidates
        if (
            candidate.projection.source_conserved
            and bool(candidate.source_ids)
            and abs(candidate.longitudinal_span - top_course.length) <= 0.02
        )
    )
    top_by_center = sorted(
        (
            float(candidate.projection.polygon.centroid.x),
            candidate.candidate_id,
        )
        for candidate in top_exact
    )
    top_by_id = {candidate.candidate_id: candidate for candidate in top_exact}
    top_centers = [center for center, _candidate_id in top_by_center]
    course_order = (
        top_course.longitudinal_center - bottom_course.longitudinal_center
    )
    alignment_tolerance = BOX_DRAFTING_RESOLUTION_MM
    authoritative_bottom_ids: set[str] = set()
    authoritative_top_ids: set[str] = set()
    for bottom in bottom_exact:
        expected_top_center = (
            float(bottom.projection.polygon.centroid.x) + course_order
        )
        first = bisect_left(
            top_centers,
            expected_top_center - alignment_tolerance,
        )
        last = bisect_right(
            top_centers,
            expected_top_center + alignment_tolerance,
        )
        if first == last:
            continue
        for _center, candidate_id in top_by_center[first:last]:
            top = top_by_id[candidate_id]
            if not role_aligned_exact_flange_pair(
                (top, bottom),
                bottom_course=bottom_evidence,
                top_course=top_evidence,
            ):
                continue
            authoritative_bottom_ids.add(bottom.candidate_id)
            authoritative_top_ids.add(candidate_id)

    if not authoritative_bottom_ids or role_aligned_exact_flange_pair(
        selected,
        bottom_course=bottom_evidence,
        top_course=top_evidence,
    ):
        return ()
    conflicts = tuple(
        candidate
        for candidate, authoritative_ids in (
            (selected[0], authoritative_top_ids),
            (selected[1], authoritative_bottom_ids),
        )
        if candidate.candidate_id not in authoritative_ids
    )
    return conflicts or selected


def _same_projection_domain(
    first: WebOutlineCandidate,
    second: WebOutlineCandidate,
) -> bool:
    """Whether two source projections occupy the same quantized view domain."""

    tolerance = max(
        0.01,
        first.projection.grid_size_mm * 2.0,
        second.projection.grid_size_mm * 2.0,
    )
    return all(
        abs(float(first_value) - float(second_value)) <= tolerance
        for first_value, second_value in zip(
            first.projection.polygon.bounds,
            second.projection.polygon.bounds,
            strict=True,
        )
    )


def _direct_source_boundary_with_complete_course_domain_dominates(
    search: WebCandidateSearchResult,
    selected: tuple[WebOutlineCandidate, WebOutlineCandidate],
) -> bool:
    """Certify direct web outlines despite an exhausted face-subset budget.

    A complete connected-course search proves the projection domain.  Within
    that domain, a selected SOURCE_FACE_UNION retains the source-drawn boundary
    detail (notches, slopes and arcs).  Exhausting enumeration of other face
    subsets therefore cannot invalidate that direct boundary evidence.  The
    certificate is deliberately web-only and applies only to a budgeted search
    that actually ran; derived flange rectangles cannot use it.
    """

    if (
        search.direct_face_search_complete
        or not search.connected_course_search_complete
        or "BOX.PROJECTION.SOURCE_FACE_SUBSET_SEARCH.STATE_BUDGET_EXHAUSTED"
        not in search.diagnostics
    ):
        return False
    if not all(
        candidate.projection.source_conserved
        and WebDerivation.SOURCE_FACE_UNION in candidate.derivations
        for candidate in selected
    ):
        return False
    course_witnesses = tuple(
        candidate
        for candidate in search.candidates
        if candidate.projection.source_conserved
        and WebDerivation.CONNECTED_COURSE_CYCLE in candidate.derivations
    )
    return bool(course_witnesses) and all(
        any(_same_projection_domain(candidate, witness) for witness in course_witnesses)
        for candidate in selected
    )


def _role_evidence(
    source_ids: tuple[str, ...],
    rule_ids: tuple[str, ...],
    role: PhysicalPlateRole,
) -> FeatureEvidence:
    return FeatureEvidence(
        state=EvidenceState.DIRECT if source_ids else EvidenceState.MISSING,
        source_ids=source_ids,
        rule_ids=rule_ids,
        proof_ids=("BOX.PROOF.COMPLETE_FOUR_PLATE_ASSEMBLY",),
        description=f"complete BOX assembly selected {role.value}",
    )


def _lower_inner_openings_for_pair(
    *,
    hypothesis_id: str,
    view: PartViewIR,
    roles: tuple[PhysicalPlateRole, PhysicalPlateRole],
    candidate_ids: tuple[str, str],
    projections: tuple[ProjectionFaceCandidate, ProjectionFaceCandidate],
    candidate_search_projections: tuple[ProjectionFaceCandidate, ...],
    openings: InnerContourOpeningInventory,
    enumerator_id: str,
    enumerator_exhausted: bool,
) -> tuple[
    tuple[tuple[InnerContourIR, ...], tuple[InnerContourIR, ...]],
    tuple[OpeningInventoryRejection, ...],
]:
    """Lower one view's non-circular openings against both physical roles."""

    if not enumerator_exhausted:
        return (
            ((), ()),
            tuple(
                OpeningInventoryRejection(
                    stage="ownership",
                    reason="candidate_search_incomplete",
                    source_ids=opening.source_ids,
                )
                for opening in openings.openings
            ),
        )

    role_candidates = tuple(
        OpeningOwnershipRoleCandidate(
            candidate_id=f"{candidate_id}:{role.value}",
            role=role,
            projection=projection,
        )
        for role, candidate_id, projection in zip(
            roles,
            candidate_ids,
            projections,
            strict=True,
        )
    )
    candidate_search = OpeningCandidateSearchSnapshot.capture(
        view=view,
        candidates=candidate_search_projections,
        enumerator_id=enumerator_id,
        enumerator_exhausted=enumerator_exhausted,
    )
    scope = OpeningOwnershipScope.from_candidate_search(
        hypothesis_id=hypothesis_id,
        view=view,
        candidate_search=candidate_search,
        role_candidates=role_candidates,
    )
    lowered = tuple(
        lower_inner_contour_openings(
            candidate,
            openings,
            ownership_scope=scope,
        )
        for candidate in role_candidates
    )
    rejections = tuple(
        sorted(
            {
                rejection
                for result in lowered
                for rejection in result.rejections
            },
            key=lambda value: (value.stage, value.reason, value.source_ids),
        )
    )
    return ((lowered[0].contours, lowered[1].contours), rejections)


def _make_plate(
    role: PhysicalPlateRole,
    candidate: WebOutlineCandidate | FlangeOutlineCandidate,
    cuts: tuple[object, ...],
    inner_contours: tuple[InnerContourIR, ...],
    metadata: BoxMetadata,
) -> PhysicalPlateIR:
    if isinstance(candidate, WebOutlineCandidate):
        thickness = metadata.profile.value.web_thickness
        rule_ids = tuple(
            f"BOX.WEB.{derivation.name}" for derivation in candidate.derivations
        )
    else:
        thickness = metadata.profile.value.flange_thickness
        rule_ids = tuple(
            sorted(
                set(candidate.rule_ids)
                | {
                    f"BOX.FLANGE.{derivation.name}"
                    for derivation in candidate.derivations
                }
            )
        )
    evidence = _role_evidence(candidate.source_ids, rule_ids, role)
    return PhysicalPlateIR(
        plate_id=f"{metadata.member_mark.value}:{role.value}",
        role=role,
        material=metadata.material.value,
        thickness_mm=thickness,
        outer_segments=candidate.contour,
        circular_cuts=tuple(cuts),  # type: ignore[arg-type]
        inner_contours=inner_contours,
        role_evidence=evidence,
    )


def _part_geometry_edge(
    entity: object,
    view: PartViewIR,
) -> _PartGeometryEdge | None:
    source_id = getattr(entity, "source_id")
    kind = getattr(entity, "kind")
    if kind == "LINE":
        start = getattr(entity, "start")
        end = getattr(entity, "end")
        if start is None or end is None:
            return None
        local_start = view.frame.world_to_local(start)
        local_end = view.frame.world_to_local(end)
        return _PartGeometryEdge(
            source_id=source_id,
            line=LineString((local_start, local_end)),
            endpoints=(local_start, local_end),
        )
    if kind == "ARC":
        center = getattr(entity, "center")
        radius = getattr(entity, "radius")
        start_angle = getattr(entity, "start_angle")
        end_angle = getattr(entity, "end_angle")
        if (
            center is None
            or radius is None
            or radius <= 0.0
            or start_angle is None
            or end_angle is None
        ):
            return None
        sweep = (float(end_angle) - float(start_angle)) % 360.0
        if sweep <= 1e-9:
            sweep = 360.0
        steps = min(
            128,
            max(8, int(ceil(radians(sweep) * float(radius) / 5.0))),
        )
        points = tuple(
            view.frame.world_to_local(
                (
                    float(center[0])
                    + float(radius)
                    * cos(radians(float(start_angle) + sweep * index / steps)),
                    float(center[1])
                    + float(radius)
                    * sin(radians(float(start_angle) + sweep * index / steps)),
                )
            )
            for index in range(steps + 1)
        )
        return _PartGeometryEdge(
            source_id=source_id,
            line=LineString(points),
            endpoints=(points[0], points[-1]),
        )
    if kind in {"LWPOLYLINE", "POLYLINE"}:
        raw_points = getattr(entity, "points")
        if len(raw_points) < 2:
            return None
        points = tuple(
            view.frame.world_to_local((float(point[0]), float(point[1])))
            for point in raw_points
        )
        if getattr(entity, "closed") and points[0] != points[-1]:
            points = (*points, points[0])
        return _PartGeometryEdge(
            source_id=source_id,
            line=LineString(points),
            endpoints=(points[0], points[-1]),
        )
    return None


@lru_cache(maxsize=128)
def _part_geometry_components(
    view: PartViewIR,
) -> tuple[tuple[_PartGeometryComponent, ...], tuple[str, ...]]:
    """Return endpoint-connected Part representation components for one view."""

    part_entities = tuple(
        entity for entity in view.entities if entity.layer.casefold() == "part"
    )
    edges = tuple(
        edge
        for entity in part_entities
        if (edge := _part_geometry_edge(entity, view)) is not None
    )
    unsupported = tuple(
        sorted(
            {
                entity.source_id for entity in part_entities
            }
            - {edge.source_id for edge in edges}
        )
    )
    if not edges:
        return (), unsupported

    tolerance = 0.20
    parent = list(range(len(edges)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first: int, second: int) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parent[max(first_root, second_root)] = min(first_root, second_root)

    def endpoint_meets(
        first: _PartGeometryEdge,
        second: _PartGeometryEdge,
    ) -> bool:
        return any(
            Point(endpoint).distance(second.line) <= tolerance
            for endpoint in first.endpoints
        )

    for first_index, first in enumerate(edges):
        for second_index in range(first_index + 1, len(edges)):
            second = edges[second_index]
            if first.line.distance(second.line) > tolerance:
                continue
            shared = first.line.intersection(second.line)
            if (
                endpoint_meets(first, second)
                or endpoint_meets(second, first)
                or getattr(shared, "length", 0.0) > tolerance
            ):
                union(first_index, second_index)

    grouped: dict[int, list[_PartGeometryEdge]] = {}
    for index, edge in enumerate(edges):
        grouped.setdefault(find(index), []).append(edge)
    components: list[_PartGeometryComponent] = []
    for members in grouped.values():
        bounds = tuple(edge.line.bounds for edge in members)
        components.append(
            _PartGeometryComponent(
                source_ids=tuple(sorted(edge.source_id for edge in members)),
                bounds=(
                    min(value[0] for value in bounds),
                    min(value[1] for value in bounds),
                    max(value[2] for value in bounds),
                    max(value[3] for value in bounds),
                ),
            )
        )
    components.sort(key=lambda component: component.source_ids)
    return tuple(components), unsupported


def _structural_course_context(
    assignment: ViewAssignmentCandidate,
    metadata: BoxMetadata,
    *,
    claimed_source_ids: set[str],
    opening_source_ids: set[str],
    searched_source_ids: set[str] | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Partition Part linework by connected structural representation.

    Selected and exhaustively searched plate boundaries are anchors.  Every
    endpoint-connected projection segment in the same component is structural
    drawing context.  An independent interior loop remains unaccounted, which
    prevents a missed long slot from being relabelled as harmless linework.
    """

    structural: set[str] = set()
    unaccounted: set[str] = set()
    searched = searched_source_ids or set()
    for view in (assignment.h_view, assignment.b_view):
        frame = view.frame
        components, unsupported = _part_geometry_components(view)
        anchors = claimed_source_ids | searched
        boundary_tolerance = max(
            0.20,
            0.002
            * min(
                metadata.profile.value.height,
                metadata.profile.value.width,
            ),
        )
        for component in components:
            active = set(component.source_ids) - opening_source_ids
            if not active:
                continue
            min_x, min_y, max_x, max_y = component.bounds
            touches_view_boundary = (
                abs(min_x - frame.longitudinal_min) <= boundary_tolerance
                or abs(max_x - frame.longitudinal_max) <= boundary_tolerance
                or abs(min_y - frame.transverse_min) <= boundary_tolerance
                or abs(max_y - frame.transverse_max) <= boundary_tolerance
            )
            view_spanning_boundary = (
                max_x - min_x >= 0.75 * frame.longitudinal_span
                and touches_view_boundary
            )
            if active.intersection(anchors) or view_spanning_boundary:
                structural.update(active)
            else:
                unaccounted.update(active)
        unaccounted.update(
            source_id
            for source_id in unsupported
            if source_id not in opening_source_ids
            and source_id not in claimed_source_ids
        )
    return tuple(sorted(structural)), tuple(sorted(unaccounted))


def _projected_circular_context_source_ids(
    assignment: ViewAssignmentCandidate,
    openings: tuple[ProjectedCircularOpening, ...],
) -> tuple[str, ...]:
    """Identify bounded Part projections of already observed circular holes.

    Tekla may draw a Bolt circle in one longitudinal view and a short
    line/arc projection in the orthogonal view.  Matching uses normalized
    longitudinal station and a radius-bounded local envelope; it cannot absorb
    a long interior slot or a main plate course.
    """

    views = {
        assignment.h_view.group_id: assignment.h_view,
        assignment.b_view.group_id: assignment.b_view,
    }
    station_radius_pairs = tuple(
        (
            opening.center[0]
            - views[opening.view_group_id].frame.longitudinal_min,
            opening.radius_mm,
        )
        for opening in openings
        if opening.view_group_id in views
    )
    if not station_radius_pairs:
        return ()

    result: set[str] = set()
    for view in views.values():
        for entity in view.entities:
            if entity.layer.casefold() != "part":
                continue
            local_points: tuple[tuple[float, float], ...]
            if entity.kind == "LINE" and entity.start is not None and entity.end is not None:
                local_points = (
                    view.frame.world_to_local(entity.start),
                    view.frame.world_to_local(entity.end),
                )
            elif (
                entity.kind == "ARC"
                and entity.center is not None
                and entity.radius is not None
                and entity.radius > 0.0
            ):
                center = view.frame.world_to_local(entity.center)
                local_points = (
                    (center[0] - entity.radius, center[1] - entity.radius),
                    (center[0] + entity.radius, center[1] + entity.radius),
                )
            elif entity.kind in {"LWPOLYLINE", "POLYLINE"} and entity.points:
                local_points = tuple(
                    view.frame.world_to_local((point[0], point[1]))
                    for point in entity.points
                )
            else:
                continue
            min_longitudinal = min(point[0] for point in local_points)
            max_longitudinal = max(point[0] for point in local_points)
            min_transverse = min(point[1] for point in local_points)
            max_transverse = max(point[1] for point in local_points)
            station = (
                (min_longitudinal + max_longitudinal) / 2.0
                - view.frame.longitudinal_min
            )
            longitudinal_span = max_longitudinal - min_longitudinal
            transverse_span = max_transverse - min_transverse
            if any(
                abs(station - opening_station) <= 1.25 * radius
                and longitudinal_span <= 2.5 * radius
                and transverse_span <= 2.5 * radius
                for opening_station, radius in station_radius_pairs
            ):
                result.add(entity.source_id)
    return tuple(sorted(result))


def _cross_view_duplicate_context_source_ids(
    assignment: ViewAssignmentCandidate,
    metadata: BoxMetadata,
) -> tuple[str, ...]:
    """Return bounded Part entities repeated at the same station in H and B."""

    def signatures(view: PartViewIR) -> dict[tuple[object, ...], set[str]]:
        result: dict[tuple[object, ...], set[str]] = {}
        maximum_span = 0.25 * min(
            metadata.profile.value.height,
            metadata.profile.value.width,
        )
        for entity in view.entities:
            if entity.layer.casefold() != "part":
                continue
            local_points: tuple[tuple[float, float], ...]
            if entity.kind == "LINE" and entity.start is not None and entity.end is not None:
                local_points = (
                    view.frame.world_to_local(entity.start),
                    view.frame.world_to_local(entity.end),
                )
            elif (
                entity.kind == "ARC"
                and entity.center is not None
                and entity.radius is not None
                and entity.radius > 0.0
            ):
                center = view.frame.world_to_local(entity.center)
                local_points = (
                    (center[0] - entity.radius, center[1] - entity.radius),
                    (center[0] + entity.radius, center[1] + entity.radius),
                )
            else:
                continue
            min_longitudinal = min(point[0] for point in local_points)
            max_longitudinal = max(point[0] for point in local_points)
            min_transverse = min(point[1] for point in local_points)
            max_transverse = max(point[1] for point in local_points)
            longitudinal_span = max_longitudinal - min_longitudinal
            transverse_span = max_transverse - min_transverse
            major_span = max(longitudinal_span, transverse_span)
            if major_span > maximum_span:
                continue
            key = (
                entity.kind,
                is_hidden_projection_linetype(entity.linetype),
                round(
                    (
                        (min_longitudinal + max_longitudinal) / 2.0
                        - view.frame.longitudinal_min
                    )
                    / 0.1
                ),
                round(longitudinal_span / 0.1),
                round(major_span / 0.1),
            )
            result.setdefault(key, set()).add(entity.source_id)
        return result

    h_signatures = signatures(assignment.h_view)
    b_signatures = signatures(assignment.b_view)
    shared = set(h_signatures).intersection(b_signatures)
    return tuple(
        sorted(
            {
                source_id
                for key in shared
                for source_id in (*h_signatures[key], *b_signatures[key])
            }
        )
    )


def _selected_search_domain_measurement(
    *,
    channel: str,
    search: WebCandidateSearchResult | FlangeCandidateSearchResult,
    selected: tuple[WebOutlineCandidate, WebOutlineCandidate]
    | tuple[FlangeOutlineCandidate, FlangeOutlineCandidate],
    assignment: ViewAssignmentCandidate,
    metadata: BoxMetadata,
    source_openings: tuple[ProjectedCircularOpening, ...],
    flange_authority_conflicts: tuple[FlangeOutlineCandidate, ...] = (),
) -> str:
    """Return the one audited completeness result used by proof and lowering."""

    part_arc_role_evidence = tuple(
        opening
        for opening in source_openings
        if opening.kind is CircularOpeningKind.PART_ARC_CIRCLE
        and (
            (
                channel == "web"
                and opening.view_group_id == assignment.h_view.group_id
            )
            or (
                channel == "flange"
                and opening.view_group_id == assignment.b_view.group_id
            )
        )
    )
    selected_part_arc_role = (
        bool(part_arc_role_evidence)
        and len({candidate.candidate_id for candidate in selected}) == 2
        and all(
            candidate.projection.source_conserved and candidate.source_ids
            for candidate in selected
        )
        and (
            channel == "flange"
            or (
                any(
                    WebDerivation.INNER_COURSE_BAND in candidate.derivations
                    for candidate in selected
                )
                and any(
                    WebDerivation.CONNECTED_COURSE_CYCLE
                    in candidate.derivations
                    for candidate in selected
                )
            )
        )
    )
    selected_inner_band = channel == "web" and all(
        candidate.projection.source_conserved
        and WebDerivation.INNER_COURSE_BAND in candidate.derivations
        for candidate in selected
    )
    selected_complete_connected_courses = (
        channel == "web"
        and isinstance(search, WebCandidateSearchResult)
        and search.connected_course_search_complete
        and all(
            candidate.projection.source_conserved
            and WebDerivation.CONNECTED_COURSE_CYCLE in candidate.derivations
            for candidate in selected
        )
    )
    selected_paired_caps = channel == "flange" and all(
        candidate.projection.source_conserved
        and FlangeDerivation.PAIRED_COURSE_CAP_DEVELOPMENT
        in candidate.derivations
        and PAIRED_CAP_THICKNESS_BOUNDED_SOURCE_BOUNDARY_RULE_ID
        in candidate.rule_ids
        for candidate in selected
    )
    selected_neutral_axis = channel == "flange" and all(
        candidate.projection.source_conserved
        and FlangeDerivation.NEUTRAL_AXIS_FROM_PAIRED_WEB_COURSES
        in candidate.derivations
        for candidate in selected
    )
    if channel == "flange" and flange_authority_conflicts:
        return "hard_conflict_exact_source_course_authority"
    if search.direct_face_search_complete:
        return "complete"
    if selected_part_arc_role:
        return "part_arc_role_evidence_dominates"
    if selected_inner_band or selected_paired_caps or selected_neutral_axis:
        return "independent_source_topology_dominates"
    if (
        channel == "web"
        and isinstance(search, WebCandidateSearchResult)
        and _direct_source_boundary_with_complete_course_domain_dominates(
            search,
            selected,  # type: ignore[arg-type]
        )
    ):
        return "direct_source_boundary_with_complete_course_domain"
    if selected_complete_connected_courses:
        return "complete_connected_course_topology_dominates"
    if channel == "flange" and _exact_h_course_maximal_flange_pair_dominates(
        assignment,
        metadata,
        selected,  # type: ignore[arg-type]
    ):
        return "exact_h_course_maximal_flange_dominates"
    return "incomplete"


def _search_measurement_proves_complete_domain(measurement: str) -> bool:
    return measurement not in {
        "incomplete",
        "hard_conflict_exact_source_course_authority",
    }


_SINGLE_BOLT_MARK_RE = re.compile(r"^1[\u03a6Ø⌀](?:\d+(?:\.\d*)?|\.\d+)$")


def _bolt_mark_support_source_ids(
    source: SourceDocumentIR,
    assignment: ViewAssignmentCandidate,
    opening: ProjectedCircularOpening,
    *,
    tolerance_mm: float = 0.05,
) -> tuple[str, ...]:
    views = {
        assignment.h_view.group_id: assignment.h_view,
        assignment.b_view.group_id: assignment.b_view,
    }
    view = views.get(opening.view_group_id)
    if view is None:
        return ()
    world_center = view.frame.local_to_world(opening.center)
    matches: list[tuple[str, ...]] = []
    for group in source.groups_by_layer("BoltMark"):
        entities = source.entities_for_group(group.group_id)
        mark_texts = tuple(
            entity
            for entity in entities
            if entity.text_decoded is not None
            and _SINGLE_BOLT_MARK_RE.fullmatch(
                "".join(entity.text_decoded.split())
            )
            is not None
        )
        if len(mark_texts) != 1:
            continue
        lines = tuple(
            entity
            for entity in entities
            if entity.layer.casefold() == "boltmark"
            and entity.kind == "LINE"
            and entity.start is not None
            and entity.end is not None
        )
        connected_leader = False
        for leader in lines:
            for endpoint, joint in (
                (leader.start, leader.end),
                (leader.end, leader.start),
            ):
                if hypot(
                    endpoint[0] - world_center[0],
                    endpoint[1] - world_center[1],
                ) > tolerance_mm:
                    continue
                if any(
                    other is not leader
                    and (
                        hypot(
                            other.start[0] - joint[0],
                            other.start[1] - joint[1],
                        )
                        <= tolerance_mm
                        or hypot(
                            other.end[0] - joint[0],
                            other.end[1] - joint[1],
                        )
                        <= tolerance_mm
                    )
                    for other in lines
                ):
                    connected_leader = True
                    break
            if connected_leader:
                break
        if connected_leader:
            matches.append(group.source_ids)
    return matches[0] if len(matches) == 1 else ()


def _opening_representation_obligation(
    openings: tuple[ProjectedCircularOpening, ...],
    *,
    source: SourceDocumentIR | None = None,
    assignment: ViewAssignmentCandidate | None = None,
) -> ProofObligation:
    """Require drawing-level visible evidence for the hidden Bolt convention."""

    hidden = tuple(
        opening
        for opening in openings
        if opening.kind is CircularOpeningKind.BOLT_CIRCLE
        and opening.visibility is OpeningVisibility.HIDDEN
    )
    visible = tuple(
        opening
        for opening in openings
        if opening.kind is CircularOpeningKind.BOLT_CIRCLE
        and opening.visibility in {OpeningVisibility.VISIBLE, OpeningVisibility.MIXED}
    )
    bolt_mark_support = {
        opening.source_ids: _bolt_mark_support_source_ids(
            source,
            assignment,
            opening,
        )
        for opening in hidden
        if source is not None and assignment is not None
    }
    unpaired_hidden = (
        ()
        if visible
        else tuple(
            opening
            for opening in hidden
            if not bolt_mark_support.get(opening.source_ids)
        )
    )
    bolt_mark_source_ids = tuple(
        sorted(
            {
                source_id
                for source_ids in bolt_mark_support.values()
                for source_id in source_ids
            }
        )
    )
    return ProofObligation(
        obligation_id="BOX.PROOF.OPENINGS.REPRESENTATION_PAIR",
        status=(
            ProofStatus.NOT_APPLICABLE
            if not hidden
            else ProofStatus.MISSING
            if unpaired_hidden
            else ProofStatus.PASS
        ),
        critical=True,
        evidence=(
            ProofEvidence(
                evidence_id="opening:representation-pair",
                channel="opening_representation",
                source_ids=tuple(
                    sorted(
                        {
                            source_id
                            for opening in openings
                            for source_id in opening.source_ids
                        }
                        | set(bolt_mark_source_ids)
                    )
                ),
                measured=(
                    f"visible={len(visible)};hidden={len(hidden)};"
                    f"bolt_mark_supported={sum(bool(value) for value in bolt_mark_support.values())};"
                    f"unpaired_hidden={len(unpaired_hidden)}"
                ),
                expected=(
                    "hidden Bolt representation supported by visible drawing evidence "
                    "or a connected single-hole BoltMark"
                ),
                tolerance=None,
            ),
        ),
        diagnostic_code=(
            "BOX.OPENING.UNPAIRED_HIDDEN_REPRESENTATION"
            if unpaired_hidden
            else None
        ),
    )


def _proof_report(
    source: SourceDocumentIR,
    metadata: BoxMetadata,
    assignment: ViewAssignmentCandidate,
    plates: tuple[PhysicalPlateIR, ...],
    web_search: WebCandidateSearchResult,
    flange_search: FlangeCandidateSearchResult,
    web_candidates: tuple[WebOutlineCandidate, WebOutlineCandidate],
    flange_candidates: tuple[FlangeOutlineCandidate, FlangeOutlineCandidate],
    source_openings: tuple[ProjectedCircularOpening, ...],
    source_inner_openings: tuple[ProjectedInnerContourOpening, ...],
    opening_rejections: tuple[OpeningInventoryRejection, ...] = (),
) -> ProofReport:
    geometry_sources = tuple(
        sorted(
            {
                source_id
                for plate in plates
                for source_id in plate.role_evidence.source_ids
            }
        )
    )
    incomplete_search_channels: list[str] = []
    independent_topology_compensated_channels: list[str] = []
    complete_connected_course_compensated_channels: list[str] = []
    direct_source_boundary_compensated_channels: list[str] = []
    exact_h_course_compensated_channels: list[str] = []
    part_arc_role_compensated_channels: list[str] = []
    flange_authority_conflicts = _flange_course_authority_conflicts(
        assignment,
        metadata,
        flange_search,
        flange_candidates,
    )
    search_evidence: list[ProofEvidence] = []
    for channel, search, selected in (
        ("web", web_search, web_candidates),
        ("flange", flange_search, flange_candidates),
    ):
        measured = _selected_search_domain_measurement(
            channel=channel,
            search=search,
            selected=selected,
            assignment=assignment,
            metadata=metadata,
            source_openings=source_openings,
            flange_authority_conflicts=flange_authority_conflicts,
        )
        if measured == "part_arc_role_evidence_dominates":
            part_arc_role_compensated_channels.append(channel)
        elif measured == "independent_source_topology_dominates":
            independent_topology_compensated_channels.append(channel)
        elif measured == "direct_source_boundary_with_complete_course_domain":
            direct_source_boundary_compensated_channels.append(channel)
        elif measured == "complete_connected_course_topology_dominates":
            complete_connected_course_compensated_channels.append(channel)
        elif measured == "exact_h_course_maximal_flange_dominates":
            exact_h_course_compensated_channels.append(channel)
        elif measured == "incomplete":
            incomplete_search_channels.append(channel)
        search_evidence.append(
            ProofEvidence(
                evidence_id=f"search:{channel}",
                channel="topology_search",
                source_ids=tuple(
                    sorted(
                        {
                            source_id
                            for candidate in selected
                            for source_id in candidate.source_ids
                        }
                    )
                ),
                measured=measured,
                expected="complete_or_source_topology_dominance",
                tolerance=None,
            )
        )
    search_complete = not incomplete_search_channels
    if incomplete_search_channels:
        search_diagnostic = "BOX.SEARCH.DIRECT_SOURCE_FACE_SUBSEARCH_INCOMPLETE"
    elif part_arc_role_compensated_channels:
        search_diagnostic = "BOX.SEARCH.PART_ARC_ROLE_EVIDENCE_DOMINATES"
    elif independent_topology_compensated_channels:
        search_diagnostic = "BOX.SEARCH.INDEPENDENT_SOURCE_TOPOLOGY_DOMINATES"
    elif direct_source_boundary_compensated_channels:
        search_diagnostic = (
            "BOX.SEARCH.DIRECT_SOURCE_BOUNDARY_WITH_COMPLETE_COURSE_DOMAIN"
        )
    elif exact_h_course_compensated_channels:
        search_diagnostic = "BOX.SEARCH.EXACT_H_COURSE_MAXIMAL_FLANGE_DOMINATES"
    elif complete_connected_course_compensated_channels:
        search_diagnostic = (
            "BOX.SEARCH.COMPLETE_CONNECTED_COURSE_TOPOLOGY_DOMINATES"
        )
    else:
        search_diagnostic = None
    drawing_graph_target = assignment.drawing_graph_target_group_id
    drawing_graph_status = (
        ProofStatus.NOT_APPLICABLE
        if drawing_graph_target is None
        else (
            ProofStatus.PASS
            if assignment.h_view.group_id == drawing_graph_target
            else ProofStatus.CONFLICT
        )
    )
    drawing_graph_evidence = (
        ()
        if drawing_graph_target is None
        else (
            ProofEvidence(
                evidence_id="view:part-mark-leader",
                channel="drawing_graph",
                source_ids=assignment.drawing_graph_source_ids,
                measured=assignment.h_view.group_id,
                expected=drawing_graph_target,
                tolerance=None,
            ),
        )
    )
    assigned_opening_source_ids = {
        source_id
        for plate in plates
        for cut in plate.circular_cuts
        for source_id in cut.evidence.source_ids
    }
    assigned_opening_source_ids.update(
        source_id
        for plate in plates
        for contour in plate.inner_contours
        for source_id in contour.evidence.source_ids
    )
    exterior_boundary_rejections = tuple(
        rejection
        for rejection in opening_rejections
        if rejection.reason
        in {"candidate_exterior_boundary", "candidate_course_context"}
    )
    unresolved_opening_rejections = tuple(
        rejection
        for rejection in opening_rejections
        if rejection.reason
        not in {"candidate_exterior_boundary", "candidate_course_context"}
    )
    exterior_boundary_source_ids = {
        source_id
        for rejection in exterior_boundary_rejections
        for source_id in rejection.source_ids
    }
    manufacturing_inner_openings = tuple(
        opening
        for opening in source_inner_openings
        if not set(opening.source_ids).issubset(exterior_boundary_source_ids)
    )
    accounted_openings = tuple(
        opening
        for opening in (*source_openings, *manufacturing_inner_openings)
        if set(opening.source_ids).issubset(assigned_opening_source_ids)
    )
    expected_opening_count = len(source_openings) + len(manufacturing_inner_openings)
    opening_inventory_status = (
        ProofStatus.PASS
        if len(accounted_openings) == expected_opening_count
        and not unresolved_opening_rejections
        else ProofStatus.CONFLICT
    )
    opening_source_ids = {
        source_id
        for opening in (*source_openings, *source_inner_openings)
        for source_id in opening.source_ids
    }
    projected_circular_context_ids = _projected_circular_context_source_ids(
        assignment,
        source_openings,
    )
    cross_view_duplicate_context_ids = _cross_view_duplicate_context_source_ids(
        assignment,
        metadata,
    )
    structural_source_ids, unaccounted_part_source_ids = _structural_course_context(
        assignment,
        metadata,
        claimed_source_ids=set(geometry_sources),
        searched_source_ids={
            source_id
            for candidate in (
                *web_search.candidates,
                *flange_search.candidates,
            )
            for source_id in candidate.source_ids
        },
        opening_source_ids=(
            opening_source_ids
            | set(projected_circular_context_ids)
            | set(cross_view_duplicate_context_ids)
        ),
    )
    structural_context_status = (
        ProofStatus.PASS
        if (
            search_complete
            and not unresolved_opening_rejections
            and not unaccounted_part_source_ids
        )
        else ProofStatus.CONFLICT
    )
    flange_authority_conflict_ids = {
        candidate.candidate_id for candidate in flange_authority_conflicts
    }
    flange_authority_source_ids = tuple(
        sorted(
            {
                source_id
                for candidate in (*flange_search.candidates, *flange_authority_conflicts)
                if (
                    candidate.candidate_id in flange_authority_conflict_ids
                    or FlangeDerivation.SOURCE_FACE_UNION in candidate.derivations
                )
                for source_id in candidate.source_ids
            }
        )
    )
    selected_role_sources_conserved = all(
        candidate.projection.source_conserved and bool(candidate.source_ids)
        for candidate in (*web_candidates, *flange_candidates)
    )

    # A prismatic BOX has two identical webs.  When an end chamfer line leaks
    # into the H-view polygonization, one web reads ~tf-2 short and the pair is
    # unequal even though both flanges are plain rectangles (a straight box).
    # Such a hypothesis is manufacturing-inconsistent and must not be
    # auto-accepted; it is routed to manual review instead of shipping a short
    # plate.  Skewed members (trapezoid flanges) keep their legitimately
    # unequal webs.
    flange_plates = tuple(
        plate for plate in plates if plate.role.value.startswith("flange")
    )
    web_plates = tuple(
        plate for plate in plates if plate.role.value.startswith("web")
    )

    def _plate_is_axis_rect(plate: PhysicalPlateIR) -> bool:
        coordinates = list(contour_polygon(plate.outer_segments).exterior.coords)
        count = len(coordinates) - 1
        for index in range(count):
            x1, y1 = coordinates[index]
            x2, y2 = coordinates[(index + 1) % count]
            if abs(x1 - x2) > 1.0 and abs(y1 - y2) > 1.0:
                return False
        return True

    flanges_are_plain_rectangles = all(
        _plate_is_axis_rect(plate) for plate in flange_plates
    )
    web_lengths = tuple(
        (lambda b: abs(b[2] - b[0]))(
            contour_polygon(plate.outer_segments).bounds
        )
        for plate in web_plates
    )
    web_length_delta = (
        abs(web_lengths[0] - web_lengths[1]) if len(web_lengths) == 2 else 0.0
    )
    minimum_wall = min(
        metadata.profile.value.web_thickness,
        metadata.profile.value.flange_thickness,
    )
    # Only a chamfer-scale mismatch (~one wall thickness) is a manufacturing
    # inconsistency worth rejecting.  A much larger web difference (e.g. a
    # long member whose broken-section lines leak into the outline) is a
    # separate real geometry case and must keep its review path.
    chamfer_scale_uneven_webs = (
        flanges_are_plain_rectangles
        and 2.0 < web_length_delta <= 2.0 * minimum_wall
    )
    web_consistency_status = (
        ProofStatus.CONFLICT
        if chamfer_scale_uneven_webs
        else ProofStatus.PASS
    )
    obligations = (
        ProofObligation(
            obligation_id="BOX.PROOF.ASSEMBLY.WEB_LENGTH_CONSISTENCY",
            status=web_consistency_status,
            critical=True,
            evidence=(
                ProofEvidence(
                    evidence_id="assembly:web-length-consistency",
                    channel="manufacturing",
                    source_ids=tuple(
                        sorted(
                            {
                                source_id
                                for plate in web_plates
                                for source_id in plate.role_evidence.source_ids
                            }
                        )
                    )
                    or geometry_sources,
                    measured=(
                        f"web_lengths={tuple(round(v, 2) for v in web_lengths)};"
                        f"flanges_plain={flanges_are_plain_rectangles}"
                    ),
                    expected="consistent_web_lengths",
                    tolerance=2.0,
                ),
            ),
            diagnostic_code=(
                "BOX.ASSEMBLY.UNEVEN_WEB_LENGTHS"
                if web_consistency_status is ProofStatus.CONFLICT
                else None
            ),
        ),
        ProofObligation(
            obligation_id="BOX.PROOF.METADATA.UNIQUE",
            status=ProofStatus.PASS,
            critical=True,
            evidence=tuple(
                ProofEvidence(
                    evidence_id=f"metadata:{index}",
                    channel="metadata",
                    source_ids=(field.source_id,),
                    measured=field.normalized_text,
                    expected=field.normalized_text,
                    tolerance=None,
                )
                for index, field in enumerate(metadata.fields)
            ),
        ),
        ProofObligation(
            obligation_id="BOX.PROOF.VIEW_ASSIGNMENT.SECTION_SPANS",
            status=(
                ProofStatus.PASS
                if _section_span_assignment_is_consistent(assignment, metadata)
                else ProofStatus.CONFLICT
            ),
            critical=True,
            evidence=(
                ProofEvidence(
                    evidence_id="view:H+B",
                    channel="projection",
                    source_ids=(assignment.h_view.group_id, assignment.b_view.group_id),
                    measured=_section_span_residual(assignment, metadata),
                    expected=0.0,
                    tolerance=VIEW_ASSIGNMENT_SECTION_SPAN_TOLERANCE,
                ),
            ),
            diagnostic_code=(
                None
                if _section_span_assignment_is_consistent(assignment, metadata)
                else "BOX.VIEW.SECTION_SPAN_CONFLICT"
            ),
        ),
        ProofObligation(
            obligation_id="BOX.PROOF.ASSEMBLY.FOUR_PHYSICAL_ROLES",
            status=ProofStatus.PASS,
            critical=True,
            evidence=(
                ProofEvidence(
                    evidence_id="assembly:four_roles",
                    channel="manufacturing",
                    source_ids=geometry_sources,
                    measured=len(plates),
                    expected=4,
                    tolerance=0.0,
                ),
            ),
        ),
        ProofObligation(
            obligation_id="BOX.PROOF.ROLE.SOURCE_CONSERVATION",
            status=(
                ProofStatus.PASS
                if selected_role_sources_conserved
                else ProofStatus.CONFLICT
            ),
            critical=True,
            evidence=(
                ProofEvidence(
                    evidence_id="assembly:role-source-conservation",
                    channel="projection_source_conservation",
                    source_ids=geometry_sources,
                    measured=(
                        "source_conserved"
                        if selected_role_sources_conserved
                        else "source_conservation_unproven"
                    ),
                    expected="source_conserved",
                    tolerance=None,
                ),
            ),
            diagnostic_code=(
                "BOX.ROLE.SOURCE_CONSERVATION_UNPROVEN"
                if not selected_role_sources_conserved
                else None
            ),
        ),
        ProofObligation(
            obligation_id="BOX.PROOF.OPENINGS.CONTAINED",
            status=opening_inventory_status,
            critical=True,
            evidence=(
                ProofEvidence(
                    evidence_id="assembly:openings",
                    channel="topology",
                    source_ids=(
                        tuple(
                        sorted(
                            {
                                source_id
                                for opening in (*source_openings, *source_inner_openings)
                                for source_id in opening.source_ids
                            }
                        )
                        )
                        or geometry_sources
                    ),
                    measured=len(accounted_openings),
                    expected=expected_opening_count,
                    tolerance=0.0,
                ),
                *(
                    ProofEvidence(
                        evidence_id=(
                            f"opening-rejection:{index}:{rejection.stage}:"
                            f"{rejection.reason}"
                        ),
                        channel="opening_inventory_rejection",
                        source_ids=rejection.source_ids,
                        measured=f"{rejection.stage}:{rejection.reason}",
                        expected=(
                            "every opening-like source loop is classified and lowered"
                        ),
                        tolerance=None,
                    )
                    for index, rejection in enumerate(unresolved_opening_rejections)
                ),
                *(
                    ProofEvidence(
                        evidence_id=(
                            f"opening-classification:{index}:"
                            f"{rejection.reason}"
                        ),
                        channel="plate_exterior_classification",
                        source_ids=rejection.source_ids,
                        measured=rejection.reason,
                        expected=rejection.reason,
                        tolerance=0.15,
                    )
                    for index, rejection in enumerate(
                        exterior_boundary_rejections
                    )
                ),
            ),
        ),
        _opening_representation_obligation(
            source_openings,
            source=source,
            assignment=assignment,
        ),
        ProofObligation(
            obligation_id="BOX.PROOF.FLANGE.EXACT_SOURCE_COURSE_AUTHORITY",
            status=(
                ProofStatus.CONFLICT
                if flange_authority_conflicts
                else ProofStatus.PASS
            ),
            critical=True,
            evidence=(
                ProofEvidence(
                    evidence_id="flange:exact-source-course-authority",
                    channel="course_authority",
                    source_ids=flange_authority_source_ids or geometry_sources,
                    measured=(
                        ",".join(
                            candidate.candidate_id
                            for candidate in flange_authority_conflicts
                        )
                        or "selected_roles_preserve_source_authority"
                    ),
                    expected="selected_roles_preserve_source_authority",
                    tolerance=None,
                ),
            ),
            diagnostic_code=(
                "BOX.FLANGE.DERIVED_ROLE_DISPLACED_BY_EXACT_SOURCE_FACE"
                if flange_authority_conflicts
                else None
            ),
        ),
        ProofObligation(
            obligation_id="BOX.PROOF.VIEW.STRUCTURAL_COURSE_CONTEXT",
            status=structural_context_status,
            critical=True,
            evidence=(
                ProofEvidence(
                    evidence_id="view:structural-course-context",
                    channel="source_partition",
                    source_ids=(
                        tuple(
                            sorted(
                                set(structural_source_ids)
                                | set(projected_circular_context_ids)
                                | set(cross_view_duplicate_context_ids)
                            )
                        )
                        or geometry_sources
                    ),
                    measured=(
                        f"structural={len(structural_source_ids)};"
                        f"circular_projection={len(projected_circular_context_ids)};"
                        "cross_view_duplicate="
                        f"{len(cross_view_duplicate_context_ids)};"
                        f"unaccounted={len(unaccounted_part_source_ids)}"
                    ),
                    expected="unaccounted=0",
                    tolerance=0.0,
                ),
                *(
                    (
                        ProofEvidence(
                            evidence_id="view:unaccounted-part-context",
                            channel="source_partition_conflict",
                            source_ids=unaccounted_part_source_ids,
                            measured=len(unaccounted_part_source_ids),
                            expected=0,
                            tolerance=0.0,
                        ),
                    )
                    if unaccounted_part_source_ids
                    else ()
                ),
            ),
            diagnostic_code=(
                "BOX.VIEW.UNACCOUNTED_PART_GEOMETRY"
                if unaccounted_part_source_ids
                else None
            ),
        ),
        ProofObligation(
            obligation_id="BOX.PROOF.VIEW.PART_MARK_H_ROLE",
            status=drawing_graph_status,
            critical=True,
            evidence=drawing_graph_evidence,
            diagnostic_code=(
                "BOX.VIEW.PART_MARK_H_ROLE_CONFLICT"
                if drawing_graph_status is ProofStatus.CONFLICT
                else None
            ),
        ),
        ProofObligation(
            obligation_id="BOX.PROOF.SEARCH.DIRECT_SOURCE_FACE_DOMAIN",
            status=(ProofStatus.PASS if search_complete else ProofStatus.INCOMPLETE),
            critical=True,
            evidence=tuple(search_evidence),
            diagnostic_code=search_diagnostic,
        ),
    )
    return ProofReport(obligations=obligations, search_complete=search_complete)


def _build_assignment_context(
    source: SourceDocumentIR,
    metadata: BoxMetadata,
    assignment: ViewAssignmentCandidate,
) -> _AssignmentCompileContext:
    web_search = enumerate_web_outline_candidates(assignment, metadata)
    flange_search = enumerate_flange_outline_candidates(assignment, metadata)
    web_bolt_openings = project_circular_openings(source, assignment.h_view)
    flange_bolt_openings = project_circular_openings(source, assignment.b_view)
    return _AssignmentCompileContext(
        web_search=web_search,
        flange_search=flange_search,
        web_bolt_openings=web_bolt_openings,
        flange_bolt_openings=flange_bolt_openings,
        web_part_openings=project_part_arc_openings(source, assignment.h_view),
        flange_part_openings=project_part_arc_openings(source, assignment.b_view),
        web_inner_inventory=project_inner_contour_openings(
            assignment.h_view,
            circular_openings=web_bolt_openings,
        ),
        flange_inner_inventory=project_inner_contour_openings(
            assignment.b_view,
            circular_openings=flange_bolt_openings,
        ),
    )


def _compile_assignment(
    source: SourceDocumentIR,
    metadata: BoxMetadata,
    assignment: ViewAssignmentCandidate,
    *,
    compile_context: _AssignmentCompileContext,
    web_pair: tuple[WebOutlineCandidate, WebOutlineCandidate],
    flange_pair: tuple[FlangeOutlineCandidate, FlangeOutlineCandidate],
) -> CompleteBoxHypothesis:
    context = compile_context
    web_search = context.web_search
    flange_search = context.flange_search
    web_bolt_openings = context.web_bolt_openings
    flange_bolt_openings = context.flange_bolt_openings
    web_part_openings = context.web_part_openings
    flange_part_openings = context.flange_part_openings
    web_inner_inventory = context.web_inner_inventory
    flange_inner_inventory = context.flange_inner_inventory
    source_openings = (
        *web_bolt_openings,
        *flange_bolt_openings,
        *web_part_openings,
        *flange_part_openings,
    )
    asymmetric_part_evidence = bool(web_part_openings or flange_part_openings)

    web_roles = (PhysicalPlateRole.WEB_LEFT, PhysicalPlateRole.WEB_RIGHT)
    flange_roles = (PhysicalPlateRole.FLANGE_TOP, PhysicalPlateRole.FLANGE_BOTTOM)
    web_buckets = _assign_openings_to_pair(
        web_pair,
        (*web_bolt_openings, *web_part_openings),
        assignment.h_view,
        duplicate_legacy_bolt_openings=not asymmetric_part_evidence,
    )
    flange_buckets = _assign_openings_to_pair(
        flange_pair,
        (*flange_bolt_openings, *flange_part_openings),
        assignment.b_view,
        duplicate_legacy_bolt_openings=not asymmetric_part_evidence,
    )
    hypothesis_scope_id = (
        f"box-native:{assignment.signature}:"
        f"{web_pair[0].candidate_id}:{web_pair[1].candidate_id}:"
        f"{flange_pair[0].candidate_id}:{flange_pair[1].candidate_id}"
    )
    flange_authority_conflicts = _flange_course_authority_conflicts(
        assignment,
        metadata,
        flange_search,
        flange_pair,
    )
    web_search_measurement = _selected_search_domain_measurement(
        channel="web",
        search=web_search,
        selected=web_pair,
        assignment=assignment,
        metadata=metadata,
        source_openings=source_openings,
    )
    flange_search_measurement = _selected_search_domain_measurement(
        channel="flange",
        search=flange_search,
        selected=flange_pair,
        assignment=assignment,
        metadata=metadata,
        source_openings=source_openings,
        flange_authority_conflicts=flange_authority_conflicts,
    )
    web_inner_lowering = (
        _lower_inner_openings_for_pair(
            hypothesis_id=hypothesis_scope_id,
            view=assignment.h_view,
            roles=web_roles,
            candidate_ids=tuple(candidate.candidate_id for candidate in web_pair),
            projections=tuple(candidate.projection for candidate in web_pair),
            candidate_search_projections=tuple(
                candidate.projection for candidate in web_search.candidates
            ),
            openings=web_inner_inventory,
            enumerator_id="box.web_outline_candidates.v1",
            enumerator_exhausted=_search_measurement_proves_complete_domain(
                web_search_measurement
            ),
        )
        if web_inner_inventory.openings
        else (((), ()), ())
    )
    flange_inner_lowering = (
        _lower_inner_openings_for_pair(
            hypothesis_id=hypothesis_scope_id,
            view=assignment.b_view,
            roles=flange_roles,
            candidate_ids=tuple(candidate.candidate_id for candidate in flange_pair),
            projections=tuple(candidate.projection for candidate in flange_pair),
            candidate_search_projections=tuple(
                candidate.projection for candidate in flange_search.candidates
            ),
            openings=flange_inner_inventory,
            enumerator_id="box.flange_outline_candidates.v1",
            enumerator_exhausted=_search_measurement_proves_complete_domain(
                flange_search_measurement
            ),
        )
        if flange_inner_inventory.openings
        else (((), ()), ())
    )
    web_inner_buckets, web_inner_rejections = web_inner_lowering
    flange_inner_buckets, flange_inner_rejections = flange_inner_lowering
    manufacturing_web_pair = _apply_cross_view_web_total_spans(
        web_pair,
        assignment,
        face_separation_mm=metadata.profile.value.flange_thickness,
    )
    opening_rejections = tuple(
        sorted(
            {
                *web_inner_inventory.rejections,
                *flange_inner_inventory.rejections,
                *web_inner_rejections,
                *flange_inner_rejections,
            },
            key=lambda value: (value.stage, value.reason, value.source_ids),
        )
    )
    plates = tuple(
        _make_plate(
            role,
            candidate,
            lower_circular_openings(candidate.projection, openings),
            inner_contours,
            metadata,
        )
        for role, candidate, openings, inner_contours in zip(
            web_roles,
            manufacturing_web_pair,
            web_buckets,
            web_inner_buckets,
            strict=True,
        )
    ) + tuple(
        _make_plate(
            role,
            candidate,
            lower_circular_openings(candidate.projection, openings),
            inner_contours,
            metadata,
        )
        for role, candidate, openings, inner_contours in zip(
            flange_roles,
            flange_pair,
            flange_buckets,
            flange_inner_buckets,
            strict=True,
        )
    )
    proof = _proof_report(
        metadata,
        assignment,
        plates,
        web_search,
        flange_search,
        manufacturing_web_pair,
        flange_pair,
        source_openings,
        (*web_inner_inventory.openings, *flange_inner_inventory.openings),
        opening_rejections,
    )
    mir = BoxManufacturingIR.create(
        part_number=metadata.member_mark.value,
        profile=metadata.profile.value.canonical,
        nominal_length_mm=metadata.nominal_length.value,
        material=metadata.material.value,
        physical_plates=plates,
        proof_disposition=proof.disposition.value,
        proof_ids=tuple(obligation.obligation_id for obligation in proof.obligations),
    )
    direct_features = sum(
        segment.evidence.state is EvidenceState.DIRECT
        for plate in plates
        for segment in plate.outer_segments
    )
    inferred_features = sum(
        segment.evidence.state is EvidenceState.INFERRED
        for plate in plates
        for segment in plate.outer_segments
    )
    bolt_circles = len(web_bolt_openings) + len(flange_bolt_openings)
    part_arc_openings = len(web_part_openings) + len(flange_part_openings)
    inner_contour_openings = (
        len(web_inner_inventory.openings)
        + len(flange_inner_inventory.openings)
    )
    rank_key = (
        assignment.score,
        -assignment.drawing_graph_score,
        -float(direct_features),
        float(inferred_features),
        -float(bolt_circles),
        -float(part_arc_openings),
        -float(inner_contour_openings),
        -float(bool(web_part_openings)),
        -sum(
            _strength(candidate.derivations)
            for candidate in (*manufacturing_web_pair, *flange_pair)
        ),
    )
    return CompleteBoxHypothesis(
        assignment=assignment,
        web_candidates=manufacturing_web_pair,
        flange_candidates=flange_pair,
        mir=mir,
        proof_report=proof,
        score_terms=(
            AssemblyScoreTerm(
                "view_span_residual",
                assignment.score,
                "H/B section-span residual; lower is stronger",
            ),
            AssemblyScoreTerm(
                "drawing_graph_h_role",
                assignment.drawing_graph_score,
                "PartMark leader target agrees with the H-view role",
            ),
            AssemblyScoreTerm(
                "direct_manufacturing_edges",
                float(direct_features),
                "source-direct selected contour edges",
            ),
            AssemblyScoreTerm(
                "bolt_circle_evidence",
                float(bolt_circles),
                "deduplicated Bolt circles associated to selected views",
            ),
        ),
        rank_key=rank_key,
    )


def _compile_assignment_hypotheses(
    source: SourceDocumentIR,
    metadata: BoxMetadata,
    assignment: ViewAssignmentCandidate,
) -> tuple[tuple[CompleteBoxHypothesis, ...], bool, tuple[str, ...]]:
    """Compile every structurally feasible four-role assignment for one H/B view."""

    context = _build_assignment_context(source, metadata, assignment)
    diagnostics: list[str] = []
    web_openings = (
        *context.web_bolt_openings,
        *context.web_part_openings,
    )
    try:
        web_pairs = enumerate_web_role_pairs(
            context.web_search.candidates,
            web_openings,
            assignment.h_view,
            metadata,
            part_arc_evidence=bool(context.web_part_openings),
        )
    except RoleHypothesisError as error:
        return (), True, (f"BOX.ROLE.WEB.HARD_CONFLICT:{error}",)

    flange_pairs_by_id: dict[
        str,
        tuple[FlangeOutlineCandidate, FlangeOutlineCandidate],
    ] = {}
    cranked = (
        assignment.h_view.frame.transverse_span
        > metadata.profile.value.height * 1.5
    )
    if not cranked:
        try:
            outer_courses = _outer_flange_courses(assignment, metadata)
            flange_courses = tuple(
                FlangeCourseEvidence(
                    side=course.side,
                    length_mm=course.length,
                    longitudinal_center_mm=course.longitudinal_center,
                    source_ids=course.source_ids,
                )
                for course in outer_courses
            )
            flange_search = enumerate_straight_flange_role_pairs(
                context.flange_search.candidates,
                flange_courses,  # type: ignore[arg-type]
                metadata,
            )
            for first, second in flange_search.pairs:
                flange_pairs_by_id[
                    f"{first.candidate_id}::{second.candidate_id}"
                ] = (first, second)
            flange_enumerator_exhausted = flange_search.enumerator_exhausted
        except (AssemblyResolutionError, RoleHypothesisError) as error:
            return (), True, (f"BOX.ROLE.FLANGE.HARD_CONFLICT:{error}",)
    else:
        flange_enumerator_exhausted = True
        for web_pair in web_pairs.pairs:
            try:
                flange_search = enumerate_cranked_flange_role_pairs(
                    context.flange_search.candidates,
                    web_pair,
                    context.web_bolt_openings,
                )
            except RoleHypothesisError as error:
                diagnostics.append(
                    "BOX.ROLE.FLANGE.CRANKED_SUPPORT_REJECTED:"
                    f"{error}"
                )
                continue
            diagnostics.extend(flange_search.diagnostics)
            flange_enumerator_exhausted = (
                flange_enumerator_exhausted
                and flange_search.enumerator_exhausted
            )
            for first, second in flange_search.pairs:
                flange_pairs_by_id[
                    f"{first.candidate_id}::{second.candidate_id}"
                ] = (first, second)
        if not flange_pairs_by_id:
            return (), True, tuple(diagnostics) or (
                "BOX.ROLE.FLANGE.HARD_CONFLICT:no cranked flange pair",
            )

    hypotheses: list[CompleteBoxHypothesis] = []
    compilation_complete = True
    for web_pair in web_pairs.pairs:
        for flange_pair_id in sorted(flange_pairs_by_id):
            flange_pair = flange_pairs_by_id[flange_pair_id]
            try:
                hypotheses.append(
                    _compile_assignment(
                        source,
                        metadata,
                        assignment,
                        compile_context=context,
                        web_pair=web_pair,
                        flange_pair=flange_pair,
                    )
                )
            except AssemblyResolutionError as error:
                compilation_complete = False
                diagnostics.append(
                    "BOX.ROLE.COMBINATION.EVALUATION_FAILED:"
                    f"{web_pair[0].candidate_id}::{web_pair[1].candidate_id}:"
                    f"{flange_pair_id}:{error}"
                )
    hypotheses_by_id = {
        (
            hypothesis.assignment.signature,
            tuple(candidate.candidate_id for candidate in hypothesis.web_candidates),
            tuple(candidate.candidate_id for candidate in hypothesis.flange_candidates),
        ): hypothesis
        for hypothesis in hypotheses
    }
    return (
        tuple(hypotheses_by_id[key] for key in sorted(hypotheses_by_id)),
        (
            web_pairs.enumerator_exhausted
            and flange_enumerator_exhausted
            and compilation_complete
        ),
        tuple(diagnostics),
    )


def solve_complete_box(
    source: SourceDocumentIR,
    metadata: BoxMetadata | None = None,
) -> AssemblySearchResult:
    """Jointly solve view assignment and all four physical BOX plate roles."""

    resolved_metadata = metadata or resolve_box_metadata(source)
    preprocessed = preprocess_box_views(source, resolved_metadata)
    assignments = enumerate_view_assignments(
        preprocessed.views,
        resolved_metadata,
        source=preprocessed.source,
    )
    if not assignments:
        raise AssemblyResolutionError("no H/B view assignment candidates")
    hypotheses: list[CompleteBoxHypothesis] = []
    diagnostics = list(preprocessed.diagnostics)
    enumeration_complete = True
    has_section_span_consistent_assignment = any(
        _section_span_assignment_is_consistent(assignment, resolved_metadata)
        for assignment in assignments
    )
    for assignment in assignments:
        if (
            has_section_span_consistent_assignment
            and not _section_span_assignment_is_consistent(
                assignment,
                resolved_metadata,
            )
        ):
            residual = _section_span_residual(assignment, resolved_metadata)
            diagnostics.append(
                "BOX.VIEW.PRUNE.SECTION_SPAN_CONFLICT:"
                f"{assignment.signature}:"
                f"score={residual:.12g}:"
                f"tolerance={VIEW_ASSIGNMENT_SECTION_SPAN_TOLERANCE:.12g}"
            )
            continue
        assignment_hypotheses, assignment_complete, assignment_diagnostics = (
            _compile_assignment_hypotheses(
                preprocessed.source,
                resolved_metadata,
                assignment,
            )
        )
        enumeration_complete = enumeration_complete and assignment_complete
        diagnostics.extend(
            f"{diagnostic}:{assignment.signature}"
            for diagnostic in assignment_diagnostics
        )
        hypotheses.extend(assignment_hypotheses)
        for hypothesis in assignment_hypotheses:
            if not hypothesis.proof_report.search_complete:
                diagnostics.append(
                    "BOX.ASSEMBLY.SEARCH_INCOMPLETE:"
                    f"{assignment.signature}:"
                    f"{','.join(hypothesis.proof_report.blocking_obligation_ids)}"
                )
    hypotheses.sort(
        key=lambda hypothesis: (
            # A prismatic BOX has two identical webs.  A skewed-end chamfer
            # line can leak into one view's polygonization and read one web
            # shorter by ~tf-2; the equal-length hypothesis is then the
            # physically correct one even when its H/B direction disagrees
            # with the PartMark leader (square members are direction-free and
            # non-square members are already pinned by the section-span score).
            -float(
                abs(
                    hypothesis.web_candidates[0].longitudinal_span
                    - hypothesis.web_candidates[1].longitudinal_span
                )
                <= 1.0
            ),
            any(
                obligation.obligation_id == "BOX.PROOF.VIEW.PART_MARK_H_ROLE"
                and obligation.status is ProofStatus.CONFLICT
                for obligation in hypothesis.proof_report.obligations
            ),
            not hypothesis.proof_report.search_complete,
            # A hypothesis whose critical proof obligations already conflict
            # must never surface as the search ``best`` ahead of a complete,
            # auto-acceptable candidate with the same score.
            hypothesis.proof_report.disposition is not ProofDisposition.AUTO_ACCEPT,
            hypothesis.rank_key,
            hypothesis.assignment.signature,
            hypothesis.mir.fingerprint,
        )
    )
    if not hypotheses:
        raise AssemblyResolutionError("; ".join(diagnostics) or "assembly failed")

    def search_domain_is_closed(hypothesis: CompleteBoxHypothesis) -> bool:
        if hypothesis.proof_report.search_complete:
            return True
        return any(
            obligation.critical and obligation.status is ProofStatus.CONFLICT
            for obligation in hypothesis.proof_report.obligations
        )

    return AssemblySearchResult(
        hypotheses=tuple(hypotheses),
        search_complete=(
            enumeration_complete
            and all(search_domain_is_closed(hypothesis) for hypothesis in hypotheses)
        ),
        diagnostics=tuple(diagnostics),
        enumeration_complete=enumeration_complete,
    )
