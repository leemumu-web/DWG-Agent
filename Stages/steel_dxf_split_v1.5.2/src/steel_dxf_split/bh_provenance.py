from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite, radians, tan

from .bh_frames import LocalFrame
from .bh_manufacturing_ir import EvidenceState, FeatureEvidence
from .bh_models import BHPlate, BulgeContour
from .bh_proofs import ProofReport
from .bh_regions import NormalizedEntity, build_view_regions
from .bh_source import SourceDocument


@dataclass(frozen=True, slots=True)
class _Curve:
    start: tuple[float, float]
    end: tuple[float, float]
    bulge: float
    source_id: str


@dataclass(frozen=True, slots=True)
class _Segment:
    start: tuple[float, float]
    end: tuple[float, float]
    bulge: float


@dataclass(frozen=True, slots=True)
class PlateFeatureEvidenceBundle:
    outer: tuple[FeatureEvidence, ...]
    inner: tuple[tuple[FeatureEvidence, ...], ...]
    inner_contours: tuple[FeatureEvidence, ...]
    cuts: tuple[FeatureEvidence, ...]


def _contour_segments(contour: BulgeContour) -> tuple[_Segment, ...]:
    return tuple(
        _Segment(
            (vertex.x, vertex.y),
            (
                contour.vertices[(index + 1) % len(contour.vertices)].x,
                contour.vertices[(index + 1) % len(contour.vertices)].y,
            ),
            vertex.bulge,
        )
        for index, vertex in enumerate(contour.vertices)
    )


def _arc_bulge(entity: NormalizedEntity) -> float:
    geometry = entity.geometry
    if geometry is None:
        return 0.0
    sweep = ((geometry.end_angle or 0.0) - (geometry.start_angle or 0.0)) % 360.0
    return tan(radians(sweep) / 4.0)


def _scaled_point(
    point: tuple[float, float],
    factor: float,
) -> tuple[float, float]:
    return point[0] * factor, point[1] * factor


def _entity_curves(
    entity: NormalizedEntity,
    *,
    factor: float = 1.0,
) -> tuple[_Curve, ...]:
    geometry = entity.geometry
    if geometry is None:
        return ()
    if entity.entity_type == "LINE" and len(geometry.coordinates) == 2:
        return (
            _Curve(
                _scaled_point(geometry.coordinates[0], factor),
                _scaled_point(geometry.coordinates[1], factor),
                0.0,
                entity.source_id,
            ),
        )
    if entity.entity_type == "ARC" and len(geometry.coordinates) >= 2:
        return (
            _Curve(
                _scaled_point(geometry.coordinates[0], factor),
                _scaled_point(geometry.coordinates[1], factor),
                _arc_bulge(entity),
                entity.source_id,
            ),
        )
    if entity.entity_type not in {"LWPOLYLINE", "POLYLINE"}:
        return ()
    count = len(geometry.coordinates)
    edge_count = count if geometry.closed else max(0, count - 1)
    return tuple(
        _Curve(
            _scaled_point(geometry.coordinates[index], factor),
            _scaled_point(
                geometry.coordinates[(index + 1) % count],
                factor,
            ),
            geometry.bulges[index] if index < len(geometry.bulges) else 0.0,
            entity.source_id,
        )
        for index in range(edge_count)
        if geometry.coordinates[index] != geometry.coordinates[(index + 1) % count]
    )


def _vector(start: tuple[float, float], end: tuple[float, float]) -> tuple[float, float]:
    return end[0] - start[0], end[1] - start[1]


def _distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return hypot(left[0] - right[0], left[1] - right[1])


