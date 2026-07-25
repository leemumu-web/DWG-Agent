from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations_with_replacement
from math import hypot
from typing import Protocol

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
    PhysicalPlateIR,
    PhysicalPlateRole,
)
from .metadata import BoxMetadata, resolve_box_metadata
from .openings import (
    CircularOpeningKind,
    OpeningVisibility,
    ProjectedCircularOpening,
    circular_opening_is_contained,
    lower_circular_openings,
    project_part_arc_openings,
    project_circular_openings,
)
from .projection_geometry import CONNECTED_MAXIMAL_MATERIAL_FACE_RULE_ID
from .proofs import (
    ProofEvidence,
    ProofObligation,
    ProofReport,
    ProofStatus,
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
)


class AssemblyResolutionError(ValueError):
    """No complete, source-backed four-plate BOX assembly could be proved."""


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

    @property
    def best(self) -> CompleteBoxHypothesis:
        if not self.hypotheses:
            raise AssemblyResolutionError("complete BOX hypothesis set is empty")
        return self.hypotheses[0]


@dataclass(frozen=True, slots=True)
class _CandidateCluster[CandidateT: "_AreaCandidate"]:
    members: tuple[CandidateT, ...]
    representative: CandidateT
    strength: float


class _AreaCandidate(Protocol):
    @property
    def area(self) -> float: ...


_WEB_STRENGTH = {
    WebDerivation.BOUNDED_VIRTUAL_COURSE_CYCLE: 95.0,
    WebDerivation.CONNECTED_COURSE_CYCLE: 90.0,
    WebDerivation.INNER_COURSE_BAND: 85.0,
    WebDerivation.SOURCE_FACE_UNION: 80.0,
    WebDerivation.ENDPOINT_CAP_PATH_CYCLE: 75.0,
}

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
        return max(_WEB_STRENGTH[value] for value in values)  # type: ignore[index]
    return max(_FLANGE_STRENGTH[value] for value in values)  # type: ignore[index]


