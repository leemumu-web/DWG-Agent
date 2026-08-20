from __future__ import annotations

from collections import Counter
from pathlib import Path

import ezdxf
from ezdxf import bbox
from ezdxf.entities import DXFEntity
from ezdxf.enums import TextEntityAlignment

from steel_dxf_split.dxf_io import load_document
from steel_dxf_split.part_mark_layout import (
    PartMarkTarget,
    layout_part_marks,
    preferred_standard_part_mark_height,
)

from .contracts import DevelopedPlate, PLSplitError, PLWriteResult
from .geometry import validate_closed_outline

_WINDOWS_CJK_DXF_FONT = "simsun.ttc"
_DIMENSION_TOLERANCE_MM = 0.001


def _ensure_layers(document: ezdxf.document.Drawing) -> None:
    for name, color in {
        "PLATE_CUT": 7,
        "PART_LABEL": 3,
        "SPLIT_NOTE": 5,
    }.items():
        if name not in document.layers:
            document.layers.add(name, color=color)


def _ensure_style(document: ezdxf.document.Drawing) -> str:
    if "SplitChinese" not in document.styles:
        document.styles.add("SplitChinese", font=_WINDOWS_CJK_DXF_FONT)
    return "SplitChinese"


def _manufacturing_clone(entity: DXFEntity) -> DXFEntity:
    clone = entity.copy()
    clone.dxf.layer = "PLATE_CUT"
    for attribute in ("color", "linetype", "lineweight", "transparency"):
        clone.dxf.discard(attribute)
    return clone


def _dimension_error(code: str, message: str) -> PLSplitError:
    return PLSplitError(code, message)


def validate_saved_pl_dxf(
    output_path: str | Path,
    developed: DevelopedPlate,
) -> PLWriteResult:
    target = Path(output_path).resolve()
    try:
        document = load_document(target)
    except Exception as error:
        raise PLSplitError("OUTPUT_LOAD_FAILED", f"结果 DXF 无法重新审计读取：{error}") from error
    if document.dxfversion != "AC1021":
        raise PLSplitError("OUTPUT_DXF_VERSION", "PL 结果必须是 R2007 DXF。")
    if int(document.header.get("$INSUNITS", 0)) != 4:
        raise PLSplitError("OUTPUT_UNITS", "PL 结果单位必须是毫米。")
    modelspace = tuple(document.modelspace())
    plate_entities = tuple(
        entity for entity in modelspace if entity.dxf.layer == "PLATE_CUT"
    )
    label_entities = tuple(
        entity for entity in modelspace if entity.dxf.layer == "PART_LABEL"
    )
    if len(modelspace) != len(plate_entities) + len(label_entities):
        raise PLSplitError(
            "OUTPUT_ENTITY_CONTRACT",
            "结果 DXF 含 PLATE_CUT 和 PART_LABEL 之外的模型空间实体。",
        )
    if len(plate_entities) != len(developed.transformed_entities) or any(
        entity.dxftype() not in {"LINE", "ARC", "ELLIPSE"}
        for entity in plate_entities
    ):
        raise PLSplitError(
            "OUTPUT_ENTITY_CONTRACT",
            "结果 PLATE_CUT 实体数量或原生类型不符合展开结果。",
        )
    expected_label = f"p={developed.metadata.part_number}"
    if (
        len(label_entities) != 1
        or label_entities[0].dxftype() != "TEXT"
        or label_entities[0].dxf.text != expected_label
        or label_entities[0].dxf.style != "SplitChinese"
    ):
        raise PLSplitError(
            "OUTPUT_LABEL_CONTRACT",
            f"结果必须只有一个 SplitChinese 标签 {expected_label}。",
        )
    validate_closed_outline(plate_entities)
    native_bounds = bbox.extents(plate_entities, fast=False)
    if not native_bounds.has_data:
        raise PLSplitError("OUTPUT_ENTITY_CONTRACT", "结果 PLATE_CUT 没有有效范围。")
    min_x = float(native_bounds.extmin.x)
    min_y = float(native_bounds.extmin.y)
    max_x = float(native_bounds.extmax.x)
    max_y = float(native_bounds.extmax.y)
    length = max_x - min_x
    width = max_y - min_y
    if abs(length - developed.metrics.target_length_mm) > _DIMENSION_TOLERANCE_MM:
        raise _dimension_error("OUTPUT_LENGTH", "结果外轮廓长度与目标长度不一致。")
    if abs(width - developed.outline.width_mm) > _DIMENSION_TOLERANCE_MM:
        raise _dimension_error("OUTPUT_WIDTH", "结果外轮廓板宽发生变化。")
    if abs(min_x - developed.outline.anchor_x_mm) > _DIMENSION_TOLERANCE_MM:
        raise _dimension_error("OUTPUT_ANCHOR", "结果外轮廓左端锚点发生变化。")
    auditor = document.audit()
    if auditor.has_errors:
        raise PLSplitError(
            "OUTPUT_AUDIT",
            f"结果 DXF 审计发现 {len(auditor.errors)} 个错误。",
        )
    counts = Counter(entity.dxftype() for entity in modelspace)
    return PLWriteResult(
        output_path=target,
        min_x_mm=min_x,
        length_mm=length,
        width_mm=width,
        label=expected_label,
        entity_type_counts=tuple(sorted(counts.items())),
        audit_error_count=len(auditor.errors),
    )


def write_pl_dxf(
    developed: DevelopedPlate,
    output_path: str | Path,
) -> PLWriteResult:
    target = Path(output_path).resolve()
    document = ezdxf.new("R2007", setup=False)
    document.header["$INSUNITS"] = 4
    _ensure_layers(document)
    style = _ensure_style(document)
    modelspace = document.modelspace()
    manufacturing_entities = tuple(
        _manufacturing_clone(entity) for entity in developed.transformed_entities
    )
    developed_polygon = validate_closed_outline(manufacturing_entities)
    for entity in manufacturing_entities:
        modelspace.add_entity(entity)
    label = f"p={developed.metadata.part_number}"
    placement = layout_part_marks(
        (
            PartMarkTarget(
                target_id=developed.metadata.part_number,
                label=label,
                outer_geometry=developed_polygon,
                material_geometry=developed_polygon,
            ),
        ),
        preferred_height_mm=preferred_standard_part_mark_height(
            developed.outline.width_mm / 2.5
        ),
    )[0]
    modelspace.add_text(
        label,
        height=placement.height_mm,
        dxfattribs={"layer": "PART_LABEL", "style": style},
    ).set_placement(placement.point, align=TextEntityAlignment.MIDDLE_CENTER)
    auditor = document.audit()
    if auditor.has_errors:
        raise PLSplitError(
            "OUTPUT_AUDIT",
            f"保存前 DXF 审计发现 {len(auditor.errors)} 个错误。",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    document.saveas(target)
    return validate_saved_pl_dxf(target, developed)
