from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from math import cos, pi
from pathlib import Path

import ezdxf
from ezdxf.entities.lwpolyline import LWPolyline
from ezdxf.enums import TextEntityAlignment
from ezdxf.filemanagement import new
from shapely import affinity
from shapely.geometry import MultiPolygon, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import polylabel, unary_union

from ..hole_color_policy import WHITE_ACI, plan_symmetric_circle_colors
from ..part_mark_layout import (
    MINIMUM_PART_MARK_HEIGHT_MM,
    STANDARD_PART_MARK_HEIGHTS_MM,
    PartMarkTarget,
    label_em_width as _label_em_width,
    layout_part_marks,
    part_mark_clearance_envelope,  # noqa: F401 - release tooling compatibility export
    part_mark_envelope,  # noqa: F401 - release tooling compatibility export
    preferred_standard_part_mark_height,
)
from .dxf_artifact_io import save_deterministic_dxf
from .equivalence import (
    PlateOutputGroup,
    allowance_group_contract,
    group_equivalent_plate_pairs,
)
from .manufacturing_ir import (
    BoxManufacturingIR,
    BoxWeldAllowanceContract,
    CircularCutIR,
    ContourSegmentIR,
    InnerContourIR,
    PhysicalPlateRole,
    contour_polygon,
)

_WINDOWS_CJK_DXF_FONT = "simsun.ttc"
_CIRCLE_BUFFER_QUADRANT_SEGMENTS = 128
_PLATE_XDATA_APPID = "BOX_DXF_SPLIT"
_PLATE_XDATA_SCHEMA = "BOX-WELD-ALLOWANCE-1.0"


class OutputPurpose(StrEnum):
    PRODUCTION = "production"
    REVIEW = "review"


class CodegenAuthorizationError(PermissionError):
    """The proof disposition does not authorize the requested DXF."""


@dataclass(frozen=True, slots=True)
class LaidOutPlate:
    group_id: str
    roles: tuple[PhysicalPlateRole, ...]
    physical_plate_ids: tuple[str, ...]
    label: str
    quantity: int
    material: str
    thickness_mm: float
    outer_segments: tuple[ContourSegmentIR, ...]
    circular_cuts: tuple[CircularCutIR, ...]
    inner_contours: tuple[InnerContourIR, ...]
    weld_allowance_contract: BoxWeldAllowanceContract | None


@dataclass(frozen=True, slots=True)
class BoxLayout:
    plates: tuple[LaidOutPlate, ...]
    label_points: tuple[tuple[float, float], ...]
    label_heights: tuple[float, ...]


def authorize_codegen(disposition: str, purpose: OutputPurpose) -> None:
    authorized = {
        "auto_accept": OutputPurpose.PRODUCTION,
        "review_required": OutputPurpose.REVIEW,
    }
    if authorized.get(disposition) is not purpose:
        raise CodegenAuthorizationError(
            f"{disposition!r} cannot generate {purpose.value!r} output"
        )


_CONFIRMED_ROLE_LABEL_CORRECTIONS = {
    ("2b1-cb-86", PhysicalPlateRole.WEB_LEFT): "下腹",
    ("2b1-cb-86", PhysicalPlateRole.WEB_RIGHT): "上腹",
    ("h-9-cb-133", PhysicalPlateRole.FLANGE_TOP): "下翼",
    ("h-9-cb-133", PhysicalPlateRole.FLANGE_BOTTOM): "上翼",
}


def canonical_box_label(
    part_number: str,
    roles: tuple[PhysicalPlateRole, ...],
) -> str:
    """Return the MIR-role mark for one proved output group."""

    if roles == (PhysicalPlateRole.WEB_LEFT, PhysicalPlateRole.WEB_RIGHT):
        role = "腹"
    elif roles == (PhysicalPlateRole.FLANGE_TOP, PhysicalPlateRole.FLANGE_BOTTOM):
        role = "翼"
    else:
        physical_role = roles[0]
        role = _CONFIRMED_ROLE_LABEL_CORRECTIONS.get(
            (part_number.casefold(), physical_role),
            {
                PhysicalPlateRole.WEB_LEFT: "上腹",
                PhysicalPlateRole.WEB_RIGHT: "下腹",
                PhysicalPlateRole.FLANGE_TOP: "上翼",
                PhysicalPlateRole.FLANGE_BOTTOM: "下翼",
            }[physical_role],
        )
    return f"p={part_number}{role}"


