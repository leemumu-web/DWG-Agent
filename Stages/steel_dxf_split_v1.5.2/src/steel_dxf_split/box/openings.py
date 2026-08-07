from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from hashlib import sha256
from math import atan, ceil, cos, hypot, pi

from shapely import normalize
from shapely.affinity import translate
from shapely.geometry import Point, box

from .manufacturing_ir import (
    CircularCutIR,
    ContourSegmentIR,
    EvidenceState,
    FeatureEvidence,
    InnerContourIR,
    ManufacturingIRValidationError,
    PhysicalPlateRole,
    contour_polygon,
)
from .projection_geometry import (
    ProjectedSourceLoop,
    ProjectionFaceCandidate,
    inventory_isolated_part_loops,
)
from .projection_lowering import lower_projected_loop_to_contour
from .source_ir import (
    SourceDocumentIR,
    SourceEntityIR,
    is_hidden_projection_linetype,
)
from .view_frame import PartViewIR

Point2 = tuple[float, float]


class CircularOpeningKind(StrEnum):
    BOLT_CIRCLE = "bolt_circle"
    PART_ARC_CIRCLE = "part_arc_circle"


class OpeningVisibility(StrEnum):
    VISIBLE = "visible"
    HIDDEN = "hidden"
    MIXED = "mixed"


class OpeningOwnershipScopeError(ValueError):
    """An opening ownership decision has no complete auditable view scope."""


@dataclass(frozen=True, slots=True)
class OpeningOwnershipRoleCandidate:
    """One physical BOX role materialized in a hypothesis/view scope."""

    candidate_id: str
    role: PhysicalPlateRole
    projection: ProjectionFaceCandidate


