from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from math import hypot

from shapely.geometry import Point

from .manufacturing_ir import CircularCutIR, EvidenceState, FeatureEvidence
from .projection_geometry import ProjectionFaceCandidate
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
