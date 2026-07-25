from __future__ import annotations

from math import atan, atan2, ceil, cos, degrees, hypot, sin
from pathlib import Path
from xml.etree import ElementTree as ET

from .bh_trace import TraceShape
from .layered_scene import SceneStyle, StageScene


SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


def _tag(name: str) -> str:
    return f"{{{SVG_NS}}}{name}"


def _fmt(value: float) -> str:
    return format(float(value), ".9g")


def _style_attributes(style: SceneStyle) -> dict[str, str]:
    attributes = {
        "stroke": style.stroke,
        "fill": style.fill,
        "stroke-width": _fmt(style.stroke_width),
        "opacity": _fmt(style.opacity),
        "vector-effect": "non-scaling-stroke",
    }
    if style.dasharray:
        attributes["stroke-dasharray"] = style.dasharray
    return attributes


def _points(coordinates) -> str:
    return " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in coordinates)


def _flatten_bulge_segment(
    start: tuple[float, float], end: tuple[float, float], bulge: float
) -> list[tuple[float, float]]:
    if abs(bulge) <= 1e-12:
        return [start, end]
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    chord = hypot(dx, dy)
    if chord <= 1e-12:
        return [start]
    theta = 4.0 * atan(bulge)
    midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    offset = chord / (2.0 * (sin(theta / 2.0) / cos(theta / 2.0)))
    center = (
        midpoint[0] - dy / chord * offset,
        midpoint[1] + dx / chord * offset,
    )
    start_angle = atan2(start[1] - center[1], start[0] - center[0])
    radius = hypot(start[0] - center[0], start[1] - center[1])
    count = max(4, int(ceil(abs(degrees(theta)) / 5.0)))
    result = [
        (
            center[0] + radius * cos(start_angle + theta * index / count),
            center[1] + radius * sin(start_angle + theta * index / count),
        )
        for index in range(count + 1)
    ]
    result[0] = start
    result[-1] = end
    return result


def _flatten_polyline(shape: TraceShape) -> list[tuple[float, float]]:
    coordinates = list(shape.coordinates)
    if len(coordinates) < 2:
        return coordinates
    segment_count = len(coordinates) if shape.closed else len(coordinates) - 1
    result: list[tuple[float, float]] = []
    for index in range(segment_count):
        start = coordinates[index]
        end = coordinates[(index + 1) % len(coordinates)]
        bulge = float(shape.bulges[index]) if index < len(shape.bulges) else 0.0
        segment = _flatten_bulge_segment(start, end, bulge)
        result.extend(segment if not result else segment[1:])
    return result


def _polygon_path(shape: TraceShape) -> str:
    rings = [shape.coordinates, *shape.properties.get("interiors", ())]
    parts: list[str] = []
    for ring in rings:
        if not ring:
            continue
        parts.append("M " + " L ".join(f"{_fmt(x)} {_fmt(y)}" for x, y in ring) + " Z")
    return " ".join(parts)


def _add_shape(parent: ET.Element, shape: TraceShape, style: SceneStyle) -> None:
    attributes = {"id": shape.shape_id, "data-role": shape.role, **_style_attributes(style)}
    if shape.kind == "line":
        if len(shape.coordinates) == 2:
            (x1, y1), (x2, y2) = shape.coordinates
            attributes.update({"x1": _fmt(x1), "y1": _fmt(y1), "x2": _fmt(x2), "y2": _fmt(y2)})
            ET.SubElement(parent, _tag("line"), attributes)
        else:
            attributes["points"] = _points(shape.coordinates)
            ET.SubElement(parent, _tag("polyline"), attributes)
        return
    if shape.kind == "polygon":
        attributes.update({"d": _polygon_path(shape), "fill-rule": "evenodd"})
        ET.SubElement(parent, _tag("path"), attributes)
        return
    if shape.kind in {"polyline", "arc"}:
        coordinates = _flatten_polyline(shape) if shape.kind == "polyline" else list(shape.coordinates)
        attributes["points"] = _points(coordinates)
        element = ET.SubElement(parent, _tag("polyline"), attributes)
        if shape.closed:
            element.set("points", _points([*coordinates, coordinates[0]] if coordinates else coordinates))
        return
    if shape.kind == "circle":
        if shape.coordinates:
            x, y = shape.coordinates[0]
            attributes.update({"cx": _fmt(x), "cy": _fmt(y), "r": _fmt(shape.properties.get("radius", 0.0))})
            ET.SubElement(parent, _tag("circle"), attributes)
        return
    if shape.kind == "point":
        if shape.coordinates:
            x, y = shape.coordinates[0]
            attributes.update({"cx": _fmt(x), "cy": _fmt(y), "r": "1.5"})
            ET.SubElement(parent, _tag("circle"), attributes)
        return
    if shape.kind == "text":
        if shape.coordinates:
            x, y = shape.coordinates[0]
            attributes.update({"x": _fmt(x), "y": _fmt(y), "font-size": _fmt(shape.properties.get("height", 2.5))})
            node = ET.SubElement(parent, _tag("text"), attributes)
            node.text = str(shape.properties.get("text", ""))
        return
    raise ValueError(f"Unsupported trace shape kind for SVG: {shape.kind}")


