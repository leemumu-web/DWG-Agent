from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import cos, pi
from pathlib import Path
import uuid

import ezdxf
from ezdxf.enums import TextEntityAlignment
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .bh_geometry import flatten_bulge_contour
from .bh_manufacturing_ir import (
    BHContourSegmentIR,
    BHManufacturingIR,
    BHPlateIR,
    ManufacturingPlateRole,
)
from .bh_models import (
    BHPlate,
    BHPlateRole,
    BulgeContour,
    BulgeVertex,
    CircularCut,
)
from .bh_trace import TraceObserver, emit_trace
from .bh_trace_geometry import contour_shape, cut_shapes
from .geometry_types import Point2D
from .hole_color_policy import WHITE_ACI, plan_symmetric_circle_colors
from .part_mark_layout import (
    PartMarkTarget,
    layout_part_marks,
)
from . import __version__


# Production DXF is opened in Windows CAD applications.  Keep this separate
# from the Linux-only PNG preview fallback in dxf_preview.py: a font installed
# on the build host is not a valid delivery-font contract for the recipient.
_WINDOWS_CJK_DXF_FONT = "simsun.ttc"
_CIRCLE_BUFFER_QUADRANT_SEGMENTS = 128
_PLATE_XDATA_APPID = "STEEL_DXF_SPLIT"
_PLATE_XDATA_SCHEMA = "BH-WELD-ALLOWANCE-1.0"


@dataclass(slots=True)
class BHLayout:
    plates: list[BHPlate]
    label_points: list[Point2D]
    label_heights: list[float]


class OutputPurpose(str, Enum):
    PRODUCTION = "production"
    REVIEW = "review"


class BHCodegenAuthorizationError(PermissionError):
    """The proof disposition does not authorize the requested output kind."""


def authorize_codegen(disposition: str, purpose: OutputPurpose) -> None:
    allowed = {
        "auto_accept": OutputPurpose.PRODUCTION,
        "review_required": OutputPurpose.REVIEW,
    }
    if allowed.get(disposition) is not purpose:
        raise BHCodegenAuthorizationError(
            f"{disposition!r} cannot generate {purpose.value!r} output"
        )


def _escape_non_ascii_dxf_text(path: Path) -> None:
    """Write CJK labels as DXF Unicode transport, independent of code pages.

    R2007 normally writes UTF-8, while ``$DWGCODEPAGE`` is a legacy single-byte
    declaration.  Some Windows CAD readers still apply that declaration before
    recognising the R2007 version and consequently corrupt or reject UTF-8 CJK
    text.  DXF ``\\U+XXXX`` escapes are ASCII transport recognised by those
    readers and preserve the same displayed Unicode characters.
    """

    source = path.read_text(encoding="utf-8")
    escaped: list[str] = []
    for character in source:
        codepoint = ord(character)
        if codepoint <= 0x7F:
            escaped.append(character)
        elif codepoint <= 0xFFFF:
            escaped.append(f"\\U+{codepoint:04X}")
        else:
            raise ValueError(
                "Production DXF text contains a non-BMP character that cannot "
                "be represented by the required DXF Unicode transport."
            )
    path.write_text("".join(escaped), encoding="ascii", newline="\n")