def _compatible_offsets(
    segment: _Segment,
    curve: _Curve,
    *,
    tolerance: float,
) -> tuple[tuple[float, float], ...]:
    output_vector = _vector(segment.start, segment.end)
    source_vector = _vector(curve.start, curve.end)
    result = []
    if (
        _distance(output_vector, source_vector) <= tolerance
        and abs(segment.bulge - curve.bulge) <= 1e-4
    ):
        result.append(
            (
                curve.start[0] - segment.start[0],
                curve.start[1] - segment.start[1],
            )
        )
    reversed_vector = (-source_vector[0], -source_vector[1])
    if (
        _distance(output_vector, reversed_vector) <= tolerance
        and abs(segment.bulge + curve.bulge) <= 1e-4
    ):
        result.append(
            (
                curve.end[0] - segment.start[0],
                curve.end[1] - segment.start[1],
            )
        )
    return tuple(result)


def _curve_residual(
    segment: _Segment,
    curve: _Curve,
    offset: tuple[float, float],
) -> float | None:
    translated_start = (
        segment.start[0] + offset[0],
        segment.start[1] + offset[1],
    )
    translated_end = (
        segment.end[0] + offset[0],
        segment.end[1] + offset[1],
    )
    direct = max(
        _distance(translated_start, curve.start),
        _distance(translated_end, curve.end),
        abs(segment.bulge - curve.bulge),
    )
    reverse = max(
        _distance(translated_start, curve.end),
        _distance(translated_end, curve.start),
        abs(segment.bulge + curve.bulge),
    )
    return min(direct, reverse)


def _registration_offset(
    segments: tuple[_Segment, ...],
    curves: tuple[_Curve, ...],
    *,
    tolerance: float,
) -> tuple[float, float] | None:
    candidates = {
        offset
        for segment in segments
        for curve in curves
        for offset in _compatible_offsets(segment, curve, tolerance=tolerance)
    }
    if not candidates:
        return None
    scored = []
    grid = max(tolerance, 1e-6)
    grouped: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for offset in candidates:
        key = (round(offset[0] / grid), round(offset[1] / grid))
        grouped.setdefault(key, []).append(offset)
    for key, offsets in grouped.items():
        offset = min(offsets)
        residuals = [
            min(
                (
                    residual
                    for curve in curves
                    if (residual := _curve_residual(segment, curve, offset))
                    <= tolerance
                ),
                default=None,
            )
            for segment in segments
        ]
        matched = [value for value in residuals if value is not None]
        scored.append((-len(matched), sum(matched), key, offset))
    return min(scored)[3]


def _proof_id(kind: str, assembly_index: int) -> str:
    return f"BH.PROOF.PLATE.{assembly_index:02d}.{kind}"


def _recorded_source_offset(plate: BHPlate) -> tuple[float, float] | None:
    """Return the exact source-minus-output translation recorded at lowering."""

    value = plate.provenance.get("normalization_translation_mm")
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        output_dx, output_dy = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    if not isfinite(output_dx) or not isfinite(output_dy):
        return None
    # ``make_plate`` records output = source + translation.  Evidence matching
    # uses source = output + offset, hence the sign reversal.
    return -output_dx, -output_dy


def _inferred_rule(plate: BHPlate) -> str:
    mode = str((plate.provenance.get("selection") or {}).get("mode") or "")
    if plate.role.value == "web":
        return f"BH.LOWERING.WEB.{mode or 'POLYGONIZE'}"
    return "BH.LOWERING.FLANGE.PROJECTION_POLYGONIZE"