def render_scene_svg(scene: StageScene, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    min_x, min_y, max_x, max_y = scene.bounds
    geometry_width = max(max_x - min_x, 1.0)
    geometry_height = max(max_y - min_y, 1.0)
    padding = 0.04 * max(geometry_width, geometry_height)
    text_size = max(0.03 * max(geometry_width, geometry_height), 0.08)
    panel_width = max(0.65 * geometry_width, text_size * 22.0)
    canvas_width = geometry_width + panel_width + padding * 3.0
    canvas_height = max(geometry_height + padding * 2.0, text_size * (12 + len(scene.metrics)))
    root = ET.Element(
        _tag("svg"),
        {
            "viewBox": f"0 0 {_fmt(canvas_width)} {_fmt(canvas_height)}",
            "width": "100%",
            "height": "100%",
            "role": "img",
            "data-scene-id": scene.scene_id,
        },
    )
    ET.SubElement(root, _tag("title")).text = f"{scene.title_zh} — {scene.sample_id}"
    ET.SubElement(root, _tag("desc")).text = scene.summary_zh
    geometry = ET.SubElement(
        root,
        _tag("g"),
        {
            "id": "geometry",
            "transform": (
                f"translate({_fmt(padding - min_x)} {_fmt(padding + max_y)}) scale(1 -1)"
            ),
        },
    )
    for shape in sorted(scene.shapes, key=lambda item: (item.role, item.shape_id)):
        _add_shape(geometry, shape, scene.styles[shape.role])

    panel_x = geometry_width + padding * 2.0
    panel = ET.SubElement(root, _tag("g"), {"id": "explanation"})
    ET.SubElement(
        panel,
        _tag("rect"),
        {
            "x": _fmt(panel_x - padding * 0.5),
            "y": _fmt(padding * 0.5),
            "width": _fmt(panel_width + padding),
            "height": _fmt(canvas_height - padding),
            "rx": _fmt(text_size * 0.35),
            "fill": "#f8fafc",
            "stroke": "#cbd5e1",
            "stroke-width": _fmt(max(text_size * 0.04, 0.01)),
        },
    )
    lines = [
        scene.warning,
        f"样本: {scene.sample_id}",
        f"阶段: {scene.stage_id}",
        f"产物: {scene.artifact_id}",
        f"候选: {scene.hypothesis_id or '-'}",
        f"状态: {scene.status}",
        scene.title_zh,
        scene.summary_zh,
        *(f"{key}: {value}" for key, value in scene.metrics),
    ]
    y = padding + text_size
    for index, line in enumerate(lines):
        node = ET.SubElement(
            panel,
            _tag("text"),
            {
                "x": _fmt(panel_x),
                "y": _fmt(y + index * text_size * 1.45),
                "font-size": _fmt(text_size),
                "font-family": "sans-serif",
                "fill": "#0f172a" if index else "#b45309",
            },
        )
        node.text = line

    legend_y = y + len(lines) * text_size * 1.45 + text_size
    used_roles = sorted({shape.role for shape in scene.shapes})
    for index, role in enumerate(used_roles):
        style = scene.styles[role]
        row_y = legend_y + index * text_size * 1.35
        ET.SubElement(
            panel,
            _tag("line"),
            {
                "x1": _fmt(panel_x),
                "y1": _fmt(row_y),
                "x2": _fmt(panel_x + text_size * 2.0),
                "y2": _fmt(row_y),
                **_style_attributes(style),
            },
        )
        label = ET.SubElement(
            panel,
            _tag("text"),
            {
                "x": _fmt(panel_x + text_size * 2.6),
                "y": _fmt(row_y + text_size * 0.3),
                "font-size": _fmt(text_size * 0.9),
                "font-family": "sans-serif",
                "fill": "#334155",
            },
        )
        label.text = role

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path
