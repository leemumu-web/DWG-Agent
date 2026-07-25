from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from math import atan2, cos, hypot, pi, sin
from typing import Iterable

from .bh_canonical import quantize, resolve_units
from .bh_errors import BHDomainError
from .bh_ir import SemanticLayer
from .bh_source import SourceDocument, SourceEntity
from .bh_topology import connected_source_components
from .geometry_types import Point2D


class FrameInferenceError(BHDomainError):
    diagnostic_code = "BH-FRAME-INFERENCE-FAILED"


@dataclass(frozen=True, slots=True)
class LocalFrame:
    origin: Point2D
    longitudinal: Point2D
    transverse: Point2D
    reflected: bool
    evidence_ids: tuple[str, ...]
    score: float
    canonical_signature: str

    def to_local(self, point: Point2D) -> Point2D:
        dx = point.x - self.origin.x
        dy = point.y - self.origin.y
        return Point2D(
            dx * self.longitudinal.x + dy * self.longitudinal.y,
            dx * self.transverse.x + dy * self.transverse.y,
        )

    def to_local_xy(self, x: float, y: float) -> Point2D:
        return self.to_local(Point2D(x, y))

    def to_world(self, point: Point2D) -> Point2D:
        return Point2D(
            self.origin.x
            + point.x * self.longitudinal.x
            + point.y * self.transverse.x,
            self.origin.y
            + point.x * self.longitudinal.y
            + point.y * self.transverse.y,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "origin": asdict(self.origin),
            "longitudinal": asdict(self.longitudinal),
            "transverse": asdict(self.transverse),
            "reflected": self.reflected,
            "evidence_ids": list(self.evidence_ids),
            "score": self.score,
            "canonical_signature": self.canonical_signature,
        }


@dataclass(frozen=True, slots=True)
class FrameSolveResult:
    candidates: tuple[LocalFrame, ...]
    unique: bool
    score_margin: float

    @property
    def selected(self) -> LocalFrame:
        if not self.candidates:
            raise FrameInferenceError("No member coordinate-frame candidate exists.")
        return self.candidates[0]

    def to_dict(self) -> dict[str, object]:
        return {
            "unique": self.unique,
            "score_margin": self.score_margin,
            "candidates": [item.to_dict() for item in self.candidates],
        }


@dataclass(frozen=True, slots=True)
class _Segment:
    start: Point2D
    end: Point2D
    source_id: str

    @property
    def length(self) -> float:
        return self.start.distance_to(self.end)

    @property
    def angle(self) -> float:
        angle = atan2(self.end.y - self.start.y, self.end.x - self.start.x) % pi
        return 0.0 if abs(angle - pi) <= 1e-12 else angle


@dataclass(slots=True)
class _DirectionCluster:
    segments: list[_Segment]

    @property
    def angle(self) -> float:
        sine = sum(item.length * sin(2.0 * item.angle) for item in self.segments)
        cosine = sum(item.length * cos(2.0 * item.angle) for item in self.segments)
        return (0.5 * atan2(sine, cosine)) % pi

    @property
    def support_length(self) -> float:
        return sum(item.length for item in self.segments)

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(sorted({item.source_id for item in self.segments}))


def _part_entities(source: SourceDocument) -> tuple[SourceEntity, ...]:
    return tuple(
        item
        for item in source.entities
        if item.semantic_hint.role == SemanticLayer.PART_EDGE
        and item.geometry is not None
    )


def _entity_points(entity: SourceEntity) -> tuple[Point2D, ...]:
    if entity.geometry is None:
        return ()
    return tuple(Point2D(x, y) for x, y in entity.geometry.coordinates)


def _entity_segments(entity: SourceEntity) -> Iterable[_Segment]:
    points = _entity_points(entity)
    if entity.entity_type == "LINE" and len(points) == 2:
        yield _Segment(points[0], points[1], entity.source_id)
        return
    if entity.entity_type not in {"LWPOLYLINE", "POLYLINE"} or len(points) < 2:
        return
    for start, end in zip(points, points[1:]):
        if start.distance_to(end) > 1e-9:
            yield _Segment(start, end, entity.source_id)
    if entity.geometry is not None and entity.geometry.closed:
        start, end = points[-1], points[0]
        if start.distance_to(end) > 1e-9:
            yield _Segment(start, end, entity.source_id)


def _angle_distance(left: float, right: float) -> float:
    delta = abs(left - right) % pi
    return min(delta, pi - delta)