def _segment_evidence(
    segment: _Segment,
    curves: tuple[_Curve, ...],
    offset: tuple[float, float] | None,
    *,
    plate: BHPlate,
    proof_id: str,
    proof_available: bool,
    tolerance: float,
) -> FeatureEvidence:
    matches = []
    if offset is not None:
        for curve in curves:
            residual = _curve_residual(segment, curve, offset)
            if residual is not None and residual <= tolerance:
                matches.append((residual, curve.source_id))
    if matches:
        minimum = min(item[0] for item in matches)
        source_ids = tuple(
            sorted(item[1] for item in matches if item[0] <= minimum + 1e-9)
        )
        if not proof_available:
            return FeatureEvidence(
                EvidenceState.MISSING,
                source_ids,
                (),
                (proof_id,),
                residual_mm=minimum,
                description="Source geometry matches, but its contour proof was not emitted.",
            )
        return FeatureEvidence(
            EvidenceState.DIRECT,
            source_ids,
            (),
            (proof_id,),
            residual_mm=minimum,
            description="Output contour segment matches canonical source Part geometry.",
        )
    source_ids = tuple(sorted({curve.source_id for curve in curves}))
    if source_ids and proof_available:
        return FeatureEvidence(
            EvidenceState.INFERRED,
            source_ids,
            (_inferred_rule(plate),),
            (proof_id,),
            description="Segment is derived by a recorded topology/lowering rule.",
        )
    return FeatureEvidence(
        EvidenceState.MISSING,
        (),
        (),
        (proof_id,),
        description="No source Part geometry is available for this segment.",
    )


def _aggregate_evidence(
    items: tuple[FeatureEvidence, ...],
    *,
    proof_id: str,
) -> FeatureEvidence:
    states = {item.state for item in items}
    state = (
        EvidenceState.CONFLICT
        if EvidenceState.CONFLICT in states
        else EvidenceState.MISSING
        if EvidenceState.MISSING in states
        else EvidenceState.INFERRED
        if EvidenceState.INFERRED in states
        else EvidenceState.DIRECT
    )
    return FeatureEvidence(
        state,
        tuple(sorted({source for item in items for source in item.source_ids})),
        tuple(sorted({rule for item in items for rule in item.rule_ids})),
        (proof_id,),
        residual_mm=max(
            (item.residual_mm for item in items if item.residual_mm is not None),
            default=None,
        ),
        description="Inner contour evidence is aggregated from its constituent segments.",
    )


def _selected_region_entities(
    plate: BHPlate,
    source: SourceDocument,
    frame: LocalFrame,
) -> tuple[tuple[NormalizedEntity, ...], tuple[NormalizedEntity, ...]]:
    if not source.entities:
        return (), ()
    regions = build_view_regions(source, frame)
    source_region_id = str(plate.provenance.get("source_region_id") or "")
    part_entities: tuple[NormalizedEntity, ...] = ()
    if source_region_id:
        region = next(
            (
                item
                for item in regions.part_views
                if item.region_id == source_region_id
            ),
            None,
        )
        if region is not None:
            part_entities = region.entities
    opening_rows = plate.provenance.get("inner_contour_source_ids", [])
    opening_source_ids = {
        str(source_id)
        for row in opening_rows
        if isinstance(row, (list, tuple))
        for source_id in row
    }
    if opening_source_ids:
        opening_entities = tuple(
            entity
            for entity in regions.normalized_entities
            if entity.source_id in opening_source_ids
        )
        part_entities = tuple(
            {entity.source_id: entity for entity in (*part_entities, *opening_entities)}.values()
        )
    cuts = tuple(
        entity
        for entity in regions.normalized_entities
        if entity.semantic_role.value == "physical_cut"
        and entity.entity_type == "CIRCLE"
    )
    return part_entities, cuts


