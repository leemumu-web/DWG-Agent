from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations, combinations_with_replacement, product
from math import hypot

from shapely import normalize, set_precision
from shapely.affinity import affine_transform, translate

from .equivalence import BOX_DRAFTING_RESOLUTION_MM
from .flange_solver import (
    FlangeDerivation,
    FlangeOutlineCandidate,
    preserves_exact_source_course_authority,
)
from .metadata import BoxMetadata
from .manufacturing_ir import contour_polygon
from .openings import (
    CircularOpeningKind,
    OpeningVisibility,
    ProjectedCircularOpening,
    circular_opening_is_contained,
)
from .source_ir import is_hidden_projection_linetype
from .view_frame import PartViewIR
from .web_solver import WebOutlineCandidate, web_derivation_authority


class RoleHypothesisError(ValueError):
    """The source observations do not yield a closed physical-role domain."""


@dataclass(frozen=True, slots=True)
class FlangeCourseEvidence:
    side: str
    length_mm: float
    longitudinal_center_mm: float
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RolePairSearchResult[CandidateT]:
    pairs: tuple[tuple[CandidateT, CandidateT], ...]
    generated_pair_ids: tuple[str, ...]
    enumerator_exhausted: bool
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _TranslatedCoursePairProof:
    physical_candidate_ids: tuple[str, str]
    overlay_candidate_ids: tuple[str, ...]


def _pair_id(first: object, second: object) -> str:
    return f"{getattr(first, 'candidate_id')}::{getattr(second, 'candidate_id')}"


def _deduplicate_pairs[CandidateT](
    pairs: list[tuple[CandidateT, CandidateT]],
) -> tuple[tuple[CandidateT, CandidateT], ...]:
    by_id = {
        _pair_id(first, second): (first, second)
        for first, second in pairs
    }
    return tuple(by_id[pair_id] for pair_id in sorted(by_id))


def _candidate_hidden_fraction(
    candidate: WebOutlineCandidate,
    view: PartViewIR,
) -> float:
    by_id = {entity.source_id: entity for entity in view.entities}
    relevant = tuple(
        by_id[source_id]
        for source_id in candidate.source_ids
        if source_id in by_id
    )
    if not relevant:
        return 0.0
    hidden = sum(
        is_hidden_projection_linetype(entity.linetype)
        for entity in relevant
    )
    return hidden / len(relevant)


def _opening_visibility_score(
    candidate: WebOutlineCandidate,
    opening: ProjectedCircularOpening,
    view: PartViewIR,
) -> float:
    hidden_fraction = _candidate_hidden_fraction(candidate, view)
    if opening.visibility is OpeningVisibility.HIDDEN:
        return hidden_fraction
    if opening.visibility is OpeningVisibility.VISIBLE:
        return -hidden_fraction
    return -abs(hidden_fraction - 0.5)


def _transverse_order(
    first: WebOutlineCandidate,
    second: WebOutlineCandidate,
) -> tuple[WebOutlineCandidate, WebOutlineCandidate]:
    if first.candidate_id == second.candidate_id:
        return (first, second)
    first_center = float(first.projection.polygon.centroid.y)
    second_center = float(second.projection.polygon.centroid.y)
    if abs(first_center - second_center) <= max(
        first.projection.grid_size_mm,
        second.projection.grid_size_mm,
        1e-6,
    ):
        return (
            (first, second)
            if first.candidate_id < second.candidate_id
            else (second, first)
        )
    return (first, second) if first_center < second_center else (second, first)


def _web_source_authority_order(
    first: WebOutlineCandidate,
    second: WebOutlineCandidate,
    view: PartViewIR,
) -> tuple[WebOutlineCandidate, WebOutlineCandidate]:
    """Bind upper/lower role order without using drawing position or IDs.

    Visible/hidden source semantics establish the represented near-side course.
    Source-topology authority and material area resolve candidates only when
    visibility ties.  Transverse position is the final physical tie breaker.
    """

    if first.candidate_id == second.candidate_id:
        return (first, second)
    first_hidden = _candidate_hidden_fraction(first, view)
    second_hidden = _candidate_hidden_fraction(second, view)
    if abs(first_hidden - second_hidden) > 1e-12:
        return (first, second) if first_hidden < second_hidden else (second, first)
    first_key = (web_derivation_authority(first.derivations), first.area)
    second_key = (web_derivation_authority(second.derivations), second.area)
    if first_key == second_key:
        return _transverse_order(first, second)
    return (first, second) if first_key > second_key else (second, first)