def _cluster_directions(
    segments: tuple[_Segment, ...],
    *,
    tolerance_radians: float,
) -> tuple[_DirectionCluster, ...]:
    clusters: list[_DirectionCluster] = []
    for segment in sorted(segments, key=lambda item: (item.angle, -item.length, item.source_id)):
        matching = next(
            (
                cluster
                for cluster in clusters
                if _angle_distance(segment.angle, cluster.angle) <= tolerance_radians
            ),
            None,
        )
        if matching is None:
            clusters.append(_DirectionCluster([segment]))
        else:
            matching.segments.append(segment)
    if len(clusters) > 1 and _angle_distance(clusters[0].angle, clusters[-1].angle) <= tolerance_radians:
        clusters[0].segments.extend(clusters.pop().segments)
    return tuple(clusters)


def _all_part_points(source: SourceDocument) -> tuple[Point2D, ...]:
    points = [point for entity in _part_entities(source) for point in _entity_points(entity)]
    unique = sorted({(round(point.x, 12), round(point.y, 12)) for point in points})
    return tuple(Point2D(x, y) for x, y in unique)


def _part_points_by_container(
    source: SourceDocument,
) -> tuple[tuple[Point2D, ...], ...]:
    grouped_entities: dict[str, list[SourceEntity]] = {}
    for entity in _part_entities(source):
        grouped_entities.setdefault(entity.container_id, []).append(entity)
    explicit = {
        container.container_id: container.explicit_block
        for container in source.containers
    }
    groups: list[tuple[SourceEntity, ...]] = []
    for container_id, entities in sorted(grouped_entities.items()):
        items = tuple(entities)
        if explicit.get(container_id, False):
            groups.append(items)
        else:
            groups.extend(
                component.entities
                for component in connected_source_components(items)
            )
    return tuple(
        tuple(
            Point2D(x, y)
            for x, y in sorted(
                {
                    (round(point.x, 12), round(point.y, 12))
                    for entity in group
                    for point in _entity_points(entity)
                }
            )
        )
        for group in groups
        if sum(len(_entity_points(entity)) for entity in group) >= 2
    )


def _normalized_origin(
    points: tuple[Point2D, ...],
    longitudinal: Point2D,
    transverse: Point2D,
) -> Point2D:
    min_u = min(point.x * longitudinal.x + point.y * longitudinal.y for point in points)
    min_v = min(point.x * transverse.x + point.y * transverse.y for point in points)
    return Point2D(
        min_u * longitudinal.x + min_v * transverse.x,
        min_u * longitudinal.y + min_v * transverse.y,
    )


def _signature_payload(
    source: SourceDocument,
    frame: LocalFrame,
) -> dict[str, object]:
    unit = resolve_units(source.units)
    if not unit.valid or unit.scale_to_mm is None:
        raise FrameInferenceError(f"Cannot infer a physical frame for {unit.source}.")
    points = _all_part_points(source)
    grid_mm = 1e-6
    canonical_points = sorted(
        (
            quantize(frame.to_local(point).x * unit.scale_to_mm, grid_mm),
            quantize(frame.to_local(point).y * unit.scale_to_mm, grid_mm),
        )
        for point in points
    )
    return {"grid_mm": grid_mm, "part_points": canonical_points}