def _cluster_by_area[CandidateT: _AreaCandidate](
    candidates: tuple[CandidateT, ...],
    *,
    relative_tolerance: float,
    representative_key: object,
    strength_getter: object,
) -> tuple[_CandidateCluster[CandidateT], ...]:
    choose = representative_key
    get_strength = strength_getter
    assert callable(choose)
    assert callable(get_strength)
    remaining = set(range(len(candidates)))
    clusters: list[_CandidateCluster[CandidateT]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        members = {seed}
        stack = [seed]
        while stack:
            index = stack.pop()
            area = float(candidates[index].area)
            linked = tuple(
                other
                for other in remaining
                if abs(float(candidates[other].area) - area)
                / max(area, float(candidates[other].area), 1.0)
                <= relative_tolerance
            )
            for other in linked:
                remaining.remove(other)
                members.add(other)
                stack.append(other)
        materialized = tuple(candidates[index] for index in sorted(members))
        clusters.append(
            _CandidateCluster(
                members=materialized,
                representative=max(materialized, key=choose),
                strength=max(float(get_strength(item)) for item in materialized),
            )
        )
    return tuple(clusters)


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


def _select_part_arc_web_pair(
    candidates: tuple[WebOutlineCandidate, ...],
    openings: tuple[ProjectedCircularOpening, ...],
    assignment: ViewAssignmentCandidate,
    metadata: BoxMetadata,
) -> tuple[WebOutlineCandidate, WebOutlineCandidate]:
    """Select two distinct web hypotheses when Part ARC depth evidence exists."""

    if not candidates:
        raise AssemblyResolutionError("web candidate set is empty")
    minimum_span = metadata.nominal_length.value * 0.80
    eligible = tuple(
        candidate
        for candidate in candidates
        if candidate.longitudinal_span >= minimum_span
    )
    if not eligible:
        raise AssemblyResolutionError("no long web candidate for Part ARC opening")
    pierced_candidates = tuple(
        candidate
        for candidate in eligible
        if all(
            circular_opening_is_contained(candidate.projection, opening)
            for opening in openings
        )
    )
    if not pierced_candidates:
        raise AssemblyResolutionError(
            "no web candidate contains the reconstructed Part ARC opening"
        )
    # Hidden arcs belong to the face with the strongest hidden source support;
    # visible arcs use the complementary visible face.  This is the same
    # side evidence used by the role-aware opening assignment below.
    pierced = max(
        pierced_candidates,
        key=lambda candidate: (
            sum(
                _opening_side_score(candidate, opening, assignment.h_view)[0]
                for opening in openings
            ),
            candidate.longitudinal_span,
            -candidate.area,
            candidate.candidate_id,
        ),
    )
    opposites = tuple(
        candidate
        for candidate in eligible
        if candidate.candidate_id != pierced.candidate_id
    )
    if not opposites:
        # A single outline can still represent two physical plates; the
        # feature matrix will keep the roles distinct without inventing a
        # second contour.
        return (pierced, pierced)
    opposite = max(
        opposites,
        key=lambda candidate: (
            -sum(
                _opening_side_score(candidate, opening, assignment.h_view)[0]
                for opening in openings
            ),
            candidate.longitudinal_span,
            _strength(candidate.derivations),
            -candidate.area,
            candidate.candidate_id,
        ),
    )
    return (pierced, opposite)


def _select_web_pair(
    candidates: tuple[WebOutlineCandidate, ...],
    openings: tuple[ProjectedCircularOpening, ...],
    assignment: ViewAssignmentCandidate,
    metadata: BoxMetadata,
) -> tuple[WebOutlineCandidate, WebOutlineCandidate]:
    if not candidates:
        raise AssemblyResolutionError("web candidate set is empty")
    if openings:
        minimum_span = metadata.nominal_length.value * 0.80
        counts = {
            candidate.candidate_id: len(
                lower_circular_openings(candidate.projection, openings)
            )
            for candidate in candidates
        }
        full = tuple(
            candidate
            for candidate in candidates
            if candidate.longitudinal_span >= minimum_span
            and counts[candidate.candidate_id] == len(openings)
        )
        if not full:
            raise AssemblyResolutionError("no web candidate contains the Bolt pattern")
        pierced = min(
            full,
            key=lambda candidate: (
                candidate.area,
                len(candidate.source_ids),
                candidate.candidate_id,
            ),
        )
        hidden = tuple(
            candidate
            for candidate in candidates
            if candidate.longitudinal_span >= minimum_span
            and counts[candidate.candidate_id] == 0
            and _hidden_fraction(candidate, assignment.h_view) >= 0.70
        )
        if not hidden:
            if len(candidates) == 1:
                return (pierced, pierced)
            raise AssemblyResolutionError(
                "no hidden-course web candidate complements the pierced web"
            )
        opposite = max(
            hidden,
            key=lambda candidate: (
                candidate.longitudinal_span,
                _hidden_fraction(candidate, assignment.h_view),
                -len(candidate.source_ids),
                candidate.candidate_id,
            ),
        )
        return (opposite, pierced)

    maximum_area = max(candidate.area for candidate in candidates)
    plausible = tuple(
        candidate for candidate in candidates if candidate.area >= maximum_area * 0.18
    )
    clusters = _cluster_by_area(
        plausible,
        relative_tolerance=0.02,
        representative_key=lambda candidate: (
            candidate.area,
            candidate.longitudinal_span,
            candidate.candidate_id,
        ),
        strength_getter=lambda candidate: _strength(candidate.derivations),
    )
    ranked = sorted(
        clusters,
        key=lambda cluster: (
            -cluster.strength,
            -cluster.representative.area,
            cluster.representative.candidate_id,
        ),
    )
    first = ranked[0].representative
    second = ranked[1].representative if len(ranked) > 1 else first
    return (first, second)


@dataclass(frozen=True, slots=True)
class _OuterFlangeCourse:
    side: str
    length: float
    longitudinal_center: float
    transverse: float
    source_ids: tuple[str, ...]


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


def _select_cranked_flange_pair(
    candidates: tuple[FlangeOutlineCandidate, ...],
    supporting_web: WebOutlineCandidate,
) -> tuple[FlangeOutlineCandidate, FlangeOutlineCandidate]:
    support = tuple(sorted(supporting_web.source_ids))
    supported = tuple(
        candidate
        for candidate in candidates
        if FlangeDerivation.NEUTRAL_AXIS_FROM_PAIRED_WEB_COURSES
        in candidate.derivations
        and support in candidate.support_source_sets
        and abs(candidate.longitudinal_span - round(candidate.longitudinal_span))
        <= 1e-9
    )
    distinct = {candidate.longitudinal_span: candidate for candidate in supported}
    if len(distinct) != 2:
        raise AssemblyResolutionError(
            "cranked web does not prove exactly two rounded neutral flange courses"
        )
    ordered = tuple(distinct[length] for length in sorted(distinct))
    return (ordered[0], ordered[1])


def _is_explicit_outward_development(
    candidate: FlangeOutlineCandidate,
    offset_mm: float,
) -> bool:
    """Whether B-view evidence proves material beyond the H-view course.

    A positive offset alone is not evidence.  It must be backed either by a
    parallel-course transfer or by one unambiguous extended inner end course.
    Near-zero offsets are treated as coordinate noise, not development.
    """

    if offset_mm <= 0.05:
        return False
    if any(
        derivation in candidate.derivations
        for derivation in (
            FlangeDerivation.SOURCE_FACE_UNION,
            FlangeDerivation.ENDPOINT_CAP_PATH_CYCLE,
            FlangeDerivation.CONNECTED_COURSE_CYCLE,
        )
    ):
        return True
    if "BOX.FLANGE.PARALLEL_COURSE_OFFSET" in candidate.rule_ids:
        return True
    prefix = "BOX.FLANGE.PAIRED_CAPS.EXTENDED_INNER_COUNT_"
    extension_counts = {
        int(rule_id.removeprefix(prefix))
        for rule_id in candidate.rule_ids
        if rule_id.startswith(prefix) and rule_id.removeprefix(prefix).isdigit()
    }
    return len(extension_counts) == 1 and next(iter(extension_counts), 0) > 0


def _select_straight_flange_pair(
    candidates: tuple[FlangeOutlineCandidate, ...],
    courses: tuple[_OuterFlangeCourse, _OuterFlangeCourse],
    metadata: BoxMetadata,
) -> tuple[FlangeOutlineCandidate, FlangeOutlineCandidate]:
    if not candidates:
        raise AssemblyResolutionError("flange candidate set is empty")
    tolerance = (
        2.5
        * max(
            metadata.profile.value.web_thickness,
            metadata.profile.value.flange_thickness,
        )
        + 1.0
    )
    eligible = tuple(
        candidate
        for candidate in candidates
        if min(abs(candidate.longitudinal_span - course.length) for course in courses)
        <= tolerance
    )
    if not eligible:
        raise AssemblyResolutionError(
            "no flange candidate matches H-view outer courses"
        )

    # Opposite skewed ends can transfer unequal amounts into the two flange
    # courses.  The admissible disagreement is tied to the smaller wall
    # thickness rather than to a drawing- or member-specific constant.
    asymmetric_transfer_tolerance = 1.2 * min(
        metadata.profile.value.web_thickness,
        metadata.profile.value.flange_thickness,
    )

    best: (
        tuple[tuple[float, ...], tuple[FlangeOutlineCandidate, FlangeOutlineCandidate]]
        | None
    ) = None
    for first, second in combinations_with_replacement(eligible, 2):
        for ordered in ((first, second), (second, first)):
            offsets = tuple(
                candidate.longitudinal_span - course.length
                for candidate, course in zip(ordered, courses, strict=True)
            )
            if max(abs(value) for value in offsets) > tolerance:
                continue
            # Projection lowering may preserve a course or develop it outwards;
            # shortening an observed outer face course has no manufacturing
            # interpretation and therefore fails this hypothesis.
            if any(value < -0.02 for value in offsets):
                continue
            strengths = tuple(_strength(candidate.derivations) for candidate in ordered)
            offset_mismatch = abs(offsets[0] - offsets[1])
            developed_count = sum(
                _is_explicit_outward_development(candidate, offset)
                for candidate, offset in zip(ordered, offsets, strict=True)
            )
            unsupported_outward_offset = sum(
                max(0.0, offset - 0.05)
                for candidate, offset in zip(ordered, offsets, strict=True)
                if not _is_explicit_outward_development(candidate, offset)
            )
            exact_course_count = sum(abs(offset) <= 0.02 for offset in offsets)
            exact_source_maximal_count = sum(
                _is_exact_h_course_maximal_flange_candidate(candidate, course)
                for candidate, course in zip(ordered, courses, strict=True)
            )
            derivation_breadth = sum(
                len(candidate.derivations) for candidate in ordered
            )
            course_order = (
                courses[1].longitudinal_center - courses[0].longitudinal_center
            )
            candidate_order = float(ordered[1].projection.polygon.centroid.x) - float(
                ordered[0].projection.polygon.centroid.x
            )
            longitudinal_order_residual = abs(candidate_order - course_order)
            if longitudinal_order_residual > tolerance:
                continue
            if abs(course_order) <= 0.05:
                longitudinal_order_score = float(abs(candidate_order) <= 0.05)
            elif abs(candidate_order) <= 0.05:
                longitudinal_order_score = 0.5
            else:
                longitudinal_order_score = float(course_order * candidate_order > 0.0)
            rank = (
                float(exact_source_maximal_count == 2),
                float(exact_source_maximal_count),
                # A B-view pair may be ambiguous in longitudinal position when
                # both physical roles share one geometry, but it may never
                # reverse (or spuriously split) the top/bottom relation proved
                # by the H-view outer courses.
                float(longitudinal_order_score > 0.0),
                float(developed_count),
                min(strengths),
                sum(strengths),
                longitudinal_order_score,
                float(offset_mismatch <= asymmetric_transfer_tolerance),
                -unsupported_outward_offset,
                float(exact_course_count),
                first.area + second.area,
                -longitudinal_order_residual,
                float(derivation_breadth),
                -offset_mismatch,
                -abs(first.longitudinal_span - second.longitudinal_span),
            )
            pair = (ordered[0], ordered[1])
            if (
                best is None
                or rank > best[0]
                or (
                    rank == best[0]
                    and tuple(item.candidate_id for item in pair)
                    < tuple(item.candidate_id for item in best[1])
                )
            ):
                best = (rank, pair)
    if best is None:
        raise AssemblyResolutionError(
            "flange course pair could not be jointly assigned"
        )
    # The course order is bottom, top; return top, bottom for physical roles.
    bottom, top = best[1]
    return (top, bottom)


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


def _make_plate(
    role: PhysicalPlateRole,
    candidate: WebOutlineCandidate | FlangeOutlineCandidate,
    cuts: tuple[object, ...],
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
        inner_contours=(),
        role_evidence=evidence,
    )


def _proof_report(
    metadata: BoxMetadata,
    assignment: ViewAssignmentCandidate,
    plates: tuple[PhysicalPlateIR, ...],
    web_search: WebCandidateSearchResult,
    flange_search: FlangeCandidateSearchResult,
    web_candidates: tuple[WebOutlineCandidate, WebOutlineCandidate],
    flange_candidates: tuple[FlangeOutlineCandidate, FlangeOutlineCandidate],
    source_openings: tuple[ProjectedCircularOpening, ...],
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
    search_evidence: list[ProofEvidence] = []
    for channel, search, selected in (
        ("web", web_search, web_candidates),
        ("flange", flange_search, flange_candidates),
    ):
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
                        for candidate in web_candidates
                    )
                    and any(
                        WebDerivation.CONNECTED_COURSE_CYCLE
                        in candidate.derivations
                        for candidate in web_candidates
                    )
                )
            )
        )
        selected_inner_band = channel == "web" and all(
            candidate.projection.source_conserved
            and WebDerivation.INNER_COURSE_BAND in candidate.derivations
            for candidate in web_candidates
        )
        selected_complete_connected_courses = (
            channel == "web"
            and web_search.connected_course_search_complete
            and all(
                candidate.projection.source_conserved
                and WebDerivation.CONNECTED_COURSE_CYCLE in candidate.derivations
                for candidate in web_candidates
            )
        )
        selected_paired_caps = channel == "flange" and all(
            candidate.projection.source_conserved
            and FlangeDerivation.PAIRED_COURSE_CAP_DEVELOPMENT in candidate.derivations
            and PAIRED_CAP_THICKNESS_BOUNDED_SOURCE_BOUNDARY_RULE_ID
            in candidate.rule_ids
            for candidate in flange_candidates
        )
        selected_neutral_axis = channel == "flange" and all(
            candidate.projection.source_conserved
            and FlangeDerivation.NEUTRAL_AXIS_FROM_PAIRED_WEB_COURSES
            in candidate.derivations
            for candidate in flange_candidates
        )
        if search.direct_face_search_complete:
            measured = "complete"
        elif selected_part_arc_role:
            measured = "part_arc_role_evidence_dominates"
            part_arc_role_compensated_channels.append(channel)
        elif selected_inner_band or selected_paired_caps or selected_neutral_axis:
            measured = "independent_source_topology_dominates"
            independent_topology_compensated_channels.append(channel)
        elif channel == "web" and (
            _direct_source_boundary_with_complete_course_domain_dominates(
                web_search,
                web_candidates,
            )
        ):
            measured = "direct_source_boundary_with_complete_course_domain"
            direct_source_boundary_compensated_channels.append(channel)
        elif selected_complete_connected_courses:
            measured = "complete_connected_course_topology_dominates"
            complete_connected_course_compensated_channels.append(channel)
        elif channel == "flange" and _exact_h_course_maximal_flange_pair_dominates(
            assignment,
            metadata,
            flange_candidates,
        ):
            measured = "exact_h_course_maximal_flange_dominates"
            exact_h_course_compensated_channels.append(channel)
        else:
            measured = "incomplete"
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
    accounted_openings = tuple(
        opening
        for opening in source_openings
        if set(opening.source_ids).issubset(assigned_opening_source_ids)
    )
    opening_inventory_status = (
        ProofStatus.PASS
        if len(accounted_openings) == len(source_openings)
        else ProofStatus.CONFLICT
    )
    obligations = (
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
            status=ProofStatus.PASS,
            critical=True,
            evidence=(
                ProofEvidence(
                    evidence_id="view:H+B",
                    channel="projection",
                    source_ids=(assignment.h_view.group_id, assignment.b_view.group_id),
                    measured=assignment.score,
                    expected=0.0,
                    tolerance=0.01,
                ),
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
            obligation_id="BOX.PROOF.OPENINGS.CONTAINED",
            status=opening_inventory_status,
            critical=True,
            evidence=(
                ProofEvidence(
                    evidence_id="assembly:openings",
                    channel="topology",
                    source_ids=tuple(
                        sorted(
                            {
                                source_id
                                for opening in source_openings
                                for source_id in opening.source_ids
                            }
                        )
                    ),
                    measured=len(accounted_openings),
                    expected=len(source_openings),
                    tolerance=0.0,
                ),
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


def _compile_assignment(
    source: SourceDocumentIR,
    metadata: BoxMetadata,
    assignment: ViewAssignmentCandidate,
) -> CompleteBoxHypothesis:
    web_search = enumerate_web_outline_candidates(assignment, metadata)
    flange_search = enumerate_flange_outline_candidates(assignment, metadata)
    web_bolt_openings = project_circular_openings(source, assignment.h_view)
    flange_bolt_openings = project_circular_openings(source, assignment.b_view)
    web_part_openings = project_part_arc_openings(source, assignment.h_view)
    flange_part_openings = project_part_arc_openings(source, assignment.b_view)
    source_openings = (
        *web_bolt_openings,
        *flange_bolt_openings,
        *web_part_openings,
        *flange_part_openings,
    )
    asymmetric_part_evidence = bool(web_part_openings or flange_part_openings)
    if web_part_openings:
        web_pair = _select_part_arc_web_pair(
            web_search.candidates,
            web_part_openings,
            assignment,
            metadata,
        )
    else:
        web_pair = _select_web_pair(
            web_search.candidates,
            web_bolt_openings,
            assignment,
            metadata,
        )
    if assignment.h_view.frame.transverse_span > metadata.profile.value.height * 1.5:
        flange_pair = _select_cranked_flange_pair(
            flange_search.candidates,
            web_pair[1],
        )
    else:
        flange_pair = _select_straight_flange_pair(
            flange_search.candidates,
            _outer_flange_courses(assignment, metadata),
            metadata,
        )

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
    plates = tuple(
        _make_plate(
            role,
            candidate,
            lower_circular_openings(candidate.projection, openings),
            metadata,
        )
        for role, candidate, openings in zip(
            web_roles,
            web_pair,
            web_buckets,
            strict=True,
        )
    ) + tuple(
        _make_plate(
            role,
            candidate,
            lower_circular_openings(candidate.projection, openings),
            metadata,
        )
        for role, candidate, openings in zip(
            flange_roles,
            flange_pair,
            flange_buckets,
            strict=True,
        )
    )
    proof = _proof_report(
        metadata,
        assignment,
        plates,
        web_search,
        flange_search,
        web_pair,
        flange_pair,
        source_openings,
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
    rank_key = (
        -assignment.score,
        assignment.drawing_graph_score,
        float(direct_features),
        -float(inferred_features),
        float(bolt_circles),
        float(part_arc_openings),
        float(bool(web_part_openings)),
        sum(
            _strength(candidate.derivations) for candidate in (*web_pair, *flange_pair)
        ),
    )
    return CompleteBoxHypothesis(
        assignment=assignment,
        web_candidates=web_pair,
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
    best_score = assignments[0].score
    compatible = tuple(
        assignment
        for assignment in assignments
        if assignment.score <= best_score + 1e-8
    )
    hypotheses: list[CompleteBoxHypothesis] = []
    diagnostics = list(preprocessed.diagnostics)
    for assignment in compatible:
        try:
            hypothesis = _compile_assignment(
                preprocessed.source,
                resolved_metadata,
                assignment,
            )
            hypotheses.append(hypothesis)
            if not hypothesis.proof_report.search_complete:
                diagnostics.append(
                    "BOX.ASSEMBLY.SEARCH_INCOMPLETE:"
                    f"{assignment.signature}:"
                    f"{','.join(hypothesis.proof_report.blocking_obligation_ids)}"
                )
        except AssemblyResolutionError as error:
            diagnostics.append(
                f"BOX.ASSEMBLY.CANDIDATE_REJECTED:{assignment.signature}:{error}"
            )
    hypotheses.sort(
        key=lambda hypothesis: (
            any(
                obligation.obligation_id == "BOX.PROOF.VIEW.PART_MARK_H_ROLE"
                and obligation.status is ProofStatus.CONFLICT
                for obligation in hypothesis.proof_report.obligations
            ),
            not hypothesis.proof_report.search_complete,
            tuple(-value for value in hypothesis.rank_key),
            hypothesis.assignment.signature,
            hypothesis.mir.fingerprint,
        )
    )
    if not hypotheses:
        raise AssemblyResolutionError("; ".join(diagnostics) or "assembly failed")
    return AssemblySearchResult(
        hypotheses=tuple(hypotheses),
        search_complete=hypotheses[0].proof_report.search_complete,
        diagnostics=tuple(diagnostics),
    )