def _overlay_remainder_candidate_ids(
    candidates: tuple[WebOutlineCandidate, ...],
) -> tuple[str, ...]:
    """Identify exact face partitions created by two overlaid web outlines.

    If one source-conserved face is exactly partitioned by a second complete
    face plus a remainder, the remainder is drawing topology rather than a
    third plate.  The source set is the decisive guard: polygon containment
    alone is insufficient because two real unequal webs can be nested in one
    projection.
    """

    rejected: set[str] = set()
    for container in candidates:
        container_polygon = container.projection.polygon
        tolerance = max(container.projection.grid_size_mm * 2.0, 0.01)
        area_tolerance = max(
            tolerance * max(container_polygon.length, 1.0),
            tolerance**2,
        )
        for contained in candidates:
            if contained.candidate_id == container.candidate_id:
                continue
            contained_polygon = contained.projection.polygon
            if not container_polygon.buffer(tolerance).covers(contained_polygon):
                continue
            for remainder in candidates:
                if remainder.candidate_id in {
                    container.candidate_id,
                    contained.candidate_id,
                }:
                    continue
                remainder_polygon = remainder.projection.polygon
                if not container_polygon.buffer(tolerance).covers(remainder_polygon):
                    continue
                if (
                    contained_polygon.intersection(remainder_polygon).area
                    > area_tolerance
                ):
                    continue
                reconstructed = contained_polygon.union(remainder_polygon)
                if (
                    container_polygon.symmetric_difference(reconstructed).area
                    > area_tolerance
                ):
                    continue
                expected_sources = set(container.source_ids) | set(
                    contained.source_ids
                )
                if set(remainder.source_ids) != expected_sources:
                    continue
                rejected.add(remainder.candidate_id)
    return tuple(sorted(rejected))


def _polygons_equal_within_drafting_resolution(
    first,
    second,
    *,
    tolerance: float,
) -> bool:
    area_tolerance = max(
        tolerance * max(first.length, second.length, 1.0),
        tolerance**2,
    )
    return (
        first.hausdorff_distance(second) <= tolerance
        and first.symmetric_difference(second).area <= area_tolerance
    )


def _translated_course_pair_proof(
    candidates: tuple[WebOutlineCandidate, ...],
    metadata: BoxMetadata,
) -> _TranslatedCoursePairProof | None:
    """Prove two equal physical webs behind one Boolean overlay lattice.

    In an equal-section BOX main view, two real webs can share their long
    courses while their terminal pairs are longitudinally translated.  DXF
    polygonization then exposes not only the two plates, but also their union,
    intersection and end remainders.  Geometry alone cannot choose among those
    faces.  This proof therefore requires the complete source-backed Boolean
    lattice before any derived spelling is removed.
    """

    proofs: list[_TranslatedCoursePairProof] = []
    for first, second in combinations(candidates, 2):
        tolerance = max(
            BOX_DRAFTING_RESOLUTION_MM,
            first.projection.grid_size_mm * 2.0,
            second.projection.grid_size_mm * 2.0,
        )
        first_polygon = first.projection.polygon
        second_polygon = second.projection.polygon
        first_min_x, first_min_y, _, _ = first_polygon.bounds
        second_min_x, second_min_y, _, _ = second_polygon.bounds
        normalized_first = translate(
            first_polygon,
            xoff=-first_min_x,
            yoff=-first_min_y,
        )
        normalized_second = translate(
            second_polygon,
            xoff=-second_min_x,
            yoff=-second_min_y,
        )
        if not _polygons_equal_within_drafting_resolution(
            normalized_first,
            normalized_second,
            tolerance=tolerance,
        ):
            continue
        longitudinal_shift = abs(
            float(first_polygon.centroid.x) - float(second_polygon.centroid.x)
        )
        transverse_shift = abs(
            float(first_polygon.centroid.y) - float(second_polygon.centroid.y)
        )
        if longitudinal_shift <= tolerance or transverse_shift > tolerance:
            continue
        intersection_polygon = first_polygon.intersection(second_polygon)
        if intersection_polygon.is_empty:
            continue
        area_tolerance = max(
            tolerance * max(first_polygon.length, second_polygon.length, 1.0),
            tolerance**2,
        )
        if (
            intersection_polygon.area <= area_tolerance
            or first_polygon.difference(second_polygon).area <= area_tolerance
            or second_polygon.difference(first_polygon).area <= area_tolerance
        ):
            continue

        first_sources = set(first.source_ids)
        second_sources = set(second.source_ids)
        shared_sources = first_sources & second_sources
        first_unique = first_sources - second_sources
        second_unique = second_sources - first_sources
        if (
            len(shared_sources) < 2
            or len(first_unique) < 2
            or len(second_unique) < 2
        ):
            continue

        union_polygon = first_polygon.union(second_polygon)
        pair_sources = first_sources | second_sources

        def proves_boolean_boundary(
            candidate: WebOutlineCandidate,
            expected_polygon,
        ) -> bool:
            candidate_sources = set(candidate.source_ids)
            return (
                candidate.candidate_id
                not in {first.candidate_id, second.candidate_id}
                and candidate_sources <= pair_sources
                and shared_sources <= candidate_sources
                and bool(candidate_sources & first_unique)
                and bool(candidate_sources & second_unique)
                and _polygons_equal_within_drafting_resolution(
                    candidate.projection.polygon,
                    expected_polygon,
                    tolerance=tolerance,
                )
            )

        union_candidates = tuple(
            candidate
            for candidate in candidates
            if proves_boolean_boundary(candidate, union_polygon)
        )
        intersection_candidates = tuple(
            candidate
            for candidate in candidates
            if proves_boolean_boundary(candidate, intersection_polygon)
        )
        if not union_candidates or not intersection_candidates:
            continue

        derived_polygons = (
            union_polygon,
            intersection_polygon,
            first_polygon.difference(second_polygon),
            second_polygon.difference(first_polygon),
        )
        overlay_ids: set[str] = set()
        maximum_boundary_drift = (
            max(
                metadata.profile.value.web_thickness,
                metadata.profile.value.flange_thickness,
            )
            + 1.0
        )
        for candidate in candidates:
            if candidate.candidate_id in {
                first.candidate_id,
                second.candidate_id,
            }:
                continue
            candidate_polygon = candidate.projection.polygon
            if any(
                _polygons_equal_within_drafting_resolution(
                    candidate_polygon,
                    derived_polygon,
                    tolerance=tolerance,
                )
                for derived_polygon in derived_polygons
                if not derived_polygon.is_empty
            ):
                overlay_ids.add(candidate.candidate_id)
                continue
            candidate_sources = set(candidate.source_ids)
            source_overlap = candidate_sources & pair_sources
            if (
                candidate_polygon.buffer(tolerance).covers(union_polygon)
                and candidate_polygon.hausdorff_distance(union_polygon)
                <= maximum_boundary_drift
                and candidate_polygon.symmetric_difference(union_polygon).area
                / max(candidate_polygon.area, union_polygon.area, 1.0)
                <= 0.02
                and len(source_overlap) >= 2
                and len(source_overlap) / max(len(candidate_sources), 1) >= 0.5
            ):
                overlay_ids.add(candidate.candidate_id)

        proofs.append(
            _TranslatedCoursePairProof(
                physical_candidate_ids=tuple(
                    sorted((first.candidate_id, second.candidate_id))
                ),
                overlay_candidate_ids=tuple(sorted(overlay_ids)),
            )
        )

    physical_pairs = {
        proof.physical_candidate_ids
        for proof in proofs
    }
    if len(physical_pairs) != 1:
        return None
    physical_ids = next(iter(physical_pairs))
    return _TranslatedCoursePairProof(
        physical_candidate_ids=physical_ids,
        overlay_candidate_ids=tuple(
            sorted(
                {
                    candidate_id
                    for proof in proofs
                    if proof.physical_candidate_ids == physical_ids
                    for candidate_id in proof.overlay_candidate_ids
                }
            )
        ),
    )


