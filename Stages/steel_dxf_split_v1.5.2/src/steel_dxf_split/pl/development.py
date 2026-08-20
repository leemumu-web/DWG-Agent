from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from math import isfinite

from ezdxf.entities import DXFEntity
from ezdxf.math import Matrix44
from ezdxf.transform import copies

from .contracts import DevelopmentMetrics, PLSplitError

K_FACTOR = 0.5
_TENTH_MM = Decimal("0.1")
_FLOAT_NOISE_MM = Decimal("0.000001")


def _positive_finite(value: float, name: str) -> float:
    resolved = float(value)
    if not isfinite(resolved) or resolved <= 0.0:
        raise PLSplitError("INVALID_LENGTH", f"{name}必须是正的有限毫米值。")
    return resolved


def neutral_axis_length(surface_lengths_mm: tuple[float, float]) -> float:
    first = _positive_finite(surface_lengths_mm[0], "第一板面长度")
    second = _positive_finite(surface_lengths_mm[1], "第二板面长度")
    return (first + second) / 2.0


def ceil_tenth_mm(value_mm: float | Decimal) -> Decimal:
    value = value_mm if isinstance(value_mm, Decimal) else Decimal(str(value_mm))
    if not value.is_finite() or value <= 0:
        raise PLSplitError("INVALID_LENGTH", "展开长度必须是正的有限毫米值。")
    lower_tenth = value.quantize(_TENTH_MM, rounding=ROUND_FLOOR)
    if value - lower_tenth <= _FLOAT_NOISE_MM:
        value = lower_tenth
    return value.quantize(_TENTH_MM, rounding=ROUND_CEILING)


def calculate_development(
    *,
    projection_length_mm: float,
    surface_lengths_mm: tuple[float, float],
    bom_length_mm: float,
    anchor_x_mm: float,
) -> DevelopmentMetrics:
    projection = _positive_finite(projection_length_mm, "主视图投影长度")
    bom = _positive_finite(bom_length_mm, "材料表长度")
    anchor = float(anchor_x_mm)
    if not isfinite(anchor):
        raise PLSplitError("INVALID_ANCHOR", "主视图左端锚点必须是有限坐标。")
    k_length = neutral_axis_length(surface_lengths_mm)
    raw = max(projection, k_length, bom)
    target = float(ceil_tenth_mm(raw))
    return DevelopmentMetrics(
        projection_length_mm=projection,
        surface_lengths_mm=tuple(sorted((float(surface_lengths_mm[0]), float(surface_lengths_mm[1])))),
        k_factor=K_FACTOR,
        k_length_mm=k_length,
        bom_length_mm=bom,
        raw_length_mm=raw,
        target_length_mm=target,
        scale_x=target / projection,
        anchor_x_mm=anchor,
    )


def transform_outline(
    entities: Sequence[DXFEntity],
    *,
    projection_length_mm: float,
    surface_lengths_mm: tuple[float, float],
    bom_length_mm: float,
    anchor_x_mm: float,
) -> tuple[tuple[DXFEntity, ...], DevelopmentMetrics]:
    source = tuple(entities)
    if not source:
        raise PLSplitError("EMPTY_OUTLINE", "主视图外轮廓不能为空。")
    metrics = calculate_development(
        projection_length_mm=projection_length_mm,
        surface_lengths_mm=surface_lengths_mm,
        bom_length_mm=bom_length_mm,
        anchor_x_mm=anchor_x_mm,
    )
    matrix = Matrix44.chain(
        Matrix44.translate(-metrics.anchor_x_mm, 0.0, 0.0),
        Matrix44.scale(metrics.scale_x, 1.0, 1.0),
        Matrix44.translate(metrics.anchor_x_mm, 0.0, 0.0),
    )
    log, transformed = copies(source, matrix)
    if len(log) or len(transformed) != len(source):
        messages = "; ".join(log.messages())
        raise PLSplitError(
            "TRANSFORM_FAILED",
            f"主视图原生实体无法完整拉伸。{messages}",
        )
    return tuple(transformed), metrics
