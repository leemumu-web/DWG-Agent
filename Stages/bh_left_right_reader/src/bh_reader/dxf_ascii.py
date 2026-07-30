from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import atan2, cos, degrees, isfinite, radians, sin
from pathlib import Path
from typing import Iterable

from .dxf_geometry import arc_points, polyline_points
from .model import DrawingData, Primitive, UnsupportedGeometry
from .units import insunits_info

Tag = tuple[int, str]


def _decode(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("gb18030", "utf-8-sig", "cp1252", "latin1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin1", errors="replace")


def _tags(path: Path) -> list[Tag]:
    lines = _decode(path).splitlines()
    if len(lines) % 2:
        lines = lines[:-1]
    result: list[Tag] = []
    for index in range(0, len(lines), 2):
        try:
            code = int(lines[index].strip())
        except ValueError:
            continue
        result.append((code, lines[index + 1].rstrip()))
    return result


def _sections(tags: list[Tag]) -> dict[str, list[Tag]]:
    result: dict[str, list[Tag]] = defaultdict(list)
    index = 0
    while index < len(tags):
        if tags[index] == (0, "SECTION") and index + 1 < len(tags) and tags[index + 1][0] == 2:
            name = tags[index + 1][1]
            index += 2
            while index < len(tags) and tags[index] != (0, "ENDSEC"):
                result[name].append(tags[index])
                index += 1
        index += 1
    return result


def _header_value(tags: list[Tag], variable: str) -> str | None:
    for index, (code, value) in enumerate(tags):
        if code == 9 and value.strip().upper() == variable.upper() and index + 1 < len(tags):
            return tags[index + 1][1].strip()
    return None


def _first(data: list[Tag], code: int, default: str = "") -> str:
    for item_code, value in data:
        if item_code == code:
            return value
    return default


def _values(data: list[Tag], code: int) -> list[str]:
    return [value for item_code, value in data if item_code == code]


def _float(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _strict_finite_float(value: str, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {label}: {value!r}") from exc
    if not isfinite(parsed):
        raise ValueError(f"non-finite {label}: {value!r}")
    return parsed


def _entities(tags: list[Tag]) -> list[tuple[str, list[Tag]]]:
    result: list[tuple[str, list[Tag]]] = []
    index = 0
    while index < len(tags):
        if tags[index][0] != 0:
            index += 1
            continue
        kind = tags[index][1]
        index += 1
        data: list[Tag] = []
        while index < len(tags) and tags[index][0] != 0:
            data.append(tags[index])
            index += 1
        if kind == "POLYLINE":
            # A legacy POLYLINE stores its points in following VERTEX records,
            # rather than inside the POLYLINE header.  Keep the entity-level
            # metadata and flatten only the XY vertex coordinates into the
            # same tag shape used by LWPOLYLINE so downstream geometry sees a
            # single connected outline.
            header = data
            data = [
                (code, value)
                for code, value in header
                if code in {5, 8, 40, 41, 70, 210, 220, 230}
            ]
            while index < len(tags) and tags[index] == (0, "VERTEX"):
                index += 1
                vertex: list[Tag] = []
                while index < len(tags) and tags[index][0] != 0:
                    vertex.append(tags[index])
                    index += 1
                data.extend([
                    (10, _first(vertex, 10)),
                    (20, _first(vertex, 20)),
                ])
                bulge = _first(vertex, 42)
                if bulge:
                    data.append((42, bulge))
                for code in (40, 41):
                    width = _first(vertex, code)
                    if width:
                        data.append((code, width))
            if index < len(tags) and tags[index] == (0, "SEQEND"):
                index += 1
        result.append((kind, data))
    return result


def _blocks(tags: list[Tag]) -> dict[str, tuple[tuple[float, float], list[tuple[str, list[Tag]]]]]:
    result: dict[str, tuple[tuple[float, float], list[tuple[str, list[Tag]]]]] = {}
    index = 0
    while index < len(tags):
        if tags[index] != (0, "BLOCK"):
            index += 1
            continue
        index += 1
        header: list[Tag] = []
        while index < len(tags) and tags[index][0] != 0:
            header.append(tags[index])
            index += 1
        name = _first(header, 2) or _first(header, 3)
        base = (_float(_first(header, 10)), _float(_first(header, 20)))
        body: list[Tag] = []
        while index < len(tags) and tags[index] != (0, "ENDBLK"):
            body.append(tags[index])
            index += 1
        result[name] = (base, _entities(body))
        index += 1
    return result


@dataclass(frozen=True, slots=True)
class Transform:
    m00: float = 1.0
    m01: float = 0.0
    m10: float = 0.0
    m11: float = 1.0
    tx: float = 0.0
    ty: float = 0.0

    def point(self, point: tuple[float, float]) -> tuple[float, float]:
        x, y = point
        return self.m00 * x + self.m01 * y + self.tx, self.m10 * x + self.m11 * y + self.ty

    def compose(self, child: "Transform") -> "Transform":
        return Transform(
            self.m00 * child.m00 + self.m01 * child.m10,
            self.m00 * child.m01 + self.m01 * child.m11,
            self.m10 * child.m00 + self.m11 * child.m10,
            self.m10 * child.m01 + self.m11 * child.m11,
            self.m00 * child.tx + self.m01 * child.ty + self.tx,
            self.m10 * child.tx + self.m11 * child.ty + self.ty,
        )

    def source_curve_extrema_degrees(self) -> tuple[float, ...]:
        """Return local circle angles that become global X/Y extrema.

        Curves are sampled before an INSERT's affine matrix is applied.  For
        a local radius vector ``(cos(a), sin(a))``, global X is proportional
        to ``m00*cos(a) + m01*sin(a)`` and global Y to the analogous second
        row.  Both projection directions and their opposites must be present
        on the source arc to keep the physical global envelope exact.
        """
        result: list[float] = []
        for x_coefficient, y_coefficient in (
            (self.m00, self.m01),
            (self.m10, self.m11),
        ):
            if abs(x_coefficient) <= 1e-12 and abs(y_coefficient) <= 1e-12:
                continue
            angle = degrees(atan2(y_coefficient, x_coefficient))
            result.extend((angle, angle + 180.0))
        return tuple(result)

    def preserves_circular_curves(self) -> bool:
        """Whether this affine placement leaves source circles circular.

        An ARC or bulge under a non-uniform INSERT scale becomes an ellipse.
        The portable readers do not claim ELLIPSE semantics, so such a source
        must be rejected consistently instead of silently using one backend's
        partial approximation.
        """
        first_length_squared = self.m00 * self.m00 + self.m10 * self.m10
        second_length_squared = self.m01 * self.m01 + self.m11 * self.m11
        dot = self.m00 * self.m01 + self.m10 * self.m11
        scale = max(first_length_squared, second_length_squared, 1.0)
        return (
            first_length_squared > 1e-24
            and abs(first_length_squared - second_length_squared) <= 1e-10 * scale
            and abs(dot) <= 1e-10 * scale
        )


def _insert_transform(data: list[Tag], base: tuple[float, float]) -> Transform:
    x = _strict_finite_float(_first(data, 10, "0"), "INSERT X")
    y = _strict_finite_float(_first(data, 20, "0"), "INSERT Y")
    sx = _strict_finite_float(_first(data, 41, "1"), "INSERT X scale")
    sy = _strict_finite_float(_first(data, 42, "1"), "INSERT Y scale")
    angle = radians(_strict_finite_float(_first(data, 50, "0"), "INSERT angle"))
    c, s = cos(angle), sin(angle)
    bx, by = base
    return Transform(c * sx, -s * sy, s * sx, c * sy, x - c * sx * bx + s * sy * by, y - s * sx * bx - c * sy * by)


def _polyline_vertices(data: list[Tag]) -> list[tuple[float, float, float]]:
    """Read ordered coordinate/bulge triples from flattened polyline tags."""
    vertices: list[tuple[float, float, float]] = []
    current: list[float | None] | None = None

    def append_current() -> None:
        if current is None:
            return
        if current[1] is None:
            raise ValueError("polyline vertex missing Y coordinate")
        vertices.append((float(current[0]), float(current[1]), float(current[2])))

    for code, value in data:
        if code == 10:
            if current is not None:
                append_current()
            current = [_strict_finite_float(value, "polyline vertex X"), None, 0.0]
        elif current is not None and code == 20:
            current[1] = _strict_finite_float(value, "polyline vertex Y")
        elif current is not None and code == 42:
            current[2] = _strict_finite_float(value, "bulge")
    if current is not None:
        append_current()
    if _first(data, 90):
        declared_count = _strict_finite_float(_first(data, 90), "LWPOLYLINE vertex count")
        if declared_count < 0 or declared_count != int(declared_count):
            raise ValueError(f"invalid LWPOLYLINE vertex count: {declared_count!r}")
        if int(declared_count) != len(vertices):
            raise ValueError(
                f"LWPOLYLINE declared {int(declared_count)} vertices but parsed {len(vertices)}"
            )
    return vertices


def _has_nonzero_bulge(data: list[Tag]) -> bool:
    return any(code == 42 and abs(_float(value)) > 1e-12 for code, value in data)


def _polyline_source_issue(kind: str, data: list[Tag]) -> str | None:
    flags_value = _strict_finite_float(_first(data, 70, "0"), "polyline flags")
    if flags_value != int(flags_value):
        raise ValueError(f"invalid polyline flags: {flags_value!r}")
    flags = int(flags_value)
    if kind == "POLYLINE" and flags & ~0x81:
        return f"unsupported POLYLINE mode flags {flags}"
    for code, label in ((40, "start width"), (41, "end width"), (43, "constant width")):
        for value in _values(data, code):
            width = _strict_finite_float(value, f"polyline {label}")
            if abs(width) > 1e-12:
                return f"non-zero polyline {label}"
    return None


def _has_nondefault_ocs(data: list[Tag]) -> bool:
    extrusion = (
        _strict_finite_float(_first(data, 210, "0"), "OCS extrusion X"),
        _strict_finite_float(_first(data, 220, "0"), "OCS extrusion Y"),
        _strict_finite_float(_first(data, 230, "1"), "OCS extrusion Z"),
    )
    return any(abs(actual - expected) > 1e-12 for actual, expected in zip(extrusion, (0.0, 0.0, 1.0), strict=True))


def _unsupported_geometry(
    kind: str, data: list[Tag], block_path: str, reason: str, effective_layer: str
) -> UnsupportedGeometry:
    return UnsupportedGeometry(
        kind=kind,
        layer=effective_layer,
        source_block=block_path,
        source_handle=_first(data, 5),
        reason=reason,
    )


def _effective_layer(data: list[Tag], inherited_layer: str | None) -> str:
    """Resolve DXF block Layer=0 inheritance without guessing other layers."""
    layer = _first(data, 8, "0")
    return inherited_layer if layer == "0" and inherited_layer is not None else layer


def _insert_is_array(data: list[Tag]) -> bool:
    """Whether an INSERT is an MINSERT array rather than one placement."""
    counts: list[int] = []
    for code, name in ((70, "INSERT column count"), (71, "INSERT row count")):
        value = _strict_finite_float(_first(data, code, "1"), name)
        if value < 1 or value != int(value):
            raise ValueError(f"invalid {name}: {value!r}")
        counts.append(int(value))
    return any(value > 1 for value in counts)


_NON_BOUNDARY_KINDS = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}


def _block_effective_boundary_layers(
    blocks: dict[str, tuple[tuple[float, float], list[tuple[str, list[Tag]]]]],
    name: str,
    inherited_layer: str,
    depth: int = 0,
) -> set[str]:
    """Return layers of source entities an INSERT can place as boundaries.

    Layer 0 inherits each enclosing INSERT layer; explicitly named child
    layers remain explicit.  Text-only block content cannot define a plate
    edge, so it does not turn a non-material placement into a material error.
    """
    block = blocks.get(name)
    if block is None or depth > 20:
        return {inherited_layer}

    def collect(
        entities: Iterable[tuple[str, list[Tag]]], current_layer: str, level: int
    ) -> set[str]:
        if level > 20:
            return {current_layer}
        layers: set[str] = set()
        for kind, data in entities:
            effective_layer = _effective_layer(data, current_layer)
            if kind == "INSERT":
                layers.update(
                    _block_effective_boundary_layers(
                        blocks, _first(data, 2), effective_layer, level + 1
                    )
                )
            elif kind not in _NON_BOUNDARY_KINDS:
                layers.add(effective_layer)
        return layers

    return collect(block[1], inherited_layer, depth)


def _insert_unsupported_geometry(
    blocks: dict[str, tuple[tuple[float, float], list[tuple[str, list[Tag]]]]],
    data: list[Tag],
    block_path: str,
    reason: str,
    effective_layer: str,
) -> list[UnsupportedGeometry]:
    layers = _block_effective_boundary_layers(blocks, _first(data, 2), effective_layer)
    return [
        _unsupported_geometry("INSERT", data, block_path, reason, layer)
        for layer in sorted(layers)
    ]


def raw_source_geometry_issues(path: Path) -> list[UnsupportedGeometry]:
    """Strictly validate raw source edges before a recovery library can fill gaps.

    The check follows actual modelspace INSERT placements and Layer=0
    inheritance.  It is intentionally independent of ezdxf's recovered
    entities: a missing required DXF field is incomplete source geometry even
    when a library happens to substitute a default coordinate or radius.
    """
    sections = _sections(_tags(path))
    blocks = _blocks(sections.get("BLOCKS", []))
    result: list[UnsupportedGeometry] = []
    boundary_kinds = {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE"}

    def add(kind: str, data: list[Tag], block_path: str, reason: str, layer: str) -> None:
        result.append(_unsupported_geometry(kind, data, block_path, reason, layer))

    def walk(
        entities: Iterable[tuple[str, list[Tag]]],
        inherited_layer: str | None,
        path_parts: tuple[str, ...],
        depth: int,
    ) -> None:
        if depth > 20:
            if inherited_layer is not None:
                result.append(
                    UnsupportedGeometry(
                        kind="INSERT",
                        layer=inherited_layer,
                        source_block="/".join(path_parts) or "MODELSPACE",
                        reason="block recursion limit exceeded before source geometry was validated",
                    )
                )
            return
        for kind, data in entities:
            layer = _effective_layer(data, inherited_layer)
            block_path = "/".join(path_parts) or "MODELSPACE"
            if kind == "INSERT":
                try:
                    if _has_nondefault_ocs(data):
                        result.extend(
                            _insert_unsupported_geometry(
                                blocks,
                                data,
                                block_path,
                                "non-default OCS extrusion on INSERT placement",
                                layer,
                            )
                        )
                        continue
                    if _insert_is_array(data):
                        result.extend(
                            _insert_unsupported_geometry(
                                blocks,
                                data,
                                block_path,
                                "MINSERT row/column array placement is not expanded",
                                layer,
                            )
                        )
                        continue
                except ValueError as exc:
                    result.extend(
                        _insert_unsupported_geometry(
                            blocks,
                            data,
                            block_path,
                            f"INSERT parsing incomplete: {exc}",
                            layer,
                        )
                    )
                    continue
                name = _first(data, 2)
                block = blocks.get(name)
                if block is None:
                    result.extend(
                        _insert_unsupported_geometry(
                            blocks,
                            data,
                            block_path,
                            f"missing block definition: {name}",
                            layer,
                        )
                    )
                    continue
                walk(block[1], layer, path_parts + (name,), depth + 1)
                continue
            if kind in _NON_BOUNDARY_KINDS:
                continue
            if kind not in boundary_kinds:
                add(kind, data, block_path, "no safe source-edge reader", layer)
                continue
            try:
                if kind in {"ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE"}:
                    if _has_nondefault_ocs(data):
                        add(kind, data, block_path, "non-default OCS extrusion", layer)
                        continue
                if kind in {"LWPOLYLINE", "POLYLINE"}:
                    polyline_issue = _polyline_source_issue(kind, data)
                    if polyline_issue:
                        add(kind, data, block_path, polyline_issue, layer)
                        continue
                _primitive(kind, data, Transform(), block_path, layer)
            except ValueError as exc:
                add(kind, data, block_path, f"source geometry parsing incomplete: {exc}", layer)

    walk(_entities(sections.get("ENTITIES", [])), None, (), 0)
    return result


def _primitive(
    kind: str,
    data: list[Tag],
    transform: Transform,
    block_path: str,
    effective_layer: str,
) -> Primitive | None:
    layer = effective_layer
    handle = _first(data, 5)
    points: list[tuple[float, float]] = []
    text = ""
    curve_extrema = transform.source_curve_extrema_degrees()
    if kind == "LINE":
        points = [
            (
                _strict_finite_float(_first(data, 10), "LINE start X"),
                _strict_finite_float(_first(data, 20), "LINE start Y"),
            ),
            (
                _strict_finite_float(_first(data, 11), "LINE end X"),
                _strict_finite_float(_first(data, 21), "LINE end Y"),
            ),
        ]
    elif kind in {"LWPOLYLINE", "POLYLINE"}:
        points = polyline_points(
            _polyline_vertices(data),
            closed=bool(int(_strict_finite_float(_first(data, 70, "0"), "polyline flags")) & 1),
            additional_extrema_degrees=curve_extrema,
        )
    elif kind == "ARC":
        points = arc_points(
            _strict_finite_float(_first(data, 10), "ARC center X"),
            _strict_finite_float(_first(data, 20), "ARC center Y"),
            _strict_finite_float(_first(data, 40), "ARC radius"),
            _strict_finite_float(_first(data, 50), "ARC start angle"),
            _strict_finite_float(_first(data, 51), "ARC end angle"),
            additional_extrema_degrees=curve_extrema,
        )
    elif kind == "CIRCLE":
        points = arc_points(
            _strict_finite_float(_first(data, 10), "CIRCLE center X"),
            _strict_finite_float(_first(data, 20), "CIRCLE center Y"),
            _strict_finite_float(_first(data, 40), "CIRCLE radius"),
            0.0,
            360.0,
            10.0,
            curve_extrema,
        )
    elif kind in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
        points = [
            (
                _strict_finite_float(_first(data, 10, "0"), "text X"),
                _strict_finite_float(_first(data, 20, "0"), "text Y"),
            )
        ]
        text = "".join(_values(data, 3) + _values(data, 1))
    else:
        return None
    transformed = [transform.point(point) for point in points]
    if any(not isfinite(value) for point in transformed for value in point):
        raise ValueError(f"non-finite transformed {kind} coordinate")
    return Primitive(kind=kind, layer=layer, points=transformed, source_block=block_path, source_handle=handle, text=text)


def read_ascii_dxf(path: Path) -> DrawingData:
    sections = _sections(_tags(path))
    insunits_raw = _header_value(sections.get("HEADER", []), "$INSUNITS")
    try:
        insunits_code = int(float(insunits_raw)) if insunits_raw is not None else None
    except ValueError:
        insunits_code = None
    insunits_name, header_unit_to_mm = insunits_info(insunits_code)
    blocks = _blocks(sections.get("BLOCKS", []))
    root_entities = _entities(sections.get("ENTITIES", []))
    primitives: list[Primitive] = []
    texts: list[Primitive] = []
    messages: list[str] = []
    fatal_messages: list[str] = []
    unsupported_geometry: list[UnsupportedGeometry] = []

    def expand(
        entities: Iterable[tuple[str, list[Tag]]],
        transform: Transform,
        path_parts: tuple[str, ...],
        depth: int,
        inherited_layer: str | None,
    ) -> None:
        if depth > 20:
            messages.append(f"block recursion limit exceeded: {'/'.join(path_parts)}")
            if inherited_layer is not None:
                unsupported_geometry.append(
                    UnsupportedGeometry(
                        kind="INSERT",
                        layer=inherited_layer,
                        source_block="/".join(path_parts) or "MODELSPACE",
                        reason="block recursion limit exceeded before source geometry was expanded",
                    )
                )
            return
        for kind, data in entities:
            effective_layer = _effective_layer(data, inherited_layer)
            block_path = "/".join(path_parts) or "MODELSPACE"
            if kind == "INSERT":
                try:
                    nondefault_ocs = _has_nondefault_ocs(data)
                except ValueError as exc:
                    fatal_messages.append(
                        f"DXF OCS parsing incomplete for INSERT {block_path}: {exc}"
                    )
                    continue
                if nondefault_ocs:
                    unsupported_geometry.extend(
                        _insert_unsupported_geometry(
                            blocks,
                            data,
                            block_path,
                            "non-default OCS extrusion on INSERT placement",
                            effective_layer,
                        )
                    )
                    continue
                try:
                    is_array = _insert_is_array(data)
                except ValueError as exc:
                    fatal_messages.append(
                        f"DXF INSERT array parsing incomplete for {block_path}: {exc}"
                    )
                    continue
                if is_array:
                    unsupported_geometry.extend(
                        _insert_unsupported_geometry(
                            blocks,
                            data,
                            block_path,
                            "MINSERT row/column array placement is not expanded",
                            effective_layer,
                        )
                    )
                    continue
                name = _first(data, 2)
                block = blocks.get(name)
                if block is None:
                    messages.append(f"missing block definition: {name}")
                    unsupported_geometry.extend(
                        _insert_unsupported_geometry(
                            blocks,
                            data,
                            block_path,
                            f"missing block definition: {name}",
                            effective_layer,
                        )
                    )
                    continue
                base, body = block
                try:
                    child_transform = transform.compose(_insert_transform(data, base))
                except ValueError as exc:
                    fatal_messages.append(
                        f"DXF INSERT transform parsing incomplete for {name}: {exc}"
                    )
                    continue
                expand(
                    body,
                    child_transform,
                    path_parts + (name,),
                    depth + 1,
                    effective_layer,
                )
                continue
            if kind == "ELLIPSE":
                unsupported_geometry.append(
                    _unsupported_geometry(
                        kind,
                        data,
                        block_path,
                        "unsupported ELLIPSE source geometry",
                        effective_layer,
                    )
                )
                continue
            if kind in {"ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE"}:
                try:
                    nondefault_ocs = _has_nondefault_ocs(data)
                except ValueError as exc:
                    fatal_messages.append(
                        f"DXF OCS parsing incomplete for {kind} "
                        f"{'/'.join(path_parts) or 'MODELSPACE'}: {exc}"
                    )
                    continue
                if nondefault_ocs:
                    unsupported_geometry.append(
                        _unsupported_geometry(
                            kind,
                            data,
                            block_path,
                            "non-default OCS extrusion",
                            effective_layer,
                        )
                    )
                    continue
            if kind in {"LWPOLYLINE", "POLYLINE"}:
                try:
                    source_issue = _polyline_source_issue(kind, data)
                except ValueError as exc:
                    fatal_messages.append(
                        f"DXF polyline parsing incomplete for {kind} {block_path}: {exc}"
                    )
                    continue
                if source_issue:
                    unsupported_geometry.append(
                        _unsupported_geometry(
                            kind, data, block_path, source_issue, effective_layer
                        )
                    )
                    continue
            if (
                kind in {"ARC", "CIRCLE"}
                or kind in {"LWPOLYLINE", "POLYLINE"} and _has_nonzero_bulge(data)
            ) and not transform.preserves_circular_curves():
                unsupported_geometry.append(
                    _unsupported_geometry(
                        kind,
                        data,
                        block_path,
                        "non-uniform INSERT transform turns circular source geometry into an unsupported ellipse",
                        effective_layer,
                    )
                )
                continue
            try:
                primitive = _primitive(kind, data, transform, block_path, effective_layer)
            except ValueError as exc:
                unsupported_geometry.append(
                    _unsupported_geometry(
                        kind,
                        data,
                        block_path,
                        f"source geometry parsing incomplete: {exc}",
                        effective_layer,
                    )
                )
                continue
            if primitive is None:
                unsupported_geometry.append(
                    _unsupported_geometry(
                        kind,
                        data,
                        block_path,
                        "no safe source-edge reader",
                        effective_layer,
                    )
                )
                continue
            primitives.append(primitive)
            if primitive.text:
                texts.append(primitive)

    expand(root_entities, Transform(), (), 0, None)
    return DrawingData(
        path=path, primitives=primitives, texts=texts, backend="ascii",
        audit_messages=messages, fatal_messages=fatal_messages,
        unsupported_geometry=unsupported_geometry, insunits_code=insunits_code,
        insunits_name=insunits_name, header_unit_to_mm=header_unit_to_mm,
    )