def _bounded_partition_candidate_ids(
    candidates: tuple[WebOutlineCandidate, ...],
    metadata: BoxMetadata,
) -> tuple[str, ...]:
    """Reject smaller spellings of one wall-thickness-bounded source course.

    Tekla overlays visible, hidden and developed courses.  They can polygonize
    into several almost coincident faces.  A candidate is a representation
    partition only when a source-related candidate contains it, their boundary
    drift is bounded by wall thickness, and the material difference stays
    below two percent.  A genuinely different web course therefore survives.

    The rejection is a least fixed point.  A candidate may only be rejected by
    a container that is itself retained: an intermediate overlay face (the
    union of two real webs) must not reject one of the real webs before it is
    itself removed, otherwise a shallow skew end collapses two physical plates
    into one.  A *nearly coincident* container (a distinct polygonization of
    the same physical face) still participates even when rejected, so two
    spellings of one face never both survive the fixed point.
    """

    maximum_boundary_drift = (
        max(
            metadata.profile.value.web_thickness,
            metadata.profile.value.flange_thickness,
        )
        + 1.0
    )
    rejected: set[str] = set()
    # The rejection operator is anti-monotone in ``rejected`` (a container that
    # is itself removed stops rejecting others), so a bare fixed-point loop can
    # in principle oscillate forever between two sets.  Rejection strictly
    # follows decreasing area, so the fixed point is reached within
    # ``len(candidates)`` additions; the +2 bound leaves headroom and
    # guarantees termination on any future corpus.
    for _iteration in range(len(candidates) + 2):
        newly_rejected: set[str] = set()
        for candidate in candidates:
            candidate_polygon = contour_polygon(candidate.contour)
            candidate_sources = set(candidate.source_ids)
            for container in candidates:
                if container.candidate_id == candidate.candidate_id:
                    continue
                if container.candidate_id in rejected:
                    rejected_container_polygon = contour_polygon(container.contour)
                    if (
                        rejected_container_polygon.symmetric_difference(
                            candidate_polygon
                        ).area
                        / max(
                            rejected_container_polygon.area,
                            candidate_polygon.area,
                            1.0,
                        )
                        > 0.005
                    ):
                        # The rejected container is genuinely different material;
                        # it must not decide this candidate's fate.
                        continue
                    # Nearly coincident: still a spelling of the same face.
                container_polygon = contour_polygon(container.contour)
                area_tolerance = max(
                    1e-6,
                    container.projection.grid_size_mm**2,
                    candidate.projection.grid_size_mm**2,
                )
                if container_polygon.area <= candidate_polygon.area + area_tolerance:
                    continue
                if (
                    container_polygon.hausdorff_distance(candidate_polygon)
                    > maximum_boundary_drift
                ):
                    continue
                if (
                    container_polygon.symmetric_difference(candidate_polygon).area
                    / max(container_polygon.area, candidate_polygon.area, 1.0)
                    > 0.02
                ):
                    continue
                container_sources = set(container.source_ids)
                source_union = container_sources | candidate_sources
                if (
                    not source_union
                    or len(container_sources & candidate_sources) / len(source_union)
                    < 0.25
                ):
                    continue
                newly_rejected.add(candidate.candidate_id)
                break
        if newly_rejected == rejected:
            break
        rejected = newly_rejected
    return tuple(sorted(rejected))


