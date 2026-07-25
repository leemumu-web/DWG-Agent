from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import permutations, product

from .metadata import BoxMetadata
from .source_ir import ObjectGroupIR, SourceDocumentIR, SourceEntityIR
from .view_frame import PartViewIR, ViewFrame, build_part_views


DIMENSION_RELATIVE_TOLERANCE = 0.005


@dataclass(frozen=True, slots=True)
class PreprocessedBoxViews:
    """BOX source and Part views after evidence-gated geometric normalization."""

    source: SourceDocumentIR
    views: tuple[PartViewIR, ...]
    geometry_scale: float
    diagnostics: tuple[str, ...]


def _relative_error(actual: float, expected: float) -> float:
    if expected <= 0.0:
        raise ValueError("expected BOX dimension must be positive")
    return abs(actual - expected) / expected


def _orientation_dimensions(view: PartViewIR) -> tuple[tuple[float, float], ...]:
    frame = view.frame
    return (
        (frame.longitudinal_span, frame.transverse_span),
        (frame.transverse_span, frame.longitudinal_span),
    )


def _supports_geometry_scale(
    views: tuple[PartViewIR, ...],
    metadata: BoxMetadata,
    factor: float,
) -> bool:
    if factor <= 0.0:
        return False
    nominal_length = metadata.nominal_length.value
    height = metadata.profile.value.height
    width = metadata.profile.value.width
    for h_view, b_view in permutations(views, 2):
        for h_dimensions, b_dimensions in product(
            _orientation_dimensions(h_view),
            _orientation_dimensions(b_view),
        ):
            actual = (
                h_dimensions[0] * factor,
                h_dimensions[1] * factor,
                b_dimensions[0] * factor,
                b_dimensions[1] * factor,
            )
            expected = (nominal_length, height, nominal_length, width)
            if all(
                _relative_error(value, target) <= DIMENSION_RELATIVE_TOLERANCE
                for value, target in zip(actual, expected, strict=True)
            ):
                return True
    return False


def _infer_geometry_scale(
    views: tuple[PartViewIR, ...],
    metadata: BoxMetadata,
) -> float:
    if _supports_geometry_scale(views, metadata, 1.0):
        return 1.0
    expected_factor = metadata.scale_denominator.value / 10.0
    if (
        abs(expected_factor - 1.0) > 1e-12
        and _supports_geometry_scale(
            views,
            metadata,
            expected_factor,
        )
    ):
        return expected_factor
    return 1.0


def _scale_point2(
    point: tuple[float, float] | None,
    factor: float,
) -> tuple[float, float] | None:
    if point is None:
        return None
    return (point[0] * factor, point[1] * factor)


def _scale_entity_points(
    entity: SourceEntityIR,
    factor: float,
) -> tuple[tuple[float, float, float], ...]:
    if entity.kind == "LWPOLYLINE":
        return tuple(
            (point[0] * factor, point[1] * factor, point[2])
            for point in entity.points
        )
    return tuple(
        (point[0] * factor, point[1] * factor, point[2] * factor)
        for point in entity.points
    )


def _scale_entity(entity: SourceEntityIR, factor: float) -> SourceEntityIR:
    return replace(
        entity,
        start=_scale_point2(entity.start, factor),
        end=_scale_point2(entity.end, factor),
        center=_scale_point2(entity.center, factor),
        radius=(
            entity.radius * factor
            if entity.radius is not None
            else None
        ),
        points=_scale_entity_points(entity, factor),
        major_axis=_scale_point2(entity.major_axis, factor),
    )


def _scale_group(group: ObjectGroupIR, factor: float) -> ObjectGroupIR:
    insert_point = _scale_point2(group.insert_point, factor)
    assert insert_point is not None
    return replace(group, insert_point=insert_point)


def _scale_source_geometry(
    source: SourceDocumentIR,
    factor: float,
) -> SourceDocumentIR:
    return replace(
        source,
        groups=tuple(_scale_group(group, factor) for group in source.groups),
        entities=tuple(_scale_entity(entity, factor) for entity in source.entities),
    )


def preprocess_box_views(
    source: SourceDocumentIR,
    metadata: BoxMetadata,
) -> PreprocessedBoxViews:
    """Normalize only a uniformly scaled BOX drawing proven by all dimensions."""

    source_views = build_part_views(
        source,
        nominal_length_mm=metadata.nominal_length.value,
    )
    geometry_scale = _infer_geometry_scale(source_views, metadata)
    normalized_source = (
        source
        if geometry_scale == 1.0
        else _scale_source_geometry(source, geometry_scale)
    )
    diagnostics = (
        ()
        if geometry_scale == 1.0
        else (f"BOX.VIEW.GEOMETRY_SCALE_NORMALIZED:{geometry_scale:g}",)
    )
    return PreprocessedBoxViews(
        source=normalized_source,
        views=(
            source_views
            if geometry_scale == 1.0
            else build_part_views(
                normalized_source,
                nominal_length_mm=metadata.nominal_length.value,
            )
        ),
        geometry_scale=geometry_scale,
        diagnostics=diagnostics,
    )


def _scaled_bounds(
    minimum: float,
    maximum: float,
    factor: float,
) -> tuple[float, float]:
    values = (minimum * factor, maximum * factor)
    return (min(values), max(values))


def _swap_view_frame(frame: ViewFrame) -> ViewFrame:
    longitudinal_axis = frame.transverse_axis
    if longitudinal_axis[0] < -1e-12 or (
        abs(longitudinal_axis[0]) <= 1e-12
        and longitudinal_axis[1] < 0.0
    ):
        longitudinal_axis = (-longitudinal_axis[0], -longitudinal_axis[1])
        direction = -1.0
    else:
        direction = 1.0
    transverse_axis = (-longitudinal_axis[1], longitudinal_axis[0])
    longitudinal_min, longitudinal_max = _scaled_bounds(
        frame.transverse_min,
        frame.transverse_max,
        direction,
    )
    transverse_min, transverse_max = _scaled_bounds(
        frame.longitudinal_min,
        frame.longitudinal_max,
        -direction,
    )
    return ViewFrame(
        origin=frame.origin,
        longitudinal_axis=longitudinal_axis,
        transverse_axis=transverse_axis,
        longitudinal_min=longitudinal_min,
        longitudinal_max=longitudinal_max,
        transverse_min=transverse_min,
        transverse_max=transverse_max,
    )


def enumerate_role_view_variants(
    view: PartViewIR,
    *,
    nominal_length_mm: float,
    transverse_mm: float,
) -> tuple[PartViewIR, ...]:
    """Retain the source frame and add a proven orthogonal role candidate."""

    swapped_frame = _swap_view_frame(view.frame)
    if (
        _relative_error(
            swapped_frame.longitudinal_span,
            nominal_length_mm,
        )
        > DIMENSION_RELATIVE_TOLERANCE
        or _relative_error(
            swapped_frame.transverse_span,
            transverse_mm,
        )
        > DIMENSION_RELATIVE_TOLERANCE
    ):
        return (view,)
    return (view, replace(view, frame=swapped_frame))