def build_plate_feature_evidence(
    plate: BHPlate,
    *,
    assembly_index: int,
    source: SourceDocument,
    frame: LocalFrame,
    proof_report: ProofReport,
    tolerance_mm: float,
) -> PlateFeatureEvidenceBundle:
    passed_proofs = {
        item.obligation_id
        for item in proof_report.obligations
        if item.status.value == "pass"
    }
    part_entities, circle_entities = _selected_region_entities(plate, source, frame)
    source_metric_scale_factor = float(
        plate.provenance.get("source_metric_scale_factor", 1.0)
    )
    opening_source_ids = {
        str(source_id)
        for row in plate.provenance.get("inner_contour_source_ids", [])
        if isinstance(row, (list, tuple))
        for source_id in row
    }
    curves = tuple(
        curve
        for entity in part_entities
        if entity.semantic_role.value == "part_edge"
        or entity.source_id in opening_source_ids
        for curve in _entity_curves(
            entity,
            factor=source_metric_scale_factor,
        )
    )
    outer_segments = _contour_segments(plate.contour)
    offset = _registration_offset(outer_segments, curves, tolerance=tolerance_mm)
    contour_proof = _proof_id("CONTOUR", assembly_index)
    contour_proof_available = contour_proof in passed_proofs
    outer = tuple(
        _segment_evidence(
            segment,
            curves,
            offset,
            plate=plate,
            proof_id=contour_proof,
            proof_available=contour_proof_available,
            tolerance=tolerance_mm,
        )
        for segment in outer_segments
    )
    inner = tuple(
        tuple(
            _segment_evidence(
                segment,
                curves,
                offset,
                plate=plate,
                proof_id=contour_proof,
                proof_available=contour_proof_available,
                tolerance=tolerance_mm,
            )
            for segment in _contour_segments(contour)
        )
        for contour in plate.inner_contours
    )
    inner_contours = tuple(
        _aggregate_evidence(items, proof_id=contour_proof) for items in inner
    )
    cut_proof = _proof_id("CUTS", assembly_index)
    cut_proof_available = cut_proof in passed_proofs
    cuts = []
    cut_offset = _recorded_source_offset(plate) or offset
    source_cut_rows = plate.provenance.get("circular_cut_source_ids", [])
    circles_by_source = {entity.source_id: entity for entity in circle_entities}
    for cut_index, cut in enumerate(plate.circular_cuts):
        matches = []
        owned_source_ids = tuple(
            str(value)
            for value in (
                source_cut_rows[cut_index]
                if isinstance(source_cut_rows, list)
                and cut_index < len(source_cut_rows)
                else []
            )
        )
        candidates = (
            tuple(
                circles_by_source[source_id]
                for source_id in owned_source_ids
                if source_id in circles_by_source
            )
            if owned_source_ids
            else circle_entities
        )
        if cut_offset is not None:
            for entity in candidates:
                geometry = entity.geometry
                if (
                    geometry is None
                    or geometry.center is None
                    or geometry.radius is None
                ):
                    continue
                local_center = (
                    geometry.center[0] * source_metric_scale_factor
                    - cut_offset[0],
                    geometry.center[1] * source_metric_scale_factor
                    - cut_offset[1],
                )
                residual = max(
                    _distance(local_center, (cut.center.x, cut.center.y)),
                    abs(
                        geometry.radius * source_metric_scale_factor
                        - cut.radius
                    ),
                )
                if residual <= tolerance_mm:
                    matches.append((residual, entity.source_id))
        if matches:
            minimum = min(item[0] for item in matches)
            source_ids = tuple(
                sorted(
                    item[1]
                    for item in matches
                    if item[0] <= minimum + 1e-9
                )
            )
            cuts.append(
                FeatureEvidence(
                    (
                        EvidenceState.DIRECT
                        if cut_proof_available
                        else EvidenceState.MISSING
                    ),
                    source_ids,
                    (),
                    (cut_proof,),
                    residual_mm=minimum,
                    description=(
                        "Circular cut center and radius match a source Bolt/CIRCLE."
                        if cut_proof_available
                        else "Source circle matches, but its cut-containment proof was not emitted."
                    ),
                )
            )
        else:
            cuts.append(
                FeatureEvidence(
                    EvidenceState.MISSING,
                    owned_source_ids,
                    (),
                    (cut_proof,),
                    description="No source physical circle matches this manufacturing cut.",
                )
            )
    return PlateFeatureEvidenceBundle(
        outer=outer,
        inner=inner,
        inner_contours=inner_contours,
        cuts=tuple(cuts),
    )