def enumerate_web_role_pairs(
    candidates: tuple[WebOutlineCandidate, ...],
    openings: tuple[ProjectedCircularOpening, ...],
    view: PartViewIR,
    metadata: BoxMetadata,
    *,
    part_arc_evidence: bool,
) -> RolePairSearchResult[WebOutlineCandidate]:
    """Enumerate every structurally feasible left/right web pair.

    Ranking strength, area and candidate ID never prune a manufacturing
    meaning.  They remain available to the caller only as deterministic preview
    telemetry after the neutral decision has established uniqueness.
    """

    if not candidates:
        raise RoleHypothesisError("web candidate set is empty")
    source_conserved = tuple(
        candidate
        for candidate in candidates
        if candidate.projection.source_conserved
        and candidate.source_ids
    )
    source_domain = source_conserved or tuple(
        candidate for candidate in candidates if candidate.source_ids
    )
    translated_pair_proof = _translated_course_pair_proof(
        source_domain,
        metadata,
    )
    protected_translated_ids = (
        set(translated_pair_proof.physical_candidate_ids)
        if translated_pair_proof is not None
        else set()
    )
    rejected_overlay_lattice = (
        set(translated_pair_proof.overlay_candidate_ids)
        if translated_pair_proof is not None
        else set()
    )
    rejected_remainders = set(
        _overlay_remainder_candidate_ids(source_domain)
    ) - protected_translated_ids
    without_remainders = tuple(
        candidate
        for candidate in source_domain
        if candidate.candidate_id not in rejected_remainders
        and candidate.candidate_id not in rejected_overlay_lattice
    )
    # A closed Bolt pattern is direct face evidence.  In that case the
    # bounded-partition heuristic cannot safely distinguish a physical web
    # course from an overlay spelling: the h-4 cranked views contain the
    # complete pierced and hidden courses among those bounded candidates.
    # Let the opening/visibility evidence establish the physical roles below.
    rejected_partitions = (
        set(_bounded_partition_candidate_ids(without_remainders, metadata))
        - protected_translated_ids
        if not openings
        else set()
    )
    eligible = tuple(
        candidate
        for candidate in without_remainders
        if candidate.candidate_id not in rejected_partitions
    )
    rejected_short_courses: set[str] = set()
    rejected_slivers: tuple[str, ...] = ()
    if not openings:
        minimum_span = max(
            BOX_DRAFTING_RESOLUTION_MM * 2.0,
            *(candidate.projection.grid_size_mm * 2.0 for candidate in eligible),
        )
        rejected_short_courses = {
            candidate.candidate_id
            for candidate in eligible
            if candidate.longitudinal_span <= minimum_span
        }
        eligible = tuple(
            candidate
            for candidate in eligible
            if candidate.candidate_id not in rejected_short_courses
        )
    if not eligible:
        raise RoleHypothesisError("no source-backed physical web candidate")

    pairs: list[tuple[WebOutlineCandidate, WebOutlineCandidate]] = []
    bolt_evidence_domain_reduced = False
    if openings:
        minimum_span = metadata.nominal_length.value * 0.80
        long_eligible = tuple(
            candidate
            for candidate in eligible
            if candidate.longitudinal_span >= minimum_span
        )
        if not long_eligible:
            raise RoleHypothesisError("no long web candidate for opening evidence")
        role_openings = (
            tuple(
                opening
                for opening in openings
                if opening.kind is CircularOpeningKind.PART_ARC_CIRCLE
            )
            if part_arc_evidence
            else openings
        )
        pierced = tuple(
            candidate
            for candidate in long_eligible
            if all(
                circular_opening_is_contained(candidate.projection, opening)
                for opening in role_openings
            )
        )
        if not pierced:
            raise RoleHypothesisError("no web candidate contains every opening")
        if part_arc_evidence:
            # Part ARC visibility is direct side/depth evidence.  Bolt circles
            # remain in the later opening-ownership proof, but cannot select
            # which physical web owns a Part ARC feature.
            strongest_visibility = max(
                sum(
                    _opening_visibility_score(candidate, opening, view)
                    for opening in role_openings
                )
                for candidate in pierced
            )
            pierced = tuple(
                candidate
                for candidate in pierced
                if sum(
                    _opening_visibility_score(candidate, opening, view)
                    for opening in role_openings
                )
                == strongest_visibility
            )
            for pierced_candidate in pierced:
                opposites = tuple(
                    candidate
                    for candidate in long_eligible
                    if candidate.candidate_id != pierced_candidate.candidate_id
                )
                if not opposites and len(long_eligible) == 1:
                    opposites = (pierced_candidate,)
                for opposite in opposites:
                    # Part ARC visibility is direct side evidence: the pierced
                    # material face owns the first physical web role, while
                    # the complementary face owns the second.  Drawing-space
                    # transverse position is only a presentation coordinate
                    # and may be mirrored by the source view.
                    pairs.append((pierced_candidate, opposite))
        else:
            # For Tekla Bolt views, the complete pierced course is the
            # smallest source-backed material face that contains the whole
            # pattern.  Larger faces add overlay/course fragments and do not
            # describe another physical plate.  Equal evidence stays in the
            # domain; candidate IDs are deliberately excluded from the proof.
            pierced_evidence = min(
                (candidate.area, len(candidate.source_ids))
                for candidate in pierced
            )
            pierced = tuple(
                candidate
                for candidate in pierced
                if (candidate.area, len(candidate.source_ids)) == pierced_evidence
            )
            hidden = tuple(
                candidate
                for candidate in long_eligible
                if not any(
                    circular_opening_is_contained(candidate.projection, opening)
                    for opening in openings
                )
                and _candidate_hidden_fraction(candidate, view) >= 0.70
            )
            if not hidden and len(long_eligible) == 1:
                hidden = long_eligible
            if not hidden:
                raise RoleHypothesisError(
                    "no hidden-course web candidate complements the pierced web"
                )
            # The complementary wall is the longest continuous hidden source
            # course.  Visibility strength and source economy only resolve
            # candidates with the same observed extent; exact ties survive.
            hidden_evidence = max(
                (
                    candidate.longitudinal_span,
                    _candidate_hidden_fraction(candidate, view),
                    -len(candidate.source_ids),
                )
                for candidate in hidden
            )
            hidden = tuple(
                candidate
                for candidate in hidden
                if (
                    candidate.longitudinal_span,
                    _candidate_hidden_fraction(candidate, view),
                    -len(candidate.source_ids),
                )
                == hidden_evidence
            )
            bolt_evidence_domain_reduced = True
            for pierced_candidate, hidden_candidate in product(pierced, hidden):
                pairs.append((hidden_candidate, pierced_candidate))
    else:
        # Drop sub-drafting face fragments that survive the partition filters.
        # A skewed end or a small overlay detail can polygonize into a short
        # strip whose area is a tiny fraction of the real web.  The 0.18 floor
        # mirrors the single-pair selector, and is far below any real web of a
        # prismatic BOX (both webs share length x box-height), so it only
        # removes strips that could never be a physical plate.
        maximum_area = max(candidate.area for candidate in eligible)
        plausible = tuple(
            candidate
            for candidate in eligible
            if candidate.area >= maximum_area * 0.18
        )
        rejected_slivers = tuple(
            sorted(
                candidate.candidate_id
                for candidate in eligible
                if candidate.area < maximum_area * 0.18
            )
        )
        if not plausible:
            plausible = eligible
        pair_source = (
            combinations_with_replacement(plausible, 2)
            if len(plausible) == 1
            else combinations(plausible, 2)
        )
        for first, second in pair_source:
            pairs.append(_web_source_authority_order(first, second, view))

    materialized = _deduplicate_pairs(pairs)
    if not materialized:
        raise RoleHypothesisError("web physical-role domain is empty")
    return RolePairSearchResult(
        pairs=materialized,
        generated_pair_ids=tuple(
            _pair_id(first, second) for first, second in materialized
        ),
        enumerator_exhausted=True,
        diagnostics=tuple(
            (
                *(
                    f"BOX.ROLE.WEB.OVERLAY_REMAINDER_REJECTED:{candidate_id}"
                    for candidate_id in sorted(rejected_remainders)
                ),
                *(
                    f"BOX.ROLE.WEB.BOUNDED_PARTITION_REJECTED:{candidate_id}"
                    for candidate_id in sorted(rejected_partitions)
                ),
                *(
                    f"BOX.ROLE.WEB.SUB_DRAFTING_SLIVER_REJECTED:{candidate_id}"
                    for candidate_id in sorted(rejected_short_courses)
                ),
                *(
                    f"BOX.ROLE.WEB.AREA_SLIVER_REJECTED:{candidate_id}"
                    for candidate_id in rejected_slivers
                ),
                *(
                    (
                        "BOX.ROLE.WEB.TRANSLATED_PAIR_PROVEN:"
                        + "::".join(
                            translated_pair_proof.physical_candidate_ids
                        ),
                    )
                    if translated_pair_proof is not None
                    else ()
                ),
                *(
                    f"BOX.ROLE.WEB.OVERLAY_LATTICE_REJECTED:{candidate_id}"
                    for candidate_id in sorted(rejected_overlay_lattice)
                ),
                *(
                    ("BOX.ROLE.WEB.SOURCE_CONSERVATION_UNPROVEN",)
                    if not source_conserved
                    else ()
                ),
                *(
                    ("BOX.ROLE.WEB.BOLT_EVIDENCE_DOMAIN_PROVEN",)
                    if bolt_evidence_domain_reduced
                    else ()
                ),
            )
        ),
    )