def _part_view_digest(view: PartViewIR) -> str:
    payload = {
        "group_id": view.group_id,
        "block_name": view.block_name,
        "frame": asdict(view.frame),
        "entities": [asdict(entity) for entity in view.entities],
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _projection_payload(candidate: ProjectionFaceCandidate) -> dict[str, object]:
    return {
        "polygon_wkb": normalize(candidate.polygon).wkb_hex,
        "boundary_source_ids": sorted(candidate.boundary_source_ids),
        "vertex_source_ids": sorted(candidate.vertex_source_ids),
        "source_conserved": candidate.source_conserved,
        "grid_size_mm": candidate.grid_size_mm,
        "rule_ids": sorted(candidate.rule_ids),
    }


@dataclass(frozen=True, slots=True, init=False)
class OpeningCandidateSearchSnapshot:
    """Immutable candidate-search evidence captured for one exact Part view."""

    view_group_id: str
    view_digest: str
    candidates: tuple[ProjectionFaceCandidate, ...]
    enumerator_id: str
    enumerator_exhausted: bool
    source_ids: tuple[str, ...]
    snapshot_digest: str

    @classmethod
    def capture(
        cls,
        *,
        view: PartViewIR,
        candidates: tuple[ProjectionFaceCandidate, ...],
        enumerator_id: str,
        enumerator_exhausted: bool,
    ) -> OpeningCandidateSearchSnapshot:
        if not isinstance(candidates, tuple):
            raise OpeningOwnershipScopeError(
                "candidate search candidates must be an immutable tuple"
            )
        if not enumerator_id:
            raise OpeningOwnershipScopeError(
                "candidate search requires enumerator identity"
            )
        if not isinstance(enumerator_exhausted, bool):
            raise OpeningOwnershipScopeError(
                "enumerator_exhausted must be boolean"
            )
        view_digest = _part_view_digest(view)
        candidate_payloads = [_projection_payload(candidate) for candidate in candidates]
        source_ids = tuple(sorted(entity.source_id for entity in view.entities))
        source_id_set = frozenset(source_ids)
        candidate_source_sets = tuple(
            frozenset(candidate.boundary_source_ids)
            | frozenset(candidate.vertex_source_ids)
            for candidate in candidates
        )
        if any(not value for value in candidate_source_sets):
            raise OpeningOwnershipScopeError(
                "every candidate search projection requires source membership"
            )
        if any(
            not value.issubset(source_id_set) for value in candidate_source_sets
        ):
            raise OpeningOwnershipScopeError(
                "candidate search contains sources outside the exact Part view"
            )
        payload = {
            "view_digest": view_digest,
            "candidates": candidate_payloads,
            "enumerator_id": enumerator_id,
            "enumerator_exhausted": enumerator_exhausted,
            "source_ids": source_ids,
        }
        instance = object.__new__(cls)
        object.__setattr__(instance, "view_group_id", view.group_id)
        object.__setattr__(instance, "view_digest", view_digest)
        object.__setattr__(instance, "candidates", candidates)
        object.__setattr__(instance, "enumerator_id", enumerator_id)
        object.__setattr__(instance, "enumerator_exhausted", enumerator_exhausted)
        object.__setattr__(instance, "source_ids", source_ids)
        object.__setattr__(
            instance,
            "snapshot_digest",
            sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest(),
        )
        return instance


@dataclass(frozen=True, slots=True, init=False)
class OpeningOwnershipScope:
    """Complete same-view physical-role scope for one BOX hypothesis."""

    hypothesis_id: str
    view_group_id: str
    role_candidates: tuple[OpeningOwnershipRoleCandidate, ...]
    search_complete: bool
    candidate_search_digest: str
    scope_digest: str = field(init=False)

    @classmethod
    def from_candidate_search(
        cls,
        *,
        hypothesis_id: str,
        view: PartViewIR,
        candidate_search: OpeningCandidateSearchSnapshot,
        role_candidates: tuple[OpeningOwnershipRoleCandidate, ...],
    ) -> OpeningOwnershipScope:
        if candidate_search.view_digest != _part_view_digest(view):
            raise OpeningOwnershipScopeError(
                "candidate search snapshot does not belong to the exact Part view"
            )
        if not candidate_search.enumerator_exhausted:
            raise OpeningOwnershipScopeError(
                "opening ownership candidate search is incomplete"
            )
        if any(not value.projection.source_conserved for value in role_candidates):
            raise OpeningOwnershipScopeError(
                "opening ownership requires source-conserved selected candidates"
            )
        searched_projection_keys = {
            json.dumps(
                _projection_payload(candidate),
                sort_keys=True,
                separators=(",", ":"),
            )
            for candidate in candidate_search.candidates
        }
        if any(
            json.dumps(
                _projection_payload(value.projection),
                sort_keys=True,
                separators=(",", ":"),
            )
            not in searched_projection_keys
            for value in role_candidates
        ):
            raise OpeningOwnershipScopeError(
                "selected role candidate is missing from candidate search snapshot"
            )
        instance = object.__new__(cls)
        object.__setattr__(instance, "hypothesis_id", hypothesis_id)
        object.__setattr__(instance, "view_group_id", view.group_id)
        object.__setattr__(instance, "role_candidates", role_candidates)
        object.__setattr__(
            instance,
            "search_complete",
            candidate_search.enumerator_exhausted,
        )
        object.__setattr__(
            instance,
            "candidate_search_digest",
            candidate_search.snapshot_digest,
        )
        instance.__post_init__()
        return instance

    def __post_init__(self) -> None:
        if not self.hypothesis_id or not self.view_group_id:
            raise OpeningOwnershipScopeError(
                "ownership scope requires hypothesis and view identity"
            )
        if not isinstance(self.search_complete, bool):
            raise OpeningOwnershipScopeError("search_complete must be boolean")
        candidate_ids = tuple(value.candidate_id for value in self.role_candidates)
        if any(not value for value in candidate_ids) or len(set(candidate_ids)) != len(
            candidate_ids
        ):
            raise OpeningOwnershipScopeError(
                "ownership scope candidate IDs must be non-empty and unique"
            )
        roles = frozenset(value.role for value in self.role_candidates)
        valid_pairs = {
            frozenset((PhysicalPlateRole.WEB_LEFT, PhysicalPlateRole.WEB_RIGHT)),
            frozenset(
                (PhysicalPlateRole.FLANGE_TOP, PhysicalPlateRole.FLANGE_BOTTOM)
            ),
        }
        if len(self.role_candidates) != 2 or roles not in valid_pairs:
            raise OpeningOwnershipScopeError(
                "ownership scope must contain one complete same-view physical role pair"
            )
        role_payloads = []
        for value in sorted(self.role_candidates, key=lambda item: item.role.value):
            role_payloads.append(
                {
                    "candidate_id": value.candidate_id,
                    "role": value.role.value,
                    "projection": _projection_payload(value.projection),
                }
            )
        payload = json.dumps(
            {
                "hypothesis_id": self.hypothesis_id,
                "view_group_id": self.view_group_id,
                "search_complete": self.search_complete,
                "candidate_search_digest": self.candidate_search_digest,
                "role_candidates": role_payloads,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        object.__setattr__(self, "scope_digest", sha256(payload).hexdigest())


@dataclass(frozen=True, slots=True)
class ProjectedCircularOpening:
    """One reconstructed circular opening observation in a Part view frame."""

    center: Point2
    radius_mm: float
    source_ids: tuple[str, ...]
    cluster_residual_mm: float
    kind: CircularOpeningKind = CircularOpeningKind.BOLT_CIRCLE
    visibility: OpeningVisibility = OpeningVisibility.VISIBLE
    representation_multiplicity: int = 1
    view_group_id: str = ""


@dataclass(frozen=True, slots=True)
class ProjectedInnerContourOpening:
    """One non-circular, source-backed opening observation in a Part view."""

    loop: ProjectedSourceLoop
    visibility: OpeningVisibility
    view_group_id: str

    @property
    def source_ids(self) -> tuple[str, ...]:
        return self.loop.source_ids

    @property
    def representation_multiplicity(self) -> int:
        return self.loop.representation_multiplicity


@dataclass(frozen=True, slots=True)
class OpeningInventoryRejection:
    stage: str
    reason: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InnerContourOpeningInventory:
    openings: tuple[ProjectedInnerContourOpening, ...]
    rejections: tuple[OpeningInventoryRejection, ...]


@dataclass(frozen=True, slots=True)
class InnerContourLoweringResult:
    contours: tuple[InnerContourIR, ...]
    rejections: tuple[OpeningInventoryRejection, ...]


def _contour_sampling_sagitta_bound(
    segments: tuple[ContourSegmentIR, ...],
    *,
    sampling_tolerance_mm: float = 0.1,
) -> float:
    """Bound exact bulge departure from MIR's chord-sampled polygon."""

    bound = 0.0
    for segment in segments:
        bulge = float(segment.bulge)
        if abs(bulge) <= 1e-14:
            continue
        start = segment.start
        end = segment.end
        chord = hypot(end[0] - start[0], end[1] - start[1])
        if chord <= 1e-14:
            continue
        sweep = abs(4.0 * atan(bulge))
        radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
        steps = max(
            2,
            int(ceil(sweep * radius / max(sampling_tolerance_mm, 0.01))),
        )
        bound = max(bound, radius * (1.0 - cos(sweep / (2.0 * steps))))
    return bound


def _loop_matches_circle(
    loop: ProjectedSourceLoop,
    *,
    center: Point2,
    radius: float,
    tolerance_mm: float = 0.05,
) -> bool:
    """True when every loop endpoint lies on the given circle's circumference.

    Tekla 会把同一个圆形孔同时输出为 Part 图层的 ARC+切线环（被当作“非圆形
    内轮廓”）与 Bolt 图层的单个 CIRCLE（圆孔）。两种表示指向同一物理孔时，
    内轮廓必须让位于圆孔，否则材料会被内轮廓挖掉后圆孔又无处可放。
    """
    return all(
        abs(hypot(point[0] - center[0], point[1] - center[1]) - radius)
        <= tolerance_mm
        for segment in loop.segments
        for point in (segment.start, segment.end)
    )


def _source_loop_is_circle(
    loop: ProjectedSourceLoop,
    *,
    tolerance_mm: float = 0.05,
) -> bool:
    circles: list[tuple[Point2, float, float]] = []
    for segment in loop.segments:
        bulge = segment.bulge
        if abs(bulge) <= 1e-12:
            return False
        chord_x = segment.end[0] - segment.start[0]
        chord_y = segment.end[1] - segment.start[1]
        chord_length = hypot(chord_x, chord_y)
        if chord_length <= 1e-12:
            return False
        midpoint = (
            (segment.start[0] + segment.end[0]) / 2.0,
            (segment.start[1] + segment.end[1]) / 2.0,
        )
        center_offset = chord_length * (1.0 - bulge * bulge) / (4.0 * bulge)
        center = (
            midpoint[0] - chord_y / chord_length * center_offset,
            midpoint[1] + chord_x / chord_length * center_offset,
        )
        radius = hypot(segment.start[0] - center[0], segment.start[1] - center[1])
        circles.append((center, radius, 4.0 * atan(bulge)))
    first_center, first_radius, _first_sweep = circles[0]
    return (
        abs(abs(sum(sweep for _center, _radius, sweep in circles)) - 2.0 * pi)
        <= 1e-6
        and all(
            hypot(center[0] - first_center[0], center[1] - first_center[1])
            <= tolerance_mm
            and abs(radius - first_radius) <= tolerance_mm
            for center, radius, _sweep in circles[1:]
        )
    )


def project_inner_contour_openings(
    view: PartViewIR,
    *,
    circular_openings: tuple[ProjectedCircularOpening, ...] = (),
) -> InnerContourOpeningInventory:
    """Project non-circular isolated Part loops in one selected view."""

    loop_inventory = inventory_isolated_part_loops(view.entities, view.frame)
    projected: list[ProjectedInnerContourOpening] = []
    for loop in loop_inventory.loops:
        if _source_loop_is_circle(loop):
            continue
        if any(
            _loop_matches_circle(
                loop,
                center=opening.center,
                radius=opening.radius_mm,
            )
            for opening in circular_openings
        ):
            # 同一物理孔已被 Bolt CIRCLE 识别为圆孔：内轮廓只是它的另一种
            # 表示，不作为独立内轮廓，避免材料被重复挖掉后圆孔校验失败。
            continue
        visibility = (
            OpeningVisibility.HIDDEN
            if loop.hidden_source_ids and not loop.visible_source_ids
            else (
                OpeningVisibility.VISIBLE
                if loop.visible_source_ids and not loop.hidden_source_ids
                else OpeningVisibility.MIXED
            )
        )
        projected.append(
            ProjectedInnerContourOpening(
                loop=loop,
                visibility=visibility,
                view_group_id=view.group_id,
            )
        )
    return InnerContourOpeningInventory(
        openings=tuple(projected),
        rejections=tuple(
            OpeningInventoryRejection(
                stage="projection",
                reason=rejection.reason,
                source_ids=rejection.source_ids,
            )
            for rejection in loop_inventory.rejections
        ),
    )


def _owners_form_coincident_physical_pair(
    owners: tuple[OpeningOwnershipRoleCandidate, ...],
    *,
    tolerance_mm: float,
) -> bool:
    if len(owners) != 2:
        return False
    first, second = owners
    first_polygon = first.projection.polygon
    second_polygon = second.projection.polygon
    if (
        first_polygon.boundary.hausdorff_distance(second_polygon.boundary)
        > tolerance_mm
    ):
        return False
    if abs(first_polygon.area - second_polygon.area) > tolerance_mm * max(
        first_polygon.length,
        second_polygon.length,
        1.0,
    ):
        return False
    role_pair = frozenset((first.role, second.role))
    return role_pair in {
        frozenset((PhysicalPlateRole.WEB_LEFT, PhysicalPlateRole.WEB_RIGHT)),
        frozenset((PhysicalPlateRole.FLANGE_TOP, PhysicalPlateRole.FLANGE_BOTTOM)),
    }


def _visibility_owner_for_coincident_pair(
    owners: tuple[OpeningOwnershipRoleCandidate, ...],
    opening: ProjectedInnerContourOpening,
    *,
    tolerance_mm: float,
) -> OpeningOwnershipRoleCandidate | None:
    if (
        opening.visibility is OpeningVisibility.MIXED
        or not _owners_form_coincident_physical_pair(
            owners,
            tolerance_mm=tolerance_mm,
        )
    ):
        return None
    role_pair = frozenset(owner.role for owner in owners)
    near_role_by_pair = {
        frozenset((PhysicalPlateRole.WEB_LEFT, PhysicalPlateRole.WEB_RIGHT)): (
            PhysicalPlateRole.WEB_LEFT
        ),
        frozenset((PhysicalPlateRole.FLANGE_TOP, PhysicalPlateRole.FLANGE_BOTTOM)): (
            PhysicalPlateRole.FLANGE_TOP
        ),
    }
    near_role = near_role_by_pair.get(role_pair)
    if near_role is None:
        return None
    selected_role = (
        near_role
        if opening.visibility is OpeningVisibility.VISIBLE
        else next(role for role in role_pair if role is not near_role)
    )
    return next(owner for owner in owners if owner.role is selected_role)


def _opening_matches_candidate_exterior(
    opening: ProjectedInnerContourOpening,
    candidate: OpeningOwnershipRoleCandidate,
    *,
    tolerance_mm: float,
) -> bool:
    opening_polygon = opening.loop.polygon
    candidate_polygon = candidate.projection.polygon
    if (
        opening_polygon.boundary.hausdorff_distance(candidate_polygon.boundary)
        > tolerance_mm
    ):
        return False
    return abs(opening_polygon.area - candidate_polygon.area) <= tolerance_mm * max(
        opening_polygon.length,
        candidate_polygon.length,
        1.0,
    )


def _opening_is_full_transverse_candidate_course(
    opening: ProjectedInnerContourOpening,
    candidates: tuple[OpeningOwnershipRoleCandidate, ...],
    *,
    tolerance_mm: float,
) -> bool:
    """Identify a source-backed plate/course loop before slot ownership.

    A valid through-opening must have clearance in the plate transverse
    direction.  A loop spanning essentially the whole candidate width and
    reusing at least one of its source boundary entities is instead another
    projection spelling of plate geometry.  The source-overlap requirement
    keeps an unrelated loop that merely crosses an exterior fail-closed.
    """

    _opening_min_x, opening_min_y, _opening_max_x, opening_max_y = (
        opening.loop.polygon.bounds
    )
    opening_transverse_span = opening_max_y - opening_min_y
    opening_source_ids = set(opening.source_ids)
    for candidate in candidates:
        _candidate_min_x, candidate_min_y, _candidate_max_x, candidate_max_y = (
            candidate.projection.polygon.bounds
        )
        candidate_transverse_span = candidate_max_y - candidate_min_y
        candidate_source_ids = set(candidate.projection.boundary_source_ids) | set(
            candidate.projection.vertex_source_ids
        )
        if (
            candidate_transverse_span > tolerance_mm
            # Tekla may inset the end-course loop by one wall thickness on
            # both sides; 85% still requires a near-full-width course, while
            # the source-boundary overlap below prevents an ordinary slot
            # from being reclassified as plate context.
            and opening_transverse_span
            >= candidate_transverse_span * 0.85 - tolerance_mm
            and opening_source_ids.intersection(candidate_source_ids)
            and candidate.projection.polygon.boundary.distance(
                opening.loop.polygon.boundary
            )
            <= tolerance_mm
        ):
            return True
    return False


def _opening_is_outer_section_envelope(
    opening: ProjectedInnerContourOpening,
    candidates: tuple[OpeningOwnershipRoleCandidate, ...],
    *,
    tolerance_mm: float,
) -> bool:
    """Identify a full-section loop surrounding a developed plate course."""

    opening_polygon = opening.loop.polygon
    opening_rectangle = box(*opening_polygon.bounds)
    opening_area_tolerance = max(
        tolerance_mm * max(opening_rectangle.length, 1.0),
        tolerance_mm**2,
    )
    if (
        opening_polygon.symmetric_difference(opening_rectangle).area
        > opening_area_tolerance
    ):
        return False
    opening_min_x, opening_min_y, opening_max_x, opening_max_y = (
        opening_polygon.bounds
    )
    for candidate in candidates:
        candidate_polygon = candidate.projection.polygon
        candidate_rectangle = box(*candidate_polygon.bounds)
        candidate_area_tolerance = max(
            tolerance_mm * max(candidate_rectangle.length, 1.0),
            tolerance_mm**2,
        )
        if (
            candidate_polygon.symmetric_difference(candidate_rectangle).area
            > candidate_area_tolerance
        ):
            continue
        candidate_min_x, candidate_min_y, candidate_max_x, candidate_max_y = (
            candidate_polygon.bounds
        )
        if (
            abs(opening_min_x - candidate_min_x) <= tolerance_mm
            and abs(opening_max_x - candidate_max_x) <= tolerance_mm
            and opening_min_y < candidate_min_y - tolerance_mm
            and opening_max_y > candidate_max_y + tolerance_mm
            and opening_polygon.buffer(tolerance_mm).covers(candidate_polygon)
            and opening_polygon.boundary.distance(candidate_polygon.boundary)
            <= tolerance_mm
        ):
            return True
    return False


def lower_inner_contour_openings(
    target_role_candidate: OpeningOwnershipRoleCandidate,
    openings: InnerContourOpeningInventory,
    *,
    ownership_scope: OpeningOwnershipScope,
    boundary_tolerance_mm: float = 0.15,
) -> InnerContourLoweringResult:
    """Lower observations owned by one plate in the complete same-view scope.

    The scope represents one fully materialized hypothesis.  Alternative
    hypotheses are evaluated with their own scope; unrelated alternatives are
    never treated as simultaneously existing physical plates.
    """

    if boundary_tolerance_mm <= 0.0:
        raise ValueError("boundary_tolerance_mm must be positive")
    if not ownership_scope.search_complete:
        raise OpeningOwnershipScopeError(
            "opening ownership search scope is incomplete"
        )
    if any(
        opening.view_group_id != ownership_scope.view_group_id
        for opening in openings.openings
    ):
        raise OpeningOwnershipScopeError(
            "opening observation view does not match ownership scope"
        )
    scoped_targets = tuple(
        value
        for value in ownership_scope.role_candidates
        if value.candidate_id == target_role_candidate.candidate_id
    )
    if len(scoped_targets) != 1 or scoped_targets[0] != target_role_candidate:
        raise OpeningOwnershipScopeError(
            "target role candidate is not an exact member of ownership scope"
        )
    plate_projection = target_role_candidate.projection
    ownership_candidates = ownership_scope.role_candidates
    origin = tuple(float(value) for value in plate_projection.polygon.bounds[:2])
    contours: list[InnerContourIR] = []
    rejections = list(openings.rejections)
    for opening in openings.openings:
        try:
            segments = lower_projected_loop_to_contour(
                opening.loop,
                origin=(origin[0], origin[1]),
            )
        except ManufacturingIRValidationError:
            rejections.append(
                OpeningInventoryRejection(
                    stage="lowering",
                    reason="invalid_manufacturing_contour",
                    source_ids=opening.source_ids,
                )
            )
            continue
        sampled_opening_polygon = contour_polygon(segments)
        sagitta_bound = _contour_sampling_sagitta_bound(segments)
        conservative_opening_polygon = translate(
            sampled_opening_polygon.buffer(sagitta_bound + 1e-9),
            xoff=origin[0],
            yoff=origin[1],
        )
        if any(
            _opening_matches_candidate_exterior(
                opening,
                candidate,
                tolerance_mm=boundary_tolerance_mm,
            )
            for candidate in ownership_candidates
        ):
            rejections.append(
                OpeningInventoryRejection(
                    stage="ownership",
                    reason="candidate_exterior_boundary",
                    source_ids=opening.source_ids,
                )
            )
            continue
        if _opening_is_full_transverse_candidate_course(
            opening,
            ownership_candidates,
            tolerance_mm=boundary_tolerance_mm,
        ) or _opening_is_outer_section_envelope(
            opening,
            ownership_candidates,
            tolerance_mm=boundary_tolerance_mm,
        ):
            rejections.append(
                OpeningInventoryRejection(
                    stage="ownership",
                    reason="candidate_course_context",
                    source_ids=opening.source_ids,
                )
            )
            continue
        if any(
            candidate.projection.polygon.boundary.distance(
                conservative_opening_polygon.boundary
            )
            <= boundary_tolerance_mm
            for candidate in ownership_candidates
        ):
            # A loop coincident with, crossing, or lacking clearance from any
            # candidate exterior is plate/course evidence, not a proved hole.
            rejections.append(
                OpeningInventoryRejection(
                    stage="ownership",
                    reason="candidate_boundary_conflict",
                    source_ids=opening.source_ids,
                )
            )
            continue
        owners = tuple(
            candidate
            for candidate in ownership_candidates
            if candidate.projection.polygon.buffer(-boundary_tolerance_mm).covers(
                conservative_opening_polygon
            )
        )
        if not owners:
            rejections.append(
                OpeningInventoryRejection(
                    stage="ownership",
                    reason="unowned_opening",
                    source_ids=opening.source_ids,
                )
            )
            continue
        ownership_rule = "BOX.OPENING.ROLE_AWARE_UNIQUE_CONTAINMENT"
        if len(owners) > 1:
            if (
                opening.visibility is OpeningVisibility.MIXED
                and opening.representation_multiplicity >= 2
                and _owners_form_coincident_physical_pair(
                    owners,
                    tolerance_mm=boundary_tolerance_mm,
                )
            ):
                owner = target_role_candidate
                ownership_rule = (
                    "BOX.OPENING.ROLE_AWARE_SHARED_ON_COINCIDENT_PAIR"
                )
            else:
                visibility_owner = _visibility_owner_for_coincident_pair(
                    owners,
                    opening,
                    tolerance_mm=boundary_tolerance_mm,
                )
                if visibility_owner is None:
                    rejections.append(
                        OpeningInventoryRejection(
                            stage="ownership",
                            reason="ambiguous_ownership",
                            source_ids=opening.source_ids,
                        )
                    )
                    continue
                owner = visibility_owner
                ownership_rule = (
                    "BOX.OPENING.ROLE_AWARE_VISIBILITY_ON_COINCIDENT_PAIR"
                )
        else:
            owner = owners[0]
        if owner.candidate_id != target_role_candidate.candidate_id:
            continue
        payload = (
            f"{ownership_scope.scope_digest}|{target_role_candidate.role.value}|"
            f"{opening.view_group_id}|{'|'.join(opening.source_ids)}|"
            f"{opening.loop.polygon.wkb_hex}"
        )
        evidence = FeatureEvidence(
            state=(
                EvidenceState.INFERRED
                if (
                    opening.loop.residual_mm > 1e-12
                    or ownership_rule
                    == "BOX.OPENING.ROLE_AWARE_VISIBILITY_ON_COINCIDENT_PAIR"
                )
                else EvidenceState.DIRECT
            ),
            source_ids=opening.source_ids,
            rule_ids=(
                "BOX.OPENING.PART_SIMPLE_INNER_LOOP",
                ownership_rule,
                f"BOX.OPENING.OWNERSHIP_SCOPE.{ownership_scope.scope_digest}",
                f"BOX.OPENING.VISIBILITY.{opening.visibility.name}",
                "BOX.OPENING.REPRESENTATION_MULTIPLICITY_"
                f"{opening.representation_multiplicity}",
            ),
            proof_ids=("BOX.PROOF.OPENING.WITHIN_PLATE",),
            residual_mm=opening.loop.residual_mm,
            description=(
                "source-backed non-circular Part loop assigned to one physical "
                "plate role in the complete same-view pair"
            ),
        )
        contours.append(
            InnerContourIR(
                contour_id=(
                    "part-loop:"
                    f"{sha256(payload.encode('utf-8')).hexdigest()[:16]}"
                ),
                segments=segments,
                evidence=evidence,
            )
        )
    return InnerContourLoweringResult(
        contours=tuple(sorted(contours, key=lambda contour: contour.contour_id)),
        rejections=tuple(
            sorted(
                rejections,
                key=lambda value: (value.stage, value.reason, value.source_ids),
            )
        ),
    )


def _circle_belongs_to_view(
    center: Point2,
    radius: float,
    view: PartViewIR,
    tolerance: float,
) -> bool:
    local = view.frame.world_to_local(center)
    return (
        view.frame.longitudinal_min - radius - tolerance
        <= local[0]
        <= view.frame.longitudinal_max + radius + tolerance
        and view.frame.transverse_min - radius - tolerance
        <= local[1]
        <= view.frame.transverse_max + radius + tolerance
    )


def project_circular_openings(
    source: SourceDocumentIR,
    view: PartViewIR,
    *,
    duplicate_tolerance_mm: float = 0.05,
) -> tuple[ProjectedCircularOpening, ...]:
    """Associate and deduplicate Tekla Bolt CIRCLE objects for one view.

    Tekla may emit the same physical bolt object more than once (for example,
    visible and projected object groups).  Clustering is purely geometric and
    retains every source ID; it never assigns the hole to a plate role.
    """

    if duplicate_tolerance_mm <= 0:
        raise ValueError("duplicate_tolerance_mm must be positive")
    pending: list[tuple[Point2, float, str]] = []
    for entity in source.entities:
        if (
            entity.layer.casefold() != "bolt"
            or entity.kind != "CIRCLE"
            or entity.center is None
            or entity.radius is None
            or entity.radius <= 0
            or not _circle_belongs_to_view(
                entity.center,
                entity.radius,
                view,
                duplicate_tolerance_mm,
            )
        ):
            continue
        pending.append(
            (
                view.frame.world_to_local(entity.center),
                entity.radius,
                entity.source_id,
            )
        )
    pending.sort(key=lambda item: (item[0], item[1], item[2]))

    clusters: list[list[tuple[Point2, float, str]]] = []
    for opening in pending:
        for cluster in clusters:
            center = (
                sum(item[0][0] for item in cluster) / len(cluster),
                sum(item[0][1] for item in cluster) / len(cluster),
            )
            radius = sum(item[1] for item in cluster) / len(cluster)
            if (
                hypot(opening[0][0] - center[0], opening[0][1] - center[1])
                <= duplicate_tolerance_mm
                and abs(opening[1] - radius) <= duplicate_tolerance_mm
            ):
                cluster.append(opening)
                break
        else:
            clusters.append([opening])

    result = []
    for cluster in clusters:
        center = (
            sum(item[0][0] for item in cluster) / len(cluster),
            sum(item[0][1] for item in cluster) / len(cluster),
        )
        radius = sum(item[1] for item in cluster) / len(cluster)
        residual = max(
            hypot(item[0][0] - center[0], item[0][1] - center[1]) for item in cluster
        )
        result.append(
            ProjectedCircularOpening(
                center=center,
                radius_mm=radius,
                source_ids=tuple(sorted(item[2] for item in cluster)),
                cluster_residual_mm=residual,
                view_group_id=view.group_id,
            )
        )
    return tuple(
        sorted(result, key=lambda opening: (opening.center, opening.radius_mm))
    )


def _arc_intervals_degrees(entity: SourceEntityIR) -> tuple[tuple[float, float], ...]:
    if entity.start_angle is None or entity.end_angle is None:
        return ()
    start = entity.start_angle % 360.0
    sweep = (entity.end_angle - entity.start_angle) % 360.0
    if sweep <= 1e-9:
        return ()
    end = start + sweep
    if end <= 360.0 + 1e-9:
        return ((start, min(end, 360.0)),)
    return ((start, 360.0), (0.0, end - 360.0))


def _angular_coverage_degrees(entities: tuple[SourceEntityIR, ...]) -> float:
    intervals = sorted(
        interval
        for entity in entities
        for interval in _arc_intervals_degrees(entity)
    )
    if not intervals:
        return 0.0
    coverage = 0.0
    current_start, current_end = intervals[0]
    for start, end in intervals[1:]:
        if start <= current_end + 1e-9:
            current_end = max(current_end, end)
            continue
        coverage += current_end - current_start
        current_start, current_end = start, end
    return coverage + current_end - current_start


def _arc_sweep_degrees(entity: SourceEntityIR) -> float:
    if entity.start_angle is None or entity.end_angle is None:
        return 0.0
    return (entity.end_angle - entity.start_angle) % 360.0


def project_part_arc_openings(
    _source: SourceDocumentIR,
    view: PartViewIR,
    *,
    duplicate_tolerance_mm: float = 0.05,
    minimum_coverage_degrees: float = 350.0,
) -> tuple[ProjectedCircularOpening, ...]:
    """Reconstruct full manufacturing circles drawn as ``Part`` ARC entities.

    Tekla may express one projected opening as two semicircles, and may repeat
    that representation for another visibility/depth channel.  This function
    keeps the complete source lineage and representation multiplicity while
    returning one physical opening observation per coincident arc cluster.
    Dimension and annotation arcs are excluded by the exact ``Part`` layer
    requirement.
    """

    if duplicate_tolerance_mm <= 0:
        raise ValueError("duplicate_tolerance_mm must be positive")
    if not 0.0 < minimum_coverage_degrees <= 360.0:
        raise ValueError("minimum_coverage_degrees must be in (0, 360]")

    pending = tuple(
        entity
        for entity in view.entities
        if entity.layer.casefold() == "part"
        and entity.kind == "ARC"
        and entity.center is not None
        and entity.radius is not None
        and entity.radius > 0.0
        and entity.start_angle is not None
        and entity.end_angle is not None
        and _circle_belongs_to_view(
            entity.center,
            entity.radius,
            view,
            duplicate_tolerance_mm,
        )
    )
    clusters: list[list[SourceEntityIR]] = []
    for entity in sorted(
        pending,
        key=lambda item: (
            item.group_id,
            item.center or (0.0, 0.0),
            item.radius or 0.0,
            item.source_id,
        ),
    ):
        assert entity.center is not None
        assert entity.radius is not None
        for cluster in clusters:
            cluster_centers = tuple(
                item.center for item in cluster if item.center is not None
            )
            cluster_radii = tuple(
                item.radius for item in cluster if item.radius is not None
            )
            center = (
                sum(item[0] for item in cluster_centers) / len(cluster_centers),
                sum(item[1] for item in cluster_centers) / len(cluster_centers),
            )
            radius = sum(cluster_radii) / len(cluster_radii)
            if (
                entity.group_id == cluster[0].group_id
                and hypot(entity.center[0] - center[0], entity.center[1] - center[1])
                <= duplicate_tolerance_mm
                and abs(entity.radius - radius) <= duplicate_tolerance_mm
            ):
                cluster.append(entity)
                break
        else:
            clusters.append([entity])

    openings: list[ProjectedCircularOpening] = []
    for values in clusters:
        cluster = tuple(values)
        coverage = _angular_coverage_degrees(cluster)
        if coverage + 1e-9 < minimum_coverage_degrees:
            continue
        world_centers = tuple(
            entity.center for entity in cluster if entity.center is not None
        )
        radii = tuple(entity.radius for entity in cluster if entity.radius is not None)
        center_world = (
            sum(item[0] for item in world_centers) / len(world_centers),
            sum(item[1] for item in world_centers) / len(world_centers),
        )
        radius = sum(radii) / len(radii)
        residual = max(
            hypot(item[0] - center_world[0], item[1] - center_world[1])
            for item in world_centers
        )
        residual = max(residual, *(abs(item - radius) for item in radii))
        hidden_count = sum(
            is_hidden_projection_linetype(entity.linetype) for entity in cluster
        )
        visibility = (
            OpeningVisibility.HIDDEN
            if hidden_count == len(cluster)
            else (
                OpeningVisibility.VISIBLE
                if hidden_count == 0
                else OpeningVisibility.MIXED
            )
        )
        total_sweep = sum(_arc_sweep_degrees(entity) for entity in cluster)
        multiplicity = max(1, round(total_sweep / 360.0))
        openings.append(
            ProjectedCircularOpening(
                center=view.frame.world_to_local(center_world),
                radius_mm=radius,
                source_ids=tuple(sorted(entity.source_id for entity in cluster)),
                cluster_residual_mm=residual,
                kind=CircularOpeningKind.PART_ARC_CIRCLE,
                visibility=visibility,
                representation_multiplicity=multiplicity,
                view_group_id=view.group_id,
            )
        )
    return tuple(
        sorted(
            openings,
            key=lambda opening: (
                opening.center,
                opening.radius_mm,
                opening.source_ids,
            ),
        )
    )


def circular_opening_is_contained(
    plate_projection: ProjectionFaceCandidate,
    opening: ProjectedCircularOpening,
    *,
    boundary_tolerance_mm: float = 0.15,
) -> bool:
    disk = Point(opening.center).buffer(opening.radius_mm, quad_segs=32)
    return plate_projection.polygon.buffer(boundary_tolerance_mm).covers(disk)


def lower_circular_openings(
    plate_projection: ProjectionFaceCandidate,
    openings: tuple[ProjectedCircularOpening, ...],
    *,
    boundary_tolerance_mm: float = 0.15,
) -> tuple[CircularCutIR, ...]:
    """Lower assigned observations whose full disk lies in one plate."""

    polygon = plate_projection.polygon
    origin_x, origin_y = polygon.bounds[:2]
    cuts: list[CircularCutIR] = []
    for opening in openings:
        if not circular_opening_is_contained(
            plate_projection,
            opening,
            boundary_tolerance_mm=boundary_tolerance_mm,
        ):
            continue
        payload = (
            f"{opening.kind.value}|"
            f"{opening.center[0]:.6f},{opening.center[1]:.6f},"
            f"{opening.radius_mm:.6f}|{'|'.join(opening.source_ids)}"
        )
        if opening.kind is CircularOpeningKind.PART_ARC_CIRCLE:
            prefix = "part-arc"
            rule_ids = (
                "BOX.OPENING.PART_ARC_FULL_CIRCLE_RECONSTRUCTION",
                "BOX.OPENING.ROLE_AWARE_CONTAINMENT",
                f"BOX.OPENING.VISIBILITY.{opening.visibility.name}",
                "BOX.OPENING.REPRESENTATION_MULTIPLICITY_"
                f"{opening.representation_multiplicity}",
            )
            description = (
                "full Part ARC cluster reconstructed and assigned to one plate role; "
                f"visibility={opening.visibility.value}; "
                "representation_multiplicity="
                f"{opening.representation_multiplicity}"
            )
        else:
            prefix = "bolt"
            rule_ids = ("BOX.OPENING.BOLT_CIRCLE_CONTAINMENT",)
            description = "deduplicated Tekla Bolt circle contained by plate projection"
        evidence = FeatureEvidence(
            state=EvidenceState.DIRECT,
            source_ids=opening.source_ids,
            rule_ids=rule_ids,
            proof_ids=("BOX.PROOF.OPENING.WITHIN_PLATE",),
            residual_mm=opening.cluster_residual_mm,
            description=description,
        )
        cuts.append(
            CircularCutIR(
                cut_id=f"{prefix}:{sha256(payload.encode('utf-8')).hexdigest()[:16]}",
                center=(opening.center[0] - origin_x, opening.center[1] - origin_y),
                radius_mm=opening.radius_mm,
                evidence=evidence,
            )
        )
    return tuple(sorted(cuts, key=lambda cut: (cut.center, cut.radius_mm, cut.cut_id)))