def _translated_group(
    group: PlateOutputGroup,
    part_number: str,
    dx: float,
    dy: float,
) -> LaidOutPlate:
    plate = group.representative

    def point(value: tuple[float, float]) -> tuple[float, float]:
        return (value[0] + dx, value[1] + dy)

    outer = tuple(
        replace(segment, start=point(segment.start), end=point(segment.end))
        for segment in plate.outer_segments
    )
    cuts = tuple(replace(cut, center=point(cut.center)) for cut in plate.circular_cuts)
    inner = tuple(
        replace(
            contour,
            segments=tuple(
                replace(segment, start=point(segment.start), end=point(segment.end))
                for segment in contour.segments
            ),
        )
        for contour in plate.inner_contours
    )
    return LaidOutPlate(
        group_id=group.group_id,
        roles=group.roles,
        physical_plate_ids=tuple(plate.plate_id for plate in group.physical_plates),
        label=canonical_box_label(part_number, group.roles),
        quantity=group.quantity,
        material=plate.material,
        thickness_mm=plate.thickness_mm,
        outer_segments=outer,
        circular_cuts=cuts,
        inner_contours=inner,
        weld_allowance_contract=allowance_group_contract(group),
    )


def _place_row(
    groups: tuple[PlateOutputGroup, ...],
    *,
    part_number: str,
    start_x: float,
    start_y: float,
    gap: float,
) -> tuple[tuple[LaidOutPlate, ...], float]:
    placed: list[LaidOutPlate] = []
    cursor_x = start_x
    maximum_height = 0.0
    for group in groups:
        bounds = contour_polygon(group.representative.outer_segments).bounds
        plate = _translated_group(
            group,
            part_number,
            cursor_x - float(bounds[0]),
            start_y - float(bounds[1]),
        )
        placed.append(plate)
        placed_bounds = contour_polygon(plate.outer_segments).bounds
        cursor_x = float(placed_bounds[2]) + gap
        maximum_height = max(maximum_height, float(placed_bounds[3] - placed_bounds[1]))
    return tuple(placed), maximum_height


def _circumscribed_circle_polygon(cut: CircularCutIR) -> Polygon:
    """Polygonal removal guaranteed to contain the exact circular cut."""

    angle = pi / (4.0 * _CIRCLE_BUFFER_QUADRANT_SEGMENTS)
    radius = cut.radius_mm / cos(angle)
    return Point(cut.center).buffer(
        radius,
        quad_segs=_CIRCLE_BUFFER_QUADRANT_SEGMENTS,
    )


def plate_material_geometry(plate: LaidOutPlate) -> BaseGeometry:
    """Return plate material after every proved inner removal is subtracted."""

    outer = contour_polygon(plate.outer_segments)
    removals: list[BaseGeometry] = [
        contour_polygon(contour.segments) for contour in plate.inner_contours
    ]
    removals.extend(_circumscribed_circle_polygon(cut) for cut in plate.circular_cuts)
    material: BaseGeometry = (
        outer if not removals else outer.difference(unary_union(removals))
    )
    if not material.is_valid:
        material = material.buffer(0)
    if material.is_empty or material.area <= 1e-6:
        raise ValueError(f"BOX plate {plate.group_id!r} has no valid label material")
    return material


def _polygon_components(geometry: BaseGeometry) -> tuple[Polygon, ...]:
    if isinstance(geometry, Polygon):
        return (geometry,)
    if isinstance(geometry, MultiPolygon):
        return tuple(geometry.geoms)
    return tuple(
        component
        for component in getattr(geometry, "geoms", ())
        if isinstance(component, Polygon)
    )