_FLANGE_AUTHORITY = {
    FlangeDerivation.NEUTRAL_AXIS_FROM_PAIRED_WEB_COURSES: 6,
    FlangeDerivation.PAIRED_COURSE_CAP_DEVELOPMENT: 5,
    FlangeDerivation.PARALLEL_COURSE_OFFSET_DEVELOPMENT: 5,
    FlangeDerivation.CONNECTED_COURSE_CYCLE: 4,
    FlangeDerivation.SOURCE_FACE_UNION: 3,
    FlangeDerivation.ENDPOINT_CAP_PATH_CYCLE: 2,
    FlangeDerivation.COURSE_STATION_RECTANGLE: 1,
}

_PLANE_TRANSFORMS = (
    (1.0, 0.0, 0.0, 1.0),
    (-1.0, 0.0, 0.0, 1.0),
    (1.0, 0.0, 0.0, -1.0),
    (-1.0, 0.0, 0.0, -1.0),
    (0.0, 1.0, 1.0, 0.0),
    (0.0, -1.0, 1.0, 0.0),
    (0.0, 1.0, -1.0, 0.0),
    (0.0, -1.0, -1.0, 0.0),
)


def _flange_geometry_meaning(candidate: FlangeOutlineCandidate) -> str:
    """Return a role-neutral outer-geometry key at drafting resolution."""

    polygon = contour_polygon(candidate.contour)
    variants: list[str] = []
    for a, b, d, e in _PLANE_TRANSFORMS:
        transformed = affine_transform(polygon, (a, b, d, e, 0.0, 0.0))
        min_x, min_y, _max_x, _max_y = transformed.bounds
        positioned = translate(transformed, xoff=-min_x, yoff=-min_y)
        variants.append(
            normalize(
                set_precision(
                    positioned,
                    grid_size=BOX_DRAFTING_RESOLUTION_MM,
                )
            ).wkb_hex
        )
    return min(variants)


