from __future__ import annotations

from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import ezdxf

from .bh_trace import TraceShape
from .layered_scene import StageScene


def _guid(scene: StageScene, suffix: str) -> str:
    value = uuid5(NAMESPACE_URL, f"steel-dxf-split:{scene.sample_id}:{scene.scene_id}:{suffix}")
    return "{" + str(value).upper() + "}"


def _replace_header_guid(path: Path, variable: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    for index in range(0, len(lines) - 3, 2):
        if lines[index].strip() == "9" and lines[index + 1].strip() == variable:
            if lines[index + 2].strip() != "2":
                raise ValueError(f"Unexpected DXF group code for {variable}")
            lines[index + 3] = value
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return
    raise ValueError(f"DXF header variable {variable} was not found")


def _ensure_layers(doc: ezdxf.document.Drawing, scene: StageScene) -> None:
    for style in scene.styles.values():
        if style.layer_name in doc.layers:
            continue
        linetype = style.dxf_linetype if style.dxf_linetype in doc.linetypes else "CONTINUOUS"
        doc.layers.add(style.layer_name, color=style.dxf_color, linetype=linetype)
    if "STAGE_META" not in doc.layers:
        doc.layers.add("STAGE_META", color=7)


def _add_polyline(
    msp,
    shape: TraceShape,
    *,
    layer: str,
    coordinates: tuple[tuple[float, float], ...] | None = None,
    closed: bool | None = None,
) -> None:
    points = tuple(coordinates if coordinates is not None else shape.coordinates)
    if not points:
        return
    bulges = tuple(shape.bulges)
    vertices = [
        (float(x), float(y), 0.0, 0.0, float(bulges[index]) if index < len(bulges) else 0.0)
        for index, (x, y) in enumerate(points)
    ]
    msp.add_lwpolyline(
        vertices,
        format="xyseb",
        close=shape.closed if closed is None else closed,
        dxfattribs={"layer": layer},
    )


def _add_shape(msp, shape: TraceShape, layer: str) -> None:
    if shape.kind == "line":
        if len(shape.coordinates) == 2:
            msp.add_line(shape.coordinates[0], shape.coordinates[1], dxfattribs={"layer": layer})
        else:
            _add_polyline(msp, shape, layer=layer, closed=False)
        return
    if shape.kind in {"polyline", "polygon"}:
        _add_polyline(msp, shape, layer=layer)
        if shape.kind == "polygon":
            for ring in shape.properties.get("interiors", ()):
                coordinates = tuple((float(point[0]), float(point[1])) for point in ring)
                _add_polyline(msp, shape, layer=layer, coordinates=coordinates, closed=True)
        return
    if shape.kind == "circle":
        if shape.coordinates:
            msp.add_circle(
                shape.coordinates[0],
                float(shape.properties.get("radius", 0.0)),
                dxfattribs={"layer": layer},
            )
        return
    if shape.kind == "arc":
        center = shape.properties.get("center")
        if center is not None and "radius" in shape.properties:
            msp.add_arc(
                center,
                float(shape.properties["radius"]),
                float(shape.properties.get("start_angle", 0.0)),
                float(shape.properties.get("end_angle", 0.0)),
                dxfattribs={"layer": layer},
            )
        else:
            _add_polyline(msp, shape, layer=layer, closed=False)
        return
    if shape.kind == "point":
        if shape.coordinates:
            msp.add_point(shape.coordinates[0], dxfattribs={"layer": layer})
        return
    if shape.kind == "text":
        if shape.coordinates:
            msp.add_text(
                str(shape.properties.get("text", "")),
                dxfattribs={
                    "layer": layer,
                    "height": float(shape.properties.get("height", 2.5)),
                    "insert": shape.coordinates[0],
                },
            )
        return
    raise ValueError(f"Unsupported trace shape kind for DXF: {shape.kind}")


def _add_metadata(msp, scene: StageScene) -> None:
    min_x, min_y, max_x, max_y = scene.bounds
    span = max(max_x - min_x, max_y - min_y, 1.0)
    height = max(0.1, min(12.0, span / 45.0))
    step = height * 1.55
    x = max_x + max(height * 3.0, span * 0.04)
    lines = [
        scene.warning,
        f"sample: {scene.sample_id}",
        f"stage: {scene.stage_id}",
        f"artifact: {scene.artifact_id}",
        f"candidate: {scene.hypothesis_id or '-'}",
        f"status: {scene.status}",
        scene.title_zh,
        scene.summary_zh,
        *(f"{key}: {value}" for key, value in scene.metrics),
    ]
    y = max_y
    for index, line in enumerate(lines):
        msp.add_text(
            line,
            dxfattribs={
                "layer": "STAGE_META",
                "height": height,
                "insert": (x, y - index * step),
            },
        )


def render_scene_dxf(scene: StageScene, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new("R2007", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1
    doc.header["$TDCREATE"] = 2451544.5
    doc.header["$TDUPDATE"] = 2451544.5
    doc.header["$FINGERPRINTGUID"] = _guid(scene, "fingerprint")
    doc.header["$VERSIONGUID"] = _guid(scene, "version")
    _ensure_layers(doc, scene)
    msp = doc.modelspace()
    for shape in sorted(scene.shapes, key=lambda item: (item.role, item.shape_id)):
        _add_shape(msp, shape, scene.styles[shape.role].layer_name)
    _add_metadata(msp, scene)
    metadata = doc.ezdxf_metadata()
    metadata["CREATED_BY_EZDXF"] = "steel-dxf-split layered deterministic"
    metadata["WRITTEN_BY_EZDXF"] = "steel-dxf-split layered deterministic"
    previous = ezdxf.options.write_fixed_meta_data_for_testing
    ezdxf.options.write_fixed_meta_data_for_testing = True
    try:
        doc.saveas(path)
    finally:
        ezdxf.options.write_fixed_meta_data_for_testing = previous
    _replace_header_guid(path, "$FINGERPRINTGUID", _guid(scene, "fingerprint"))
    _replace_header_guid(path, "$VERSIONGUID", _guid(scene, "version"))
    return path