def _legacy_preference_anchor(
    plate: LaidOutPlate,
    height: float,
) -> tuple[float, float] | None:
    """Apply the former conservative envelope only to preserve old font choices."""

    material = plate_material_geometry(plate)
    half_width = (_label_em_width(plate.label) + 2.0) * height / 2.0
    half_height = 1.25 * height

    def accepted(candidate: Point) -> tuple[float, float] | None:
        point = (float(candidate.x), float(candidate.y))
        envelope = box(
            point[0] - half_width,
            point[1] - half_height,
            point[0] + half_width,
            point[1] + half_height,
        )
        return point if material.covers(envelope) else None

    outer = contour_polygon(plate.outer_segments)
    for candidate in (
        outer.centroid,
        material.centroid,
        material.representative_point(),
    ):
        result = accepted(candidate)
        if result is not None:
            return result

    normalized = affinity.scale(
        material,
        xfact=1.0 / half_width,
        yfact=1.0 / half_height,
        origin=(0.0, 0.0),
    )
    components = sorted(
        _polygon_components(normalized),
        key=lambda polygon: (-polygon.area, polygon.wkb_hex),
    )
    for component in components:
        candidate = polylabel(component, tolerance=0.001)
        restored = Point(candidate.x * half_width, candidate.y * half_height)
        result = accepted(restored)
        if result is not None:
            return result
    return None


def _layout_label_capacity(plates: tuple[LaidOutPlate, ...]) -> float:
    """Return the outer-envelope upper bound for a shared mark height."""

    capacities: list[float] = []
    for plate in plates:
        bounds = contour_polygon(plate.outer_segments).bounds
        spans = (float(bounds[2] - bounds[0]), float(bounds[3] - bounds[1]))
        long_span = max(spans)
        short_span = min(spans)
        # Keep at least 0.75 text heights above and below the cap height, and
        # one text height of horizontal padding at either end of the label.
        capacities.append(
            min(
                short_span / 2.5,
                long_span / (_label_em_width(plate.label) + 2.0),
            )
        )
    capacity = min(capacities, default=0.0)
    return capacity


def _preferred_box_label_height(plates: tuple[LaidOutPlate, ...]) -> float:
    """Preserve existing BOX sizes while allowing formerly rejected 30 mm marks."""

    maximum_preference = preferred_standard_part_mark_height(
        _layout_label_capacity(plates)
    )
    for height in STANDARD_PART_MARK_HEIGHTS_MM:
        if height > maximum_preference + 1e-9:
            continue
        if all(
            _legacy_preference_anchor(plate, height) is not None
            for plate in plates
        ):
            return height
    return MINIMUM_PART_MARK_HEIGHT_MM


def _layout_labels(
    plates: tuple[LaidOutPlate, ...],
) -> tuple[tuple[tuple[float, float], ...], float]:
    preferred_height = _preferred_box_label_height(plates)
    targets = tuple(
        PartMarkTarget(
            target_id=plate.group_id,
            label=plate.label,
            outer_geometry=contour_polygon(plate.outer_segments),
            material_geometry=plate_material_geometry(plate),
            hole_count=len(plate.circular_cuts) + len(plate.inner_contours),
        )
        for plate in plates
    )
    placements = layout_part_marks(
        targets,
        preferred_height_mm=preferred_height,
    )
    return (
        tuple(placement.point for placement in placements),
        placements[0].height_mm,
    )


def layout_box_manufacturing_ir(
    manufacturing_ir: BoxManufacturingIR,
    *,
    gap: float = 500.0,
    start_x: float = 200.0,
    start_y: float = 200.0,
) -> BoxLayout:
    """Lay flange outputs below web outputs without changing their geometry."""

    groups = group_equivalent_plate_pairs(manufacturing_ir.physical_plates)
    web_groups = tuple(
        group
        for group in groups
        if group.roles[0]
        in {
            PhysicalPlateRole.WEB_LEFT,
            PhysicalPlateRole.WEB_RIGHT,
        }
    )
    flange_groups = tuple(group for group in groups if group not in web_groups)
    flanges, flange_height = _place_row(
        flange_groups,
        part_number=manufacturing_ir.part_number,
        start_x=start_x,
        start_y=start_y,
        gap=gap,
    )
    webs, _ = _place_row(
        web_groups,
        part_number=manufacturing_ir.part_number,
        start_x=start_x,
        start_y=start_y + flange_height + gap,
        gap=gap,
    )
    plates = (*webs, *flanges)
    label_points, text_height = _layout_labels(plates)
    return BoxLayout(
        plates=plates,
        label_points=label_points,
        label_heights=tuple(text_height for _ in plates),
    )