def _signature_key(source: SourceDocument, frame: LocalFrame) -> str:
    return json.dumps(
        _signature_payload(source, frame),
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_frame_signature(source: SourceDocument, frame: LocalFrame) -> str:
    return hashlib.sha256(_signature_key(source, frame).encode()).hexdigest()


def _frame_variants(
    source: SourceDocument,
    *,
    angle: float,
    score: float,
    evidence_ids: tuple[str, ...],
) -> tuple[LocalFrame, ...]:
    points = _all_part_points(source)
    variants: list[LocalFrame] = []
    for longitudinal_sign in (1.0, -1.0):
        longitudinal = Point2D(
            longitudinal_sign * cos(angle),
            longitudinal_sign * sin(angle),
        )
        for reflected in (False, True):
            transverse = (
                Point2D(-longitudinal.y, longitudinal.x)
                if not reflected
                else Point2D(longitudinal.y, -longitudinal.x)
            )
            origin = _normalized_origin(points, longitudinal, transverse)
            provisional = LocalFrame(
                origin=origin,
                longitudinal=longitudinal,
                transverse=transverse,
                reflected=reflected,
                evidence_ids=evidence_ids,
                score=score,
                canonical_signature="",
            )
            signature = canonical_frame_signature(source, provisional)
            variants.append(
                LocalFrame(
                    origin=origin,
                    longitudinal=longitudinal,
                    transverse=transverse,
                    reflected=reflected,
                    evidence_ids=evidence_ids,
                    score=score,
                    canonical_signature=signature,
                )
            )
    return tuple(variants)


def _unit_vector(x: float, y: float) -> Point2D | None:
    length = hypot(x, y)
    if length <= 1e-12:
        return None
    return Point2D(x / length, y / length)


def _explicit_basis_hint(
    source: SourceDocument,
    direction: Point2D,
    *,
    angle_tolerance_radians: float,
) -> tuple[Point2D, Point2D] | None:
    hints = []
    for entity in _part_entities(source):
        if not entity.transform_chain:
            continue
        affine = entity.transform_chain[0]
        longitudinal = _unit_vector(affine.a, affine.b)
        transverse = _unit_vector(affine.c, affine.d)
        if longitudinal is None or transverse is None:
            continue
        alignment = abs(
            longitudinal.x * direction.x + longitudinal.y * direction.y
        )
        if alignment < cos(angle_tolerance_radians):
            continue
        hints.append((longitudinal, transverse))
    if not hints:
        return None
    # Equivalent exporter projection entities can repeat the same local basis.
    # The first canonical numeric basis is deterministic if redundant entities
    # disagree.
    return min(
        hints,
        key=lambda item: (
            round(item[0].x, 12),
            round(item[0].y, 12),
            round(item[1].x, 12),
            round(item[1].y, 12),
        ),
    )


def _closed_polyline_basis_hint(
    source: SourceDocument,
    direction: Point2D,
    *,
    angle_tolerance_radians: float,
) -> tuple[Point2D, Point2D] | None:
    candidates = []
    for entity in _part_entities(source):
        geometry = entity.geometry
        if (
            geometry is None
            or not geometry.closed
            or entity.entity_type not in {"LWPOLYLINE", "POLYLINE"}
            or len(geometry.coordinates) < 3
        ):
            continue
        points = tuple(Point2D(x, y) for x, y in geometry.coordinates)
        area2 = sum(
            left.x * right.y - right.x * left.y
            for left, right in zip(points, (*points[1:], points[0]))
        )
        for index, (start, end) in enumerate(
            zip(points, (*points[1:], points[0]))
        ):
            edge = _unit_vector(end.x - start.x, end.y - start.y)
            if edge is None:
                continue
            alignment = abs(edge.x * direction.x + edge.y * direction.y)
            if alignment < cos(angle_tolerance_radians):
                continue
            transverse = (
                Point2D(-edge.y, edge.x)
                if area2 >= 0.0
                else Point2D(edge.y, -edge.x)
            )
            candidates.append(
                (
                    -start.distance_to(end),
                    entity.source_id,
                    index,
                    edge,
                    transverse,
                )
            )
    if not candidates:
        return None
    _, _, _, longitudinal, transverse = min(candidates)
    return longitudinal, transverse


def _text_basis_hint(
    source: SourceDocument,
    direction: Point2D,
    *,
    angle_tolerance_radians: float,
) -> tuple[Point2D, Point2D] | None:
    weighted: dict[tuple[float, float, float, float], float] = {}
    bases: dict[tuple[float, float, float, float], tuple[Point2D, Point2D]] = {}
    for entity in source.entities:
        if entity.text_rotation is None or entity.text_normal_z is None:
            continue
        angle = entity.text_rotation * pi / 180.0
        longitudinal = Point2D(cos(angle), sin(angle))
        alignment = abs(
            longitudinal.x * direction.x + longitudinal.y * direction.y
        )
        if alignment < cos(angle_tolerance_radians):
            continue
        normal_sign = -1.0 if entity.text_normal_z < 0.0 else 1.0
        transverse = Point2D(
            -normal_sign * longitudinal.y,
            normal_sign * longitudinal.x,
        )
        key = (
            round(longitudinal.x, 9),
            round(longitudinal.y, 9),
            round(transverse.x, 9),
            round(transverse.y, 9),
        )
        bases[key] = (longitudinal, transverse)
        weighted[key] = weighted.get(key, 0.0) + max(entity.text_height or 0.0, 1.0)
    if not weighted:
        return None
    selected = min(weighted, key=lambda key: (-weighted[key], key))
    return bases[selected]


def _fallback_basis_hint(direction: Point2D) -> tuple[Point2D, Point2D]:
    # Production BH drawings are overwhelmingly horizontal. For a vertical
    # defensive candidate, positive global Y is the corresponding stable sign.
    longitudinal = direction
    if (
        longitudinal.x < -1e-12
        or (abs(longitudinal.x) <= 1e-12 and longitudinal.y < 0.0)
    ):
        longitudinal = Point2D(-longitudinal.x, -longitudinal.y)
    return longitudinal, Point2D(-longitudinal.y, longitudinal.x)


def _preferred_basis(
    source: SourceDocument,
    direction: Point2D,
    *,
    angle_tolerance_radians: float,
) -> tuple[Point2D, Point2D]:
    return (
        _explicit_basis_hint(
            source,
            direction,
            angle_tolerance_radians=angle_tolerance_radians,
        )
        or _text_basis_hint(
            source,
            direction,
            angle_tolerance_radians=angle_tolerance_radians,
        )
        or _closed_polyline_basis_hint(
            source,
            direction,
            angle_tolerance_radians=angle_tolerance_radians,
        )
        or _fallback_basis_hint(direction)
    )


def infer_member_frames(
    source: SourceDocument,
    *,
    angle_tolerance_degrees: float = 2.0,
    uniqueness_margin: float = 1e-6,
    horizontal_axis_fact: bool = True,
    horizontal_axis_tolerance_degrees: float = 2.0,
) -> FrameSolveResult:
    points = _all_part_points(source)
    if len(points) < 2:
        raise FrameInferenceError("At least two physical Part points are required.")
    segments = tuple(
        segment
        for entity in _part_entities(source)
        for segment in _entity_segments(entity)
        if segment.length > 1e-9
    )
    if not segments:
        raise FrameInferenceError("No directional Part edge evidence is available.")
    clusters = _cluster_directions(
        segments,
        tolerance_radians=angle_tolerance_degrees * pi / 180.0,
    )
    if horizontal_axis_fact:
        horizontal_tolerance = horizontal_axis_tolerance_degrees * pi / 180.0
        clusters = tuple(
            cluster
            for cluster in clusters
            if _angle_distance(cluster.angle, 0.0) <= horizontal_tolerance
        )
        if not clusters:
            raise FrameInferenceError(
                "No Part edge supports the required horizontal X-axis fact."
            )
    angle_tolerance_radians = angle_tolerance_degrees * pi / 180.0
    total_support = max(sum(item.length for item in segments), 1e-12)
    container_points = _part_points_by_container(source)

    candidates: list[LocalFrame] = []
    for cluster in clusters:
        candidate_angle = 0.0 if horizontal_axis_fact else cluster.angle
        direction = Point2D(cos(candidate_angle), sin(candidate_angle))
        transverse = Point2D(-direction.y, direction.x)
        extent_scores: list[float] = []
        for local_points in container_points:
            longitudinal_values = [
                point.x * direction.x + point.y * direction.y
                for point in local_points
            ]
            transverse_values = [
                point.x * transverse.x + point.y * transverse.y
                for point in local_points
            ]
            longitudinal_span = max(longitudinal_values) - min(longitudinal_values)
            transverse_span = max(transverse_values) - min(transverse_values)
            diagonal = max(hypot(longitudinal_span, transverse_span), 1e-12)
            extent_scores.append(longitudinal_span / diagonal)
        extent_quality = max(extent_scores, default=0.0)
        score = extent_quality + 0.15 * cluster.support_length / total_support
        variants = _frame_variants(
            source,
            angle=candidate_angle,
            score=score,
            evidence_ids=cluster.evidence_ids,
        )
        preferred_longitudinal, preferred_transverse = _preferred_basis(
            source,
            direction,
            angle_tolerance_radians=angle_tolerance_radians,
        )
        candidates.append(
            min(
                variants,
                key=lambda item: (
                    -(
                        item.longitudinal.x * preferred_longitudinal.x
                        + item.longitudinal.y * preferred_longitudinal.y
                        + item.transverse.x * preferred_transverse.x
                        + item.transverse.y * preferred_transverse.y
                    ),
                    _signature_key(source, item),
                    item.reflected,
                    round(item.longitudinal.x, 12),
                    round(item.longitudinal.y, 12),
                ),
            )
        )
    candidates.sort(
        key=lambda item: (
            -item.score,
            item.canonical_signature,
            item.reflected,
        )
    )
    margin = candidates[0].score - candidates[1].score if len(candidates) > 1 else 1.0
    return FrameSolveResult(
        candidates=tuple(candidates),
        unique=len(candidates) == 1 or margin > uniqueness_margin,
        score_margin=margin,
    )
