from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import permutations
from math import cos, isfinite, radians, sin

from shapely.geometry import MultiPoint, Point
from shapely.geometry.base import BaseGeometry

from .metadata import BoxMetadata
from .source_ir import SourceDocumentIR
from .view_frame import PartViewIR
from .view_preprocessing import (
    DIMENSION_RELATIVE_TOLERANCE,
    enumerate_role_view_variants,
)


class ViewAssignmentError(ValueError):
    """Part projections cannot be assigned to BOX section directions."""


class AmbiguousViewAssignmentError(ViewAssignmentError):
    """View-only evidence is tied and must be resolved by full assembly."""


@dataclass(frozen=True, slots=True)
class ViewAssignmentCandidate:
    h_view: PartViewIR
    b_view: PartViewIR
    h_span_error: float
    b_span_error: float
    score: float
    drawing_graph_score: float = 0.0
    drawing_graph_target_group_id: str | None = None
    drawing_graph_source_ids: tuple[str, ...] = ()

    @property
    def signature(self) -> tuple[str, str, float]:
        return (
            self.h_view.group_id,
            self.b_view.group_id,
            round(self.score, 12),
        )


def _relative_error(actual: float, expected: float) -> float:
    if expected <= 0:
        raise ValueError("expected BOX section dimension must be positive")
    return abs(actual - expected) / expected


def _view_geometry_hull(view: PartViewIR) -> BaseGeometry:
    """Return the convex hull of actual Part source geometry in world space."""

    points: list[tuple[float, float]] = []
    for entity in view.entities:
        if entity.layer.casefold() != "part":
            continue
        if (
            entity.kind == "LINE"
            and entity.start is not None
            and entity.end is not None
        ):
            points.extend((entity.start, entity.end))
            continue
        if (
            entity.kind == "ARC"
            and entity.center is not None
            and entity.radius is not None
            and entity.start_angle is not None
            and entity.end_angle is not None
        ):
            sweep = (entity.end_angle - entity.start_angle) % 360.0
            angles = (entity.start_angle, entity.end_angle, 0.0, 90.0, 180.0, 270.0)
            for angle in angles:
                if (angle - entity.start_angle) % 360.0 > sweep + 1e-9:
                    continue
                angle_radians = radians(angle)
                points.append(
                    (
                        entity.center[0] + entity.radius * cos(angle_radians),
                        entity.center[1] + entity.radius * sin(angle_radians),
                    )
                )
            continue
        if (
            entity.kind == "CIRCLE"
            and entity.center is not None
            and entity.radius is not None
        ):
            x, y = entity.center
            radius = entity.radius
            points.extend(
                (
                    (x - radius, y),
                    (x + radius, y),
                    (x, y - radius),
                    (x, y + radius),
                )
            )
            continue
        points.extend((point[0], point[1]) for point in entity.points)
    finite = tuple(point for point in points if all(isfinite(value) for value in point))
    return MultiPoint(finite).convex_hull


def _part_mark_h_view_target(
    source: SourceDocumentIR,
    views: tuple[PartViewIR, ...],
    member_mark: str,
) -> tuple[str, tuple[str, ...]] | None:
    """Resolve a Tekla PartMark leader endpoint to one Part projection."""

    expected = member_mark.strip().casefold()
    hulls = {view.group_id: _view_geometry_hull(view).buffer(0.1) for view in views}
    relations: list[tuple[str, tuple[str, ...]]] = []
    for group in source.groups_by_layer("PartMark"):
        entities = source.entities_for_group(group.group_id)
        texts = {
            (entity.text_decoded or "").strip().casefold()
            for entity in entities
            if entity.text_decoded is not None
        }
        if expected not in texts:
            continue
        targets: set[str] = set()
        for entity in entities:
            if (
                entity.layer.casefold() != "partmark"
                or entity.kind != "LINE"
                or entity.start is None
                or entity.end is None
            ):
                continue
            for endpoint in (entity.start, entity.end):
                containing = tuple(
                    group_id
                    for group_id, hull in hulls.items()
                    if hull.covers(Point(endpoint))
                )
                if len(containing) == 1:
                    targets.add(containing[0])
        if len(targets) == 1:
            relations.append((next(iter(targets)), group.source_ids))
    target_ids = {target for target, _ in relations}
    if len(target_ids) != 1:
        return None
    target = next(iter(target_ids))
    source_ids = tuple(
        sorted(
            {
                source_id
                for relation_target, relation_sources in relations
                if relation_target == target
                for source_id in relation_sources
            }
        )
    )
    return target, source_ids


