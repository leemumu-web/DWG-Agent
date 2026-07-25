from __future__ import annotations

from dataclasses import dataclass
import json
from math import isfinite
from types import MappingProxyType
from typing import Any, Mapping

from .bh_trace import TraceEvent, TraceShape


INTERMEDIATE_WARNING = "INTERMEDIATE EVIDENCE / 非生产下料"


@dataclass(frozen=True, slots=True)
class SceneStyle:
    layer_name: str
    stroke: str
    fill: str
    stroke_width: float
    dasharray: str | None = None
    opacity: float = 1.0
    dxf_color: int = 7
    dxf_linetype: str = "CONTINUOUS"


@dataclass(frozen=True, slots=True)
class SceneText:
    text: str
    x: float
    y: float
    role: str = "annotation"


ROLE_STYLES: Mapping[str, SceneStyle] = MappingProxyType(
    {
        "part_visible": SceneStyle("PART_VISIBLE", "#334155", "none", 0.8, dxf_color=7),
        "part_hidden": SceneStyle("PART_HIDDEN", "#94a3b8", "none", 0.7, "5 3", 0.9, 8, "DASHED"),
        "physical_cut": SceneStyle("PHYSICAL_CUT", "#dc2626", "none", 0.9, dxf_color=1),
        "cut_helper": SceneStyle("CUT_HELPER", "#f59e0b", "none", 0.6, "3 3", 0.8, 30, "DASHED"),
        "face_candidate": SceneStyle("FACE_CANDIDATE", "#2563eb", "#93c5fd", 0.7, opacity=0.35, dxf_color=5),
        "face_selected": SceneStyle("FACE_SELECTED", "#059669", "#6ee7b7", 1.0, opacity=0.42, dxf_color=3),
        "repair_added": SceneStyle("REPAIR_ADDED", "#a21caf", "#f0abfc", 1.0, opacity=0.42, dxf_color=6),
        "repair_removed": SceneStyle("REPAIR_REMOVED", "#e11d48", "none", 0.9, "4 2", 0.9, 1, "DASHED"),
        "manufacturing_plate": SceneStyle("MANUFACTURING_PLATE", "#0f172a", "#cbd5e1", 1.2, opacity=0.5, dxf_color=7),
        "manufacturing_cut": SceneStyle("MANUFACTURING_CUT", "#b91c1c", "none", 1.1, dxf_color=1),
        "manual_reference": SceneStyle("MANUAL_REFERENCE", "#2563eb", "none", 1.0, "5 2", 0.95, 5, "DASHED"),
        "generated_result": SceneStyle("GENERATED_RESULT", "#16a34a", "none", 1.1, dxf_color=3),
        "pass": SceneStyle("PASS", "#15803d", "#bbf7d0", 0.8, opacity=0.45, dxf_color=3),
        "warning": SceneStyle("WARNING", "#d97706", "#fde68a", 0.9, opacity=0.55, dxf_color=30),
        "failed": SceneStyle("FAILED", "#b91c1c", "#fecaca", 1.0, opacity=0.55, dxf_color=1),
        "annotation": SceneStyle("ANNOTATION", "#111827", "none", 0.5, dxf_color=7),
    }
)


@dataclass(frozen=True, slots=True)
class StageScene:
    scene_id: str
    sequence: int
    sample_id: str
    stage_id: str
    artifact_id: str
    hypothesis_id: str | None
    status: str
    title_zh: str
    summary_zh: str
    shapes: tuple[TraceShape, ...]
    texts: tuple[SceneText, ...]
    metrics: tuple[tuple[str, str], ...]
    bounds: tuple[float, float, float, float]
    warning: str
    styles: Mapping[str, SceneStyle]


def _format_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, float):
        return format(value, ".12g")
    return str(value)


def _flatten_metrics(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    if isinstance(value, Mapping):
        result: list[tuple[str, str]] = []
        for key in sorted(value):
            child = f"{prefix}.{key}" if prefix else str(key)
            result.extend(_flatten_metrics(value[key], child))
        return result
    if isinstance(value, (tuple, list)):
        if all(item is None or isinstance(item, (str, int, float, bool)) for item in value):
            return [(prefix, json.dumps(list(value), ensure_ascii=False, separators=(",", ":")))]
        return []
    if value is None or isinstance(value, (str, int, float, bool)):
        return [(prefix, _format_scalar(value))]
    return []


def _shape_bounds(shape: TraceShape) -> tuple[float, float, float, float] | None:
    if shape.kind == "circle" and shape.coordinates:
        radius = float(shape.properties.get("radius", 0.0))
        x, y = shape.coordinates[0]
        return (x - radius, y - radius, x + radius, y + radius)
    if shape.kind == "arc" and "center" in shape.properties and "radius" in shape.properties:
        # Trace arcs already carry sampled coordinates. Include the source circle
        # envelope as a conservative guard against a sampled extrema miss.
        center = shape.properties["center"]
        radius = float(shape.properties["radius"])
        if isinstance(center, (tuple, list)) and len(center) == 2:
            cx, cy = float(center[0]), float(center[1])
            sampled = list(shape.coordinates)
            if sampled:
                xs = [point[0] for point in sampled]
                ys = [point[1] for point in sampled]
                return (
                    min(min(xs), cx - radius),
                    min(min(ys), cy - radius),
                    max(max(xs), cx + radius),
                    max(max(ys), cy + radius),
                )
    if not shape.coordinates:
        return None
    xs = [float(point[0]) for point in shape.coordinates]
    ys = [float(point[1]) for point in shape.coordinates]
    return min(xs), min(ys), max(xs), max(ys)


def _scene_bounds(shapes: tuple[TraceShape, ...]) -> tuple[float, float, float, float]:
    bounds = [item for shape in shapes if (item := _shape_bounds(shape)) is not None]
    if not bounds:
        return (0.0, 0.0, 1.0, 1.0)
    result = (
        min(item[0] for item in bounds),
        min(item[1] for item in bounds),
        max(item[2] for item in bounds),
        max(item[3] for item in bounds),
    )
    if not all(isfinite(value) for value in result):
        raise ValueError("Scene bounds contain a non-finite coordinate")
    min_x, min_y, max_x, max_y = result
    if min_x == max_x:
        min_x -= 0.5
        max_x += 0.5
    if min_y == max_y:
        min_y -= 0.5
        max_y += 0.5
    return min_x, min_y, max_x, max_y


def scene_from_event(event: TraceEvent) -> StageScene:
    shapes = tuple(event.shapes)
    unknown_roles = sorted({shape.role for shape in shapes if shape.role not in ROLE_STYLES})
    if unknown_roles:
        raise ValueError(f"Unknown scene roles: {', '.join(unknown_roles)}")
    metrics = tuple(_flatten_metrics(event.payload))
    return StageScene(
        scene_id=f"{event.sequence:04d}-{event.artifact_id}",
        sequence=event.sequence,
        sample_id=event.sample_id,
        stage_id=event.stage_id,
        artifact_id=event.artifact_id,
        hypothesis_id=event.hypothesis_id,
        status=event.status,
        title_zh=event.title_zh,
        summary_zh=event.summary_zh,
        shapes=shapes,
        texts=(),
        metrics=metrics,
        bounds=_scene_bounds(shapes),
        warning=INTERMEDIATE_WARNING,
        styles=ROLE_STYLES,
    )
