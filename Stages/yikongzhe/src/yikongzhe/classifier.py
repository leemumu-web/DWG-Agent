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
    1. 分离腹板和翼板。
    2. 腹板 → classify_part_shape_and_hole() 得到 shape + hole。
    3. 腹板 → detect_bend() 得到折弯状态。
    4. 翼板 → classify_part_shape_and_hole() 得到 shape + hole。
    5. 将腹板的折弯状态传递给所有翼板。
    6. 腹板的 bend 固定为 WITHOUT_BEND。
    7. 查表得到最终类别名。

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

    # 检测腹板折弯状态
    web_has_bend = False
    if webs:
        for web in webs:
            if detect_bend(web):
                web_has_bend = True
                break

    results: list[PartClassification] = []

    # 处理腹板
    for web in webs:
        shape, hole = classify_part_shape_and_hole(web)
        bend = BendType.WITHOUT_BEND  # 腹板永远为无折
        category = build_category_name(shape, hole, bend)
        results.append(PartClassification(
            part_name=web.name,
            dxf_file=dxf_file,
            shape=shape,
            hole=hole,
            bend=bend,
            category=category,
        ))

    # 处理翼板：统一继承腹板的折弯状态
    for flange in flanges:
        shape, hole = classify_part_shape_and_hole(flange)
        bend = BendType.WITH_BEND if web_has_bend else BendType.WITHOUT_BEND
        category = build_category_name(shape, hole, bend)
        results.append(PartClassification(
            part_name=flange.name,
            dxf_file=dxf_file,
            shape=shape,
            hole=hole,
            bend=bend,
            category=category,
        ))

    logger.info(
        "分类 %s: %d 块板, 腹板折弯=%s",
        dxf_file, len(results), web_has_bend,
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