def _replace_header_guid(path: Path, variable: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    for index in range(0, len(lines) - 3, 2):
        if lines[index].strip() == "9" and lines[index + 1].strip() == variable:
            if lines[index + 2].strip() != "2":
                raise ValueError(f"Unexpected DXF group code for {variable}.")
            lines[index + 3] = value
            path.write_text(
                "\n".join(lines) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            return
    raise ValueError(f"DXF header variable {variable} was not found.")




def _canonicalize_classes_section(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) % 2:
        raise ValueError("DXF text does not contain complete group-code pairs.")
    pairs = [(lines[index], lines[index + 1]) for index in range(0, len(lines), 2)]
    section_start = None
    section_end = None
    for index in range(len(pairs) - 1):
        if pairs[index][0].strip() == "0" and pairs[index][1].strip() == "SECTION":
            if pairs[index + 1][0].strip() == "2" and pairs[index + 1][1].strip() == "CLASSES":
                section_start = index + 2
                break
    if section_start is None:
        return
    for index in range(section_start, len(pairs)):
        if pairs[index][0].strip() == "0" and pairs[index][1].strip() == "ENDSEC":
            section_end = index
            break
    if section_end is None:
        raise ValueError("DXF CLASSES section has no ENDSEC record.")

    records: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    for pair in pairs[section_start:section_end]:
        if pair[0].strip() == "0" and pair[1].strip() == "CLASS":
            if current:
                records.append(current)
            current = [pair]
        else:
            current.append(pair)
    if current:
        records.append(current)

    def class_name(record: list[tuple[str, str]]) -> str:
        return next((value.strip() for code, value in record if code.strip() == "1"), "")

    sorted_records = sorted(records, key=lambda record: (class_name(record), record))
    flattened = [pair for record in sorted_records for pair in record]
    rebuilt = pairs[:section_start] + flattened + pairs[section_end:]
    output_lines = [item for pair in rebuilt for item in pair]
    path.write_text(
        "\n".join(output_lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _save_deterministic(
    doc: ezdxf.document.Drawing,
    output_path: Path,
    *,
    manufacturing_fingerprint: str,
) -> None:
    """Write reproducible DXF bytes while keeping per-part deterministic GUIDs."""

    previous = ezdxf.options.write_fixed_meta_data_for_testing
    metadata = doc.ezdxf_metadata()
    metadata["CREATED_BY_EZDXF"] = f"steel-dxf-split {__version__} deterministic"
    metadata["WRITTEN_BY_EZDXF"] = f"steel-dxf-split {__version__} deterministic"
    ezdxf.options.write_fixed_meta_data_for_testing = True
    try:
        doc.saveas(output_path)
    finally:
        ezdxf.options.write_fixed_meta_data_for_testing = previous

    namespace = uuid.UUID("fd98f7d2-7d61-5b69-a2bf-164d37a84eed")
    fingerprint_guid = "{" + str(
        uuid.uuid5(namespace, manufacturing_fingerprint + ":document")
    ).upper() + "}"
    version_guid = "{" + str(
        uuid.uuid5(namespace, manufacturing_fingerprint + f":version:{__version__}")
    ).upper() + "}"
    _replace_header_guid(output_path, "$FINGERPRINTGUID", fingerprint_guid)
    _replace_header_guid(output_path, "$VERSIONGUID", version_guid)
    _canonicalize_classes_section(output_path)
    _escape_non_ascii_dxf_text(output_path)


def _ensure_layers(doc: ezdxf.document.Drawing) -> None:
    for name, color in {
        "PLATE_CUT": 7,
        "CUT_HOLE": WHITE_ACI,
        "PART_LABEL": 3,
        "SPLIT_NOTE": 5,
    }.items():
        if name not in doc.layers:
            doc.layers.add(name, color=color)
    if _PLATE_XDATA_APPID not in doc.appids:
        doc.appids.add(_PLATE_XDATA_APPID)


def _ensure_style(doc: ezdxf.document.Drawing) -> str:
    if "SplitChinese" not in doc.styles:
        doc.styles.add("SplitChinese", font=_WINDOWS_CJK_DXF_FONT)
    return "SplitChinese"


def _add_contour(doc: ezdxf.document.Drawing, contour: BulgeContour, layer: str):
    """Write a native closed curve entity accepted by AutoCAD-family CAD.

    ACIS REGION stores proprietary SAT payload.  The Python DXF library can
    round-trip that payload but cannot certify that ZWCAD's ACIS kernel accepts
    it.  A closed LWPOLYLINE is a single selectable/stretchable native CAD
    object and preserves the exact bulge arcs without tessellation.
    """

    return doc.modelspace().add_lwpolyline(
        [(vertex.x, vertex.y, vertex.bulge) for vertex in contour.vertices],
        format="xyb",
        close=contour.closed,
        dxfattribs={"layer": layer},
    )


def _add_plate(doc: ezdxf.document.Drawing, plate: BHPlate) -> None:
    outer = _add_contour(doc, plate.contour, "PLATE_CUT")
    contract = plate.provenance.get("weld_allowance_contract")
    if isinstance(contract, dict):
        outer.set_xdata(
            _PLATE_XDATA_APPID,
            [
                (1000, _PLATE_XDATA_SCHEMA),
                (1000, str(plate.provenance["manufacturing_plate_id"])),
                (1000, str(plate.provenance["manufacturing_role"])),
                (1000, str(contract["coordinate_unit"])),
                (1040, float(contract["main_length_mm"])),
                (1040, float(contract["allowance_mm"])),
                (1000, str(plate.provenance["weld_allowance_contract_sha256"])),
                (1000, str(plate.provenance["manufacturing_ir_fingerprint"])),
            ],
        )
    for contour in plate.inner_contours:
        inner = _add_contour(doc, contour, "CUT_HOLE")
        inner.dxf.color = WHITE_ACI
    color_plan = plan_symmetric_circle_colors(
        tuple(
            (cut.center.x, cut.center.y, cut.radius)
            for cut in plate.circular_cuts
        ),
        plate_min_x_mm=plate.bbox.min_x,
        plate_max_x_mm=plate.bbox.max_x,
    )
    for cut, color_aci in zip(
        plate.circular_cuts,
        color_plan.colors_aci,
        strict=True,
    ):
        doc.modelspace().add_circle(
            (cut.center.x, cut.center.y),
            cut.radius,
            dxfattribs={"layer": "CUT_HOLE", "color": color_aci},
        )


def _bh_plate_outer_geometry(plate: BHPlate) -> Polygon:
    return Polygon(flatten_bulge_contour(plate.contour, max_sagitta=0.01))


def _circumscribed_circle_polygon(cut: CircularCut) -> Polygon:
    angle = pi / (4.0 * _CIRCLE_BUFFER_QUADRANT_SEGMENTS)
    radius = cut.radius / cos(angle)
    return Point(cut.center.x, cut.center.y).buffer(
        radius,
        quad_segs=_CIRCLE_BUFFER_QUADRANT_SEGMENTS,
    )


def bh_plate_material_geometry(plate: BHPlate) -> BaseGeometry:
    """Return BH plate material after every proved opening is subtracted."""

    outer = _bh_plate_outer_geometry(plate)
    removals: list[BaseGeometry] = [
        Polygon(flatten_bulge_contour(contour, max_sagitta=0.01))
        for contour in plate.inner_contours
    ]
    removals.extend(
        _circumscribed_circle_polygon(cut) for cut in plate.circular_cuts
    )
    material: BaseGeometry = (
        outer if not removals else outer.difference(unary_union(removals))
    )
    if not material.is_valid:
        material = material.buffer(0)
    if material.is_empty or material.area <= 1e-6:
        plate_id = plate.provenance.get("manufacturing_plate_id", plate.label)
        raise ValueError(f"BH plate {plate_id!r} has no valid label material")
    return material


def _contour_from_ir(
    segments: tuple[BHContourSegmentIR, ...],
    *,
    tolerance: float = 1e-6,
) -> BulgeContour:
    if len(segments) < 3:
        raise ValueError("Manufacturing IR contour requires at least three segments.")
    for segment, following in zip(
        segments,
        (*segments[1:], segments[0]),
        strict=True,
    ):
        if max(
            abs(segment.end[0] - following.start[0]),
            abs(segment.end[1] - following.start[1]),
        ) > tolerance:
            raise ValueError("Manufacturing IR contour is not end-to-start closed.")
    return BulgeContour(
        [
            BulgeVertex(segment.start[0], segment.start[1], segment.bulge)
            for segment in segments
        ]
    )


def _plate_from_ir(plate: BHPlateIR) -> BHPlate:
    return BHPlate(
        role=(
            BHPlateRole.WEB
            if plate.role == ManufacturingPlateRole.WEB
            else BHPlateRole.FLANGE
        ),
        contour=_contour_from_ir(plate.outer_segments),
        thickness=plate.thickness_mm,
        label=plate.label,
        quantity=plate.quantity,
        circular_cuts=[
            CircularCut(Point2D(*cut.center), cut.radius_mm)
            for cut in plate.circular_cuts
        ],
        inner_contours=[
            _contour_from_ir(contour.segments)
            for contour in plate.inner_contours
        ],
        source_index=plate.source_assembly_plate_index,
        provenance={
            "manufacturing_plate_id": plate.plate_id,
            "manufacturing_role": plate.role.value,
            **(
                {
                    "weld_allowance_contract": (
                        plate.weld_allowance_contract.to_dict()
                    ),
                    "weld_allowance_contract_sha256": (
                        plate.weld_allowance_contract.summary_sha256
                    ),
                }
                if plate.weld_allowance_contract is not None
                else {}
            ),
        },
    )


def _geometry_key(plate: BHPlateIR) -> tuple[object, ...]:
    return (
        plate.material,
        plate.thickness_mm,
        tuple(
            (segment.start, segment.end, segment.bulge)
            for segment in plate.outer_segments
        ),
        tuple((cut.center, cut.radius_mm) for cut in plate.circular_cuts),
        tuple(
            tuple(
                (segment.start, segment.end, segment.bulge)
                for segment in contour.segments
            )
            for contour in plate.inner_contours
        ),
    )


def codegen_plates(manufacturing_ir: BHManufacturingIR) -> tuple[BHPlate, ...]:
    """Convert three physical IR roles into one or three drawing geometries."""

    by_role = {plate.role: plate for plate in manufacturing_ir.plates}
    if set(by_role) != {
        ManufacturingPlateRole.WEB,
        ManufacturingPlateRole.UPPER_FLANGE,
        ManufacturingPlateRole.LOWER_FLANGE,
    } or len(manufacturing_ir.plates) != 3:
        raise ValueError("Manufacturing codegen requires one web and two flange roles.")
    web = _plate_from_ir(by_role[ManufacturingPlateRole.WEB])
    upper_ir = by_role[ManufacturingPlateRole.UPPER_FLANGE]
    lower_ir = by_role[ManufacturingPlateRole.LOWER_FLANGE]
    merge = (
        upper_ir.merge_group_id is not None
        and upper_ir.merge_group_id == lower_ir.merge_group_id
        and upper_ir.merge_authorized
        and lower_ir.merge_authorized
    )
    if merge:
        if _geometry_key(upper_ir) != _geometry_key(lower_ir):
            raise ValueError(
                "Authorized flange merge has non-identical manufacturing geometry."
            )
        flange = _plate_from_ir(upper_ir)
        flange.quantity = 2
        result = (web, flange)
    else:
        result = (web, _plate_from_ir(upper_ir), _plate_from_ir(lower_ir))
    for plate in result:
        plate.provenance["manufacturing_ir_fingerprint"] = (
            manufacturing_ir.fingerprint
        )
    return result


def layout_bh_manufacturing_ir(
    manufacturing_ir: BHManufacturingIR,
    *,
    gap: float = 500.0,
    start_x: float = 200.0,
    start_y: float = 200.0,
    preferred_text_height: float | None = None,
) -> BHLayout:
    plates = codegen_plates(manufacturing_ir)
    web = next(plate for plate in plates if plate.role == BHPlateRole.WEB)
    flanges = [plate for plate in plates if plate.role == BHPlateRole.FLANGE]
    flange_gap = max(500.0, gap)
    placed_flanges: list[BHPlate] = []
    cursor_x = start_x
    max_flange_height = 0.0
    for flange in flanges:
        placed = flange.translated(cursor_x - flange.bbox.min_x, start_y - flange.bbox.min_y)
        placed_flanges.append(placed)
        cursor_x = placed.bbox.max_x + flange_gap
        max_flange_height = max(max_flange_height, placed.bbox.height)

    lower_width = max(cursor_x - start_x - flange_gap, 0.0)
    web_x = start_x + max(0.0, (lower_width - web.bbox.width) / 2.0)
    web_y = start_y + max_flange_height + gap
    placed_web = web.translated(web_x - web.bbox.min_x, web_y - web.bbox.min_y)

    plates = [placed_web, *placed_flanges]
    if preferred_text_height is None:
        flange_width = max(
            (
                plate.bbox.height
                for plate in plates
                if plate.role == BHPlateRole.FLANGE
            ),
            default=200.0,
        )
        preferred_text_height = max(
            30.0,
            min(75.0, flange_width * 0.15),
        )
    targets = tuple(
        PartMarkTarget(
            target_id=str(
                plate.provenance.get(
                    "manufacturing_plate_id",
                    f"{plate.role.value}:{index}",
                )
            ),
            label=plate.label,
            outer_geometry=_bh_plate_outer_geometry(plate),
            material_geometry=bh_plate_material_geometry(plate),
            hole_count=len(plate.circular_cuts) + len(plate.inner_contours),
        )
        for index, plate in enumerate(plates)
    )
    placements = layout_part_marks(
        targets,
        preferred_height_mm=preferred_text_height,
    )
    return BHLayout(
        plates,
        [Point2D(*placement.point) for placement in placements],
        [placement.height_mm for placement in placements],
    )


def write_bh_clean(
    manufacturing_ir: BHManufacturingIR,
    output_path: Path,
    *,
    purpose: OutputPurpose,
    text_height: float | None = None,
    observer: TraceObserver | None = None,
    hypothesis_id: str | None = None,
) -> BHLayout:
    authorize_codegen(manufacturing_ir.proof_disposition, purpose)
    doc = ezdxf.new("R2007", setup=False)
    doc.header["$INSUNITS"] = 4
    _ensure_layers(doc)
    style = _ensure_style(doc)
    layout = layout_bh_manufacturing_ir(
        manufacturing_ir,
        preferred_text_height=text_height,
    )
    for plate, label_point, label_height in zip(
        layout.plates,
        layout.label_points,
        layout.label_heights,
        strict=True,
    ):
        _add_plate(doc, plate)
        doc.modelspace().add_text(
            plate.label,
            height=label_height,
            dxfattribs={"layer": "PART_LABEL", "style": style},
        ).set_placement(
            (label_point.x, label_point.y),
            align=TextEntityAlignment.MIDDLE_CENTER,
        )

    trace_shapes = []
    for index, plate in enumerate(layout.plates, start=1):
        trace_shapes.append(
            contour_shape(
                f"layout-plate-{index:02d}", "manufacturing_plate", plate.contour
            )
        )
        trace_shapes.extend(
            cut_shapes(
                f"layout-cut-{index:02d}",
                "manufacturing_cut",
                plate.circular_cuts,
            )
        )
        trace_shapes.extend(
            contour_shape(
                f"layout-opening-{index:02d}-{opening_index:02d}",
                "manufacturing_cut",
                contour,
            )
            for opening_index, contour in enumerate(plate.inner_contours, start=1)
        )
    emit_trace(
        observer,
        stage_id="10_codegen_layout",
        artifact_id="codegen_layout",
        status="observed",
        title_zh="制造板件排版",
        summary_zh=f"将 {len(layout.plates)} 个板件排入清洁 1:1 DXF",
        hypothesis_id=hypothesis_id,
        shapes=tuple(trace_shapes),
        payload={
            "output_name": output_path.name,
            "plate_count": len(layout.plates),
            "text_height": layout.label_heights[0],
            "labels": [plate.label for plate in layout.plates],
            "helper_line_policy": "LINE/XLINE/RAY forbidden",
        },
    )

    auditor = doc.audit()
    if auditor.has_errors:
        raise ValueError(f"BH output DXF audit failed with {len(auditor.errors)} errors.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _save_deterministic(
        doc,
        output_path,
        manufacturing_fingerprint=manufacturing_ir.fingerprint,
    )
    return layout