def _explicit_outward_development(
    candidate: FlangeOutlineCandidate,
    offset_mm: float,
) -> bool:
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
        if rule_id.startswith(prefix)
        and rule_id.removeprefix(prefix).isdigit()
    }
    return len(extension_counts) == 1 and next(iter(extension_counts), 0) > 0


def _flange_authority_strength(candidate: FlangeOutlineCandidate) -> int:
    return max(_FLANGE_AUTHORITY[derivation] for derivation in candidate.derivations)


def _source_course_authoritative(
    candidate: FlangeOutlineCandidate,
    course: FlangeCourseEvidence,
) -> bool:
    return preserves_exact_source_course_authority(
        candidate,
        course.length_mm,
    )


def _straight_flange_pair_authority(
    pair: tuple[FlangeOutlineCandidate, FlangeOutlineCandidate],
    *,
    bottom_course: FlangeCourseEvidence,
    top_course: FlangeCourseEvidence,
    metadata: BoxMetadata,
    same_manufacturing_geometry: bool = False,
) -> tuple[float, ...]:
    """Return the audited evidence order for one top/bottom interpretation.

    Every field is a manufacturing invariant already present in the source
    model: exact maximal source coverage, non-reversed course order, explicit
    development, bounded transfer consistency, or complete material envelope.
    Candidate IDs and drawing filenames never participate.
    """

    top, bottom = pair
    ordered = (bottom, top)
    courses = (bottom_course, top_course)
    offsets = tuple(
        candidate.longitudinal_span - course.length_mm
        for candidate, course in zip(ordered, courses, strict=True)
    )
    strengths = tuple(_flange_authority_strength(candidate) for candidate in ordered)
    developed_count = sum(
        _explicit_outward_development(candidate, offset)
        for candidate, offset in zip(ordered, offsets, strict=True)
    )
    unsupported_outward_offset = sum(
        max(0.0, offset - 0.05)
        for candidate, offset in zip(ordered, offsets, strict=True)
        if not _explicit_outward_development(candidate, offset)
    )
    authoritative_source_course_count = sum(
        _source_course_authoritative(candidate, course)
        for candidate, course in zip(ordered, courses, strict=True)
    )
    course_order = (
        top_course.longitudinal_center_mm
        - bottom_course.longitudinal_center_mm
    )
    candidate_order = (
        float(top.projection.polygon.centroid.x)
        - float(bottom.projection.polygon.centroid.x)
    )
    order_residual = abs(candidate_order - course_order)
    if abs(course_order) <= 0.05:
        order_score = float(abs(candidate_order) <= 0.05)
    elif abs(candidate_order) <= 0.05:
        order_score = 0.5
    else:
        order_score = float(course_order * candidate_order > 0.0)
    offset_mismatch = abs(offsets[0] - offsets[1])
    transfer_tolerance = 1.2 * min(
        metadata.profile.value.web_thickness,
        metadata.profile.value.flange_thickness,
    )
    equivalent_source_reuse = (
        same_manufacturing_geometry
        and authoritative_source_course_count >= 1
        and all(candidate.projection.source_conserved for candidate in pair)
    )
    return (
        float(authoritative_source_course_count == 2),
        float(equivalent_source_reuse),
        float(authoritative_source_course_count),
        float(order_score > 0.0),
        float(developed_count),
        float(min(strengths)),
        float(sum(strengths)),
        order_score,
        float(offset_mismatch <= transfer_tolerance),
        -unsupported_outward_offset,
        float(sum(abs(offset) <= 0.02 for offset in offsets)),
        top.area + bottom.area,
        -order_residual,
        float(sum(len(candidate.derivations) for candidate in ordered)),
        -offset_mismatch,
        -abs(top.longitudinal_span - bottom.longitudinal_span),
    )