def enumerate_view_assignments(
    views: Iterable[PartViewIR],
    metadata: BoxMetadata,
    *,
    source: SourceDocumentIR | None = None,
) -> tuple[ViewAssignmentCandidate, ...]:
    """Rank all ordered H/B projection pairs without freezing a local choice."""

    materialized = tuple(views)
    if len(materialized) < 2:
        raise ViewAssignmentError("BOX compilation requires two Part projections")
    height = metadata.profile.value.height
    width = metadata.profile.value.width
    drawing_relation = (
        _part_mark_h_view_target(
            source,
            materialized,
            metadata.member_mark.value,
        )
        if source is not None
        else None
    )
    candidates: list[ViewAssignmentCandidate] = []
    nominal_length = metadata.nominal_length.value
    h_axis_is_dimensionally_ambiguous = (
        _relative_error(nominal_length, height)
        <= DIMENSION_RELATIVE_TOLERANCE
        and _relative_error(nominal_length, width)
        > DIMENSION_RELATIVE_TOLERANCE
    )
    for h_source_view, b_source_view in permutations(materialized, 2):
        if h_source_view.group_id == b_source_view.group_id:
            continue
        h_variants = (
            enumerate_role_view_variants(
                h_source_view,
                nominal_length_mm=nominal_length,
                transverse_mm=height,
            )
            if h_axis_is_dimensionally_ambiguous
            else (h_source_view,)
        )
        b_variants = (b_source_view,)
        for h_view in h_variants:
            for b_view in b_variants:
                h_error = _relative_error(h_view.frame.transverse_span, height)
                b_error = _relative_error(b_view.frame.transverse_span, width)
                candidates.append(
                    ViewAssignmentCandidate(
                        h_view=h_view,
                        b_view=b_view,
                        h_span_error=h_error,
                        b_span_error=b_error,
                        score=h_error + b_error,
                        drawing_graph_score=(
                            1.0
                            if drawing_relation is not None
                            and h_view.group_id == drawing_relation[0]
                            else (
                                -1.0
                                if drawing_relation is not None
                                and b_view.group_id == drawing_relation[0]
                                else 0.0
                            )
                        ),
                        drawing_graph_target_group_id=(
                            drawing_relation[0]
                            if drawing_relation is not None
                            else None
                        ),
                        drawing_graph_source_ids=(
                            drawing_relation[1]
                            if drawing_relation is not None
                            else ()
                        ),
                    )
                )
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                candidate.score,
                -candidate.drawing_graph_score,
                candidate.h_view.group_id,
                candidate.b_view.group_id,
            ),
        )
    )


def resolve_unique_view_assignment(
    candidates: Iterable[ViewAssignmentCandidate],
    *,
    score_tolerance: float = 1e-8,
) -> ViewAssignmentCandidate:
    """Resolve only a genuinely unique view-only winner; otherwise fail closed."""

    materialized = tuple(candidates)
    if not materialized:
        raise ViewAssignmentError("no BOX view assignments were generated")
    if len(materialized) > 1:
        margin = materialized[1].score - materialized[0].score
        semantic_margin = (
            materialized[0].drawing_graph_score - materialized[1].drawing_graph_score
        )
        if margin <= score_tolerance and semantic_margin <= 0.0:
            raise AmbiguousViewAssignmentError(
                "view assignment is tied; complete assembly evidence is required"
            )
    return materialized[0]
