"""分类编排模块。

对 DXF 文件中的所有板件执行三步分类判断，
并整合结果、查表得到最终类别名称。
"""

from __future__ import annotations

import logging

from yikongzhe.bend_detector import detect_bend
from yikongzhe.dxf_reader import read_dxf_directory
from yikongzhe.geometry import (
    extract_outer_contour,
    has_internal_holes,
    is_rectangle,
)
from yikongzhe.models import (
    BendType,
    DxfResult,
    HoleType,
    Part,
    PartClassification,
    ShapeType,
)

logger = logging.getLogger(__name__)

# 三要素 → 类别名称查表
_CATEGORY_TABLE: dict[tuple[ShapeType, HoleType, BendType], str] = {
    (ShapeType.RECTANGLE, HoleType.WITHOUT_HOLE, BendType.WITHOUT_BEND): "方",
    (ShapeType.IRREGULAR, HoleType.WITHOUT_HOLE, BendType.WITHOUT_BEND): "异",
    (ShapeType.RECTANGLE, HoleType.WITH_HOLE, BendType.WITHOUT_BEND): "方孔",
    (ShapeType.IRREGULAR, HoleType.WITH_HOLE, BendType.WITHOUT_BEND): "异孔",
    (ShapeType.RECTANGLE, HoleType.WITHOUT_HOLE, BendType.WITH_BEND): "方折",
    (ShapeType.IRREGULAR, HoleType.WITHOUT_HOLE, BendType.WITH_BEND): "异折",
    (ShapeType.RECTANGLE, HoleType.WITH_HOLE, BendType.WITH_BEND): "方孔折",
    (ShapeType.IRREGULAR, HoleType.WITH_HOLE, BendType.WITH_BEND): "异孔折",
}

# 所有合法类别名，用于验证
VALID_CATEGORIES = set(_CATEGORY_TABLE.values())


def build_category_name(
    shape: ShapeType, hole: HoleType, bend: BendType
) -> str:
    """查表：三要素 → 最终类别名称。

    Args:
        shape: 方/异。
        hole: 有孔/无孔。
        bend: 有折/无折。

    Returns:
        类别名，如 "方孔折"。

    Raises:
        KeyError: 非法组合。
    """
    key = (shape, hole, bend)
    if key not in _CATEGORY_TABLE:
        raise KeyError(f"非法类别组合: {shape}, {hole}, {bend}")
    return _CATEGORY_TABLE[key]


def classify_part_shape_and_hole(
    part: Part,
) -> tuple[ShapeType, HoleType]:
    """对单块板执行步骤1和步骤2：方/异 + 有孔/无孔。

    Args:
        part: 板件对象。

    Returns:
        (shape, hole) 二元组。
    """
    contour, outer_poly = extract_outer_contour(part.entities)
    if not contour:
        logger.warning("无法提取外轮廓: %s", part.name)
        return ShapeType.IRREGULAR, HoleType.WITHOUT_HOLE

    shape = ShapeType.RECTANGLE if is_rectangle(contour) else ShapeType.IRREGULAR
    hole = (
        HoleType.WITH_HOLE
        if has_internal_holes(contour, part.entities)
        else HoleType.WITHOUT_HOLE
    )
    return shape, hole



def classify_dxf(parts: list[Part]) -> DxfResult:
    """对单个 DXF 文件的所有板件执行三步分类。

    编排逻辑：
    1. 单零件 → 不检测折弯，bend = WITHOUT_BEND。
    2. 存在翼板（is_web=False）→ BH/BOX 模式：
       - 腹板检测折弯，翼板继承腹板折弯状态。
       - 腹板的 bend 固定为 WITHOUT_BEND。
    3. 通用多零件 → 每个零件独立检测折弯。
    4. 查表得到最终类别名。

    Args:
        parts: 单个 DXF 文件的 Part 列表。

    Returns:
        DxfResult 包含所有板件的分类结果。
    """
    if not parts:
        return DxfResult(dxf_file="", parts=[])

    dxf_file = parts[0].dxf_file
    webs = [p for p in parts if p.is_web]
    flanges = [p for p in parts if not p.is_web]
    results: list[PartClassification] = []

    if len(parts) == 1:
        # 单零件 DXF → 不检测折弯
        part = parts[0]
        shape, hole = classify_part_shape_and_hole(part)
        bend = BendType.WITHOUT_BEND
        results.append(PartClassification(
            part_name=part.name,
            dxf_file=dxf_file,
            shape=shape,
            hole=hole,
            bend=bend,
            category=build_category_name(shape, hole, bend),
        ))
    elif flanges:
        # BH/BOX 模式：腹板检测折弯，翼板继承
        web_has_bend = False
        for web in webs:
            if detect_bend(web):
                web_has_bend = True
                break

        for web in webs:
            shape, hole = classify_part_shape_and_hole(web)
            bend = BendType.WITHOUT_BEND
            results.append(PartClassification(
                part_name=web.name, dxf_file=dxf_file,
                shape=shape, hole=hole, bend=bend,
                category=build_category_name(shape, hole, bend),
            ))

        for flange in flanges:
            shape, hole = classify_part_shape_and_hole(flange)
            bend = BendType.WITH_BEND if web_has_bend else BendType.WITHOUT_BEND
            results.append(PartClassification(
                part_name=flange.name, dxf_file=dxf_file,
                shape=shape, hole=hole, bend=bend,
                category=build_category_name(shape, hole, bend),
            ))
    else:
        # 通用多零件模式：每个零件独立检测折弯
        for part in parts:
            shape, hole = classify_part_shape_and_hole(part)
            has_bend = detect_bend(part)
            bend = BendType.WITH_BEND if has_bend else BendType.WITHOUT_BEND
            results.append(PartClassification(
                part_name=part.name, dxf_file=dxf_file,
                shape=shape, hole=hole, bend=bend,
                category=build_category_name(shape, hole, bend),
            ))

    logger.info(
        "分类 %s: %d 块板, 单零件=%s",
        dxf_file, len(results), len(parts) == 1,
    )
    return DxfResult(dxf_file=dxf_file, parts=results)


def classify_directory(
    directory: str, *, encoding: str = "utf-8"
) -> list[DxfResult]:
    """对目录下所有 DXF 文件执行分类。

    Args:
        directory: 输入目录路径。
        encoding: DXF 编码。

    Returns:
        DxfResult 列表，每个元素对应一个 DXF 文件的分类结果。
    """
    all_parts_groups = read_dxf_directory(directory, encoding=encoding)
    results: list[DxfResult] = []
    for parts in all_parts_groups:
        if parts:
            results.append(classify_dxf(parts))
    return results