def enumerate_straight_flange_role_pairs(
    candidates: tuple[FlangeOutlineCandidate, ...],
    courses: tuple[FlangeCourseEvidence, FlangeCourseEvidence],
    metadata: BoxMetadata,
) -> RolePairSearchResult[FlangeOutlineCandidate]:
    """Enumerate every source-conserved top/bottom course assignment."""

    if not candidates:
        raise RoleHypothesisError("flange candidate set is empty")
    by_side = {course.side: course for course in courses}
    if set(by_side) != {"bottom", "top"}:
        raise RoleHypothesisError("straight flange domain needs bottom and top courses")
    bottom_course = by_side["bottom"]
    top_course = by_side["top"]
    if not bottom_course.source_ids or not top_course.source_ids:
        raise RoleHypothesisError("flange course source evidence is missing")
    tolerance = (
        2.5
        * max(
            metadata.profile.value.web_thickness,
            metadata.profile.value.flange_thickness,
        )
        + 1.0
    )

    def matches(
        candidate: FlangeOutlineCandidate,
        course: FlangeCourseEvidence,
    ) -> bool:
        offset = candidate.longitudinal_span - course.length_mm
        return (
            candidate.projection.source_conserved
            and bool(candidate.source_ids)
            and -0.02 <= offset <= tolerance
        )

    bottom_candidates = tuple(
        candidate for candidate in candidates if matches(candidate, bottom_course)
    )
    top_candidates = tuple(
        candidate for candidate in candidates if matches(candidate, top_course)
    )
    if not bottom_candidates or not top_candidates:
        raise RoleHypothesisError("no flange candidate matches both H-view courses")

    pairs: list[tuple[FlangeOutlineCandidate, FlangeOutlineCandidate]] = []
    course_order = (
        top_course.longitudinal_center_mm
        - bottom_course.longitudinal_center_mm
    )
    for bottom, top in product(bottom_candidates, top_candidates):
        candidate_order = (
            float(top.projection.polygon.centroid.x)
            - float(bottom.projection.polygon.centroid.x)
        )
        if abs(candidate_order - course_order) > tolerance:
            continue
        pairs.append((top, bottom))
    generated = _deduplicate_pairs(pairs)
    if not generated:
        raise RoleHypothesisError(
            "flange course pair has no source-position-consistent assignment"
        )
    meaning_candidates = {
        candidate.candidate_id: candidate
        for candidate in (*bottom_candidates, *top_candidates)
    }
    meaning_by_candidate_id = {
        candidate.candidate_id: _flange_geometry_meaning(candidate)
        for candidate in meaning_candidates.values()
    }
    by_meaning: dict[
        tuple[str, str],
        list[tuple[FlangeOutlineCandidate, FlangeOutlineCandidate]],
    ] = {}
    for pair in generated:
        by_meaning.setdefault(
            (
                meaning_by_candidate_id[pair[0].candidate_id],
                meaning_by_candidate_id[pair[1].candidate_id],
            ),
            [],
        ).append(pair)
    authority_by_meaning = {
        meaning: max(
            _straight_flange_pair_authority(
                pair,
                bottom_course=bottom_course,
                top_course=top_course,
                metadata=metadata,
                same_manufacturing_geometry=(meaning[0] == meaning[1]),
            )
            for pair in meaning_pairs
        )
        for meaning, meaning_pairs in by_meaning.items()
    }
    winning_authority = max(authority_by_meaning.values())
    winning_meanings = {
        meaning
        for meaning, authority in authority_by_meaning.items()
        if authority == winning_authority
    }
    materialized = tuple(
        pair
        for pair in generated
        if (
            meaning_by_candidate_id[pair[0].candidate_id],
            meaning_by_candidate_id[pair[1].candidate_id],
        )
        in winning_meanings
    )
    return RolePairSearchResult(
        pairs=materialized,
        generated_pair_ids=tuple(
            _pair_id(first, second) for first, second in materialized
        ),
        enumerator_exhausted=True,
        diagnostics=(
            "BOX.ROLE.FLANGE.AUTHORITY_DOMAIN:"
            f"generated={len(generated)};"
            f"meanings={len(by_meaning)};"
            f"winning_meanings={len(winning_meanings)};"
            f"retained={len(materialized)}",
        ),
    )