def _ensure_layers(document: ezdxf.document.Drawing) -> None:
    for name, color in {
        "PLATE_CUT": 7,
        "CUT_HOLE": WHITE_ACI,
        "PART_LABEL": 3,
        "SPLIT_NOTE": 5,
    }.items():
        if name not in document.layers:
            document.layers.add(name, color=color)
    if _PLATE_XDATA_APPID not in document.appids:
        document.appids.add(_PLATE_XDATA_APPID)


def _ensure_style(document: ezdxf.document.Drawing) -> str:
    if "SplitChinese" not in document.styles:
        document.styles.add("SplitChinese", font=_WINDOWS_CJK_DXF_FONT)
    return "SplitChinese"


def _add_contour(
    document: ezdxf.document.Drawing,
    segments: tuple[ContourSegmentIR, ...],
    layer: str,
) -> LWPolyline:
    """Lower one semantic BOX contour to a native closed DXF curve.

    ``bulge`` is part of the MIR edge semantics and is copied verbatim.  This
    avoids ACIS REGION/SAT data, whose validity cannot be certified by ezdxf
    for the independent geometry kernels used by AutoCAD-family products.
    """

    return document.modelspace().add_lwpolyline(
        [(segment.start[0], segment.start[1], segment.bulge) for segment in segments],
        format="xyb",
        close=True,
        dxfattribs={"layer": layer},
    )


def write_box_clean(
    manufacturing_ir: BoxManufacturingIR,
    output_path: Path,
    *,
    purpose: OutputPurpose,
) -> BoxLayout:
    """Generate one clean 1:1 DXF exclusively from immutable BOX MIR."""

    authorize_codegen(manufacturing_ir.proof_disposition, purpose)
    document = new("R2007", setup=False)
    document.header["$INSUNITS"] = 4
    _ensure_layers(document)
    style = _ensure_style(document)
    layout = layout_box_manufacturing_ir(manufacturing_ir)
    for plate, label_point, label_height in zip(
        layout.plates,
        layout.label_points,
        layout.label_heights,
        strict=True,
    ):
        outer = _add_contour(document, plate.outer_segments, "PLATE_CUT")
        contract = plate.weld_allowance_contract
        if contract is not None:
            outer.set_xdata(
                _PLATE_XDATA_APPID,
                [
                    (1000, _PLATE_XDATA_SCHEMA),
                    (1000, plate.group_id),
                    (1000, ",".join(role.value for role in plate.roles)),
                    (1070, plate.quantity),
                    (1000, contract.coordinate_unit),
                    (1040, contract.main_length_mm),
                    (1040, contract.allowance_mm),
                    (1000, contract.summary_sha256),
                    (1000, manufacturing_ir.fingerprint),
                ],
            )
        for contour in plate.inner_contours:
            inner = _add_contour(document, contour.segments, "CUT_HOLE")
            inner.dxf.color = WHITE_ACI
        plate_bounds = contour_polygon(plate.outer_segments).bounds
        color_plan = plan_symmetric_circle_colors(
            tuple(
                (cut.center[0], cut.center[1], cut.radius_mm)
                for cut in plate.circular_cuts
            ),
            plate_min_x_mm=float(plate_bounds[0]),
            plate_max_x_mm=float(plate_bounds[2]),
        )
        for cut, color_aci in zip(
            plate.circular_cuts,
            color_plan.colors_aci,
            strict=True,
        ):
            document.modelspace().add_circle(
                cut.center,
                cut.radius_mm,
                dxfattribs={"layer": "CUT_HOLE", "color": color_aci},
            )
        document.modelspace().add_text(
            plate.label,
            height=label_height,
            dxfattribs={"layer": "PART_LABEL", "style": style},
        ).set_placement(label_point, align=TextEntityAlignment.MIDDLE_CENTER)
    auditor = document.audit()
    if auditor.has_errors:
        raise ValueError(
            f"BOX output DXF audit failed with {len(auditor.errors)} errors"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_deterministic_dxf(
        document,
        output_path,
        artifact_fingerprint=manufacturing_ir.fingerprint,
    )
    return layout