def enumerate_cranked_flange_role_pairs(
    candidates: tuple[FlangeOutlineCandidate, ...],
    web_pair: tuple[WebOutlineCandidate, WebOutlineCandidate],
    bolt_openings: tuple[ProjectedCircularOpening, ...],
) -> RolePairSearchResult[FlangeOutlineCandidate]:
    supporting_webs = web_pair
    if bolt_openings:
        # A complete Bolt pattern identifies the represented physical web
        # directly.  Neutral flange courses derived from the complementary
        # hidden overlay are a different longitudinal meaning and therefore
        # cannot remain in this evidence domain.
        supporting_webs = tuple(
            web
            for web in web_pair
            if all(
                circular_opening_is_contained(web.projection, opening)
                for opening in bolt_openings
            )
        )
        if not supporting_webs:
            raise RoleHypothesisError(
                "no cranked web support contains the complete Bolt pattern"
            )

    generated: list[tuple[FlangeOutlineCandidate, FlangeOutlineCandidate]] = []
    rejected_supports: list[str] = []
    for supporting_web in supporting_webs:
        support = tuple(sorted(supporting_web.source_ids))
        supported = tuple(
            candidate
            for candidate in candidates
            if candidate.projection.source_conserved
            and candidate.source_ids
            and FlangeDerivation.NEUTRAL_AXIS_FROM_PAIRED_WEB_COURSES
            in candidate.derivations
            and support in candidate.support_source_sets
            and abs(candidate.longitudinal_span - round(candidate.longitudinal_span))
            <= 1e-9
        )
        by_length: dict[float, list[FlangeOutlineCandidate]] = {}
        for candidate in supported:
            by_length.setdefault(candidate.longitudinal_span, []).append(candidate)
        if len(by_length) != 2:
            rejected_supports.append(supporting_web.candidate_id)
            continue
        low, high = sorted(by_length)
        generated.extend(
            (first, second)
            for first, second in product(by_length[low], by_length[high])
        )
    pairs = _deduplicate_pairs(generated)
    if not pairs:
        raise RoleHypothesisError(
            "cranked web does not prove exactly two neutral flange courses"
        )
    return RolePairSearchResult(
        pairs=pairs,
        generated_pair_ids=tuple(
            _pair_id(first, second) for first, second in pairs
        ),
        enumerator_exhausted=True,
        diagnostics=tuple(
            (
                *(
                    ("BOX.ROLE.FLANGE.CRANKED_BOLT_SUPPORT_PROVEN",)
                    if bolt_openings
                    else ()
                ),
                *(
                    f"BOX.ROLE.FLANGE.CRANKED_SUPPORT_REJECTED:{candidate_id}"
                    for candidate_id in sorted(rejected_supports)
                ),
            )
        ),
    )


__all__ = (
    "FlangeCourseEvidence",
    "RoleHypothesisError",
    "RolePairSearchResult",
    "enumerate_cranked_flange_role_pairs",
    "enumerate_straight_flange_role_pairs",
    "enumerate_web_role_pairs",
)
