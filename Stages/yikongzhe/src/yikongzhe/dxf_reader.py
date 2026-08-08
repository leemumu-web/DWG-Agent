"""DXF 文件解析模块。

从 DXF 文件中提取板件名称（TEXT 实体，图层 PartMark）和几何实体
（LINE / CIRCLE / ARC / LWPOLYLINE / REGION，图层 0 或 Part），
按 TEXT 坐标就近关联几何实体到对应板件。

REGION 实体（ACIS 数据）会被解析为 LINE 实体后参与后续处理。
"""

from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

import ezdxf
from ezdxf.entities import (
    Arc,
    Circle,
    Line,
    LWPolyline,
    Text,
)

from yikongzhe.models import Part

logger = logging.getLogger(__name__)

# 几何实体类型（支持多种表示方式）
_GEOM_ENTITY_TYPES = {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "REGION"}

# 板件文字所在的图层（PartMark 和 OtherObjectType 均承载板件名称标注）
_PART_MARK_LAYERS = {"PartMark", "OtherObjectType", "PART_LABEL"}


def _decode_dxf_text(raw: str) -> str:
    """解码 DXF TEXT 内容中的 Unicode 转义序列。

    某些 DXF 文件将中文字符存储为转义序列格式
    （如 \\U+8179 / \\u8179 → 腹），需要解码为实际 Unicode 字符。
    同时处理无效代理对字符（因编码错误导致）转为正确字节再解码。
    """
    # 处理 \\U+XXXX 和 \\uXXXX 两种格式
    def _replace(m: re.Match) -> str:
        try:
            return chr(int(m.group(1), 16))
        except (ValueError, OverflowError):
            return m.group(0)
    result = re.sub(r"\\[Uu]\+?([0-9A-Fa-f]{4})", _replace, raw)

    # 处理可能残留的孤立代理对字符 (U+DC80-U+DCFF 范围)
    # 这些通常由编码错误引入，尝试还原为原始字节后用 GB2312 解码
    chars = list(result)
    fixed: list[str] = []
    i = 0
    while i < len(chars):
        cp = ord(chars[i])
        if 0xDC80 <= cp <= 0xDCFF:
            # 收集连续代理字符
            buf = bytearray()
            while i < len(chars):
                cp2 = ord(chars[i])
                if 0xDC80 <= cp2 <= 0xDCFF:
                    buf.append(cp2 & 0xFF)
                    i += 1
                else:
                    break
            try:
                fixed.append(buf.decode("gb2312"))
            except (UnicodeDecodeError, LookupError):
                fixed.append(buf.decode("gbk", errors="replace"))
        else:
            fixed.append(chars[i])
            i += 1
    return "".join(fixed)


class _SyntheticLine:
    """模拟 ezdxf LINE 实体的轻量包装。

    当 REGION（ACIS）实体被展开为边界线段时使用，
    提供与 ezdxf LINE 兼容的 .dxftype() / .dxf.start / .dxf.end 接口。
    """

    __slots__ = ("_start", "_end")

    def __init__(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self._start = _Point(x1, y1)
        self._end = _Point(x2, y2)

    @property
    def dxf(self) -> _SyntheticLine:
        return self

    @property
    def start(self) -> _Point:
        return self._start

    @property
    def end(self) -> _Point:
        return self._end

    @staticmethod
    def dxftype() -> str:
        return "LINE"


@dataclass
class _Point:
    x: float
    y: float


class _SyntheticCircle:
    """模拟 ezdxf CIRCLE 实体的轻量包装。

    当 REGION (ACIS) 实体包含完整圆形边界（如螺栓孔）时使用，
    提供与 ezdxf CIRCLE 兼容的 .dxftype() / .dxf.center 接口。
    """

    __slots__ = ("_center",)

    def __init__(self, cx: float, cy: float) -> None:
        self._center = _Point(cx, cy)

    @property
    def dxf(self) -> _SyntheticCircle:
        return self

    @property
    def center(self) -> _Point:
        return self._center

    @staticmethod
    def dxftype() -> str:
        return "CIRCLE"


def _explode_region_to_entities(region_entity) -> list:
    """将 REGION (ACIS) 实体展开为 LINE + 合成 CIRCLE 列表。

    直线边展开为 LINE，完整圆形边（2π）展开为合成 CIRCLE，
    代表螺栓孔等不可展开的圆形特征。

    Args:
        region_entity: ezdxf Region 实体（须带有 SAB 数据）。

    Returns:
        混合列表，包含 _SyntheticLine 和 _SyntheticCircle 对象。
    """
    entities: list = list(_explode_region_to_lines(region_entity))
    entities.extend(_extract_circular_holes_from_region(region_entity))
    return entities


def _extract_circular_holes_from_region(region_entity) -> list[_SyntheticCircle]:
    """从 REGION 中提取完整圆形孔的合成 CIRCLE 标记。

    完整圆形（start_param=0, end_param≈2π）边代表螺栓孔等
    圆形特征，其顶点位于圆周上，可作为孔洞检测的标记点。

    Args:
        region_entity: ezdxf Region 实体。

    Returns:
        _SyntheticCircle 列表，每个对应一个完整圆孔。
    """
    sab = region_entity.sab
    if not sab:
        return []

    from ezdxf.acis import api as _acis_api

    try:
        bodies = _acis_api.load(sab)
    except Exception:
        return []

    circles: list[_SyntheticCircle] = []
    _2pi = math.pi * 2
    for body in bodies:
        for lump in body.lumps():
            for shell in lump.shells():
                for face in shell.faces():
                    for loop in face.loops():
                        for coedge in loop.coedges():
                            edge = coedge.edge
                            if edge is None:
                                continue
                            if abs(edge.start_param) < 1e-6 and abs(edge.end_param - _2pi) < 1e-4:
                                sv = edge.start_vertex
                                if sv:
                                    try:
                                        loc = sv.point.location
                                        circles.append(_SyntheticCircle(loc.x, loc.y))
                                    except Exception:
                                        continue
    return circles


def _explode_region_to_lines(region_entity) -> list[_SyntheticLine]:
    """将 REGION (ACIS) 实体的边界展开为 LINE 线段列表。

    解析 SAB 二进制 ACIS 数据，遍历 body → lump → shell → face →
    loop → coedge → edge 层次结构，提取每条边的起点和终点坐标。

    Args:
        region_entity: ezdxf Region 实体（须带有 SAB 数据）。

    Returns:
        _SyntheticLine 列表，每条对应 ACIS 边界的一条直线边。
        非直线边（如圆弧）目前跳过。
    """
    sab = region_entity.sab
    if not sab:
        return []

    from ezdxf.acis import api as _acis_api
    from ezdxf.acis import entities as _acis_entities

    try:
        bodies = _acis_api.load(sab)
    except Exception:
        logger.warning("无法解析 REGION ACIS 数据", exc_info=True)
        return []

    lines: list[_SyntheticLine] = []
    for body in bodies:
        for lump in body.lumps():
            for shell in lump.shells():
                for face in shell.faces():
                    for loop in face.loops():
                        for coedge in loop.coedges():
                            edge = coedge.edge
                            if edge is None:
                                continue
                            sv = edge.start_vertex
                            ev = edge.end_vertex
                            if sv is None or ev is None:
                                continue
                            p1 = sv.point.location
                            p2 = ev.point.location
                            # 以端点连线近似所有类型的边（直线/圆弧/样条）
                            # 对于圆弧边，弦近似损失部分精度但保留拓扑连通性
                            if (p1 - p2).magnitude < 1e-6:
                                continue
                            lines.append(_SyntheticLine(p1.x, p1.y, p2.x, p2.y))
    return lines


def read_dxf(filepath: str | Path, *, encoding: str = "utf-8") -> list[Part]:
    """解析单个 DXF 文件，返回所有板件列表。

    Args:
        filepath: DXF 文件路径。
        encoding: 文件编码，默认 utf-8。

    Returns:
        Part 列表，每块板一个 Part 对象，
        几何实体已按 TEXT 坐标就近关联到对应板件。

    Raises:
        FileNotFoundError: 文件不存在。
        ezdxf.DXFError: DXF 解析失败。
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")

    try:
        doc = ezdxf.readfile(str(filepath), encoding=encoding)
    except Exception as e:
        raise ezdxf.DXFError(f"DXF 解析失败 {filepath}: {e}") from e

    msp = doc.modelspace()

    # 打散 INSERT 块（源文件中几何可能包装在块引用内）
    _explode_inserts(doc)

    # 提取所有 TEXT 实体，得到板件名称
    texts: list[Text] = []
    for e in msp:
        if e.dxftype() == "TEXT" and e.dxf.layer in _PART_MARK_LAYERS:
            texts.append(e)

    if not texts:
        logger.warning("未在图层 %s 找到 TEXT 实体: %s",
                       "/".join(sorted(_PART_MARK_LAYERS)), filepath.name)
        return []

    # 提取所有几何实体
    geom_entities = _extract_geometry_entities(msp)

    # 将几何实体按 TEXT 就近关联
    parts = _associate_entities_to_texts(texts, geom_entities, filepath.name)

    # 编码自动回退：若当前编码未识别出板件但存在 TEXT，尝试 gb2312
    if not parts and encoding.lower() != "gb2312":
        logger.debug("编码 %s 未识别出板件，尝试 gb2312 回退: %s", encoding, filepath.name)
        try:
            doc2 = ezdxf.readfile(str(filepath), encoding="gb2312")
        except Exception:
            logger.debug("gb2312 回退解析失败: %s", filepath.name)
        else:
            msp2 = doc2.modelspace()
            _explode_inserts(doc2)
            texts2: list[Text] = []
            for e in msp2:
                if e.dxftype() == "TEXT" and e.dxf.layer in _PART_MARK_LAYERS:
                    texts2.append(e)
            if texts2:
                geom_entities2 = _extract_geometry_entities(msp2)
                parts2 = _associate_entities_to_texts(texts2, geom_entities2, filepath.name)
                if parts2:
                    logger.info("gb2312 回退成功: %s (%d 块板)", filepath.name, len(parts2))
                    return parts2

    logger.info("解析 %s: %d 块板, %d 个几何实体",
                filepath.name, len(parts), len(geom_entities))
    return parts


def read_dxf_directory(
    directory: str | Path, *, encoding: str = "utf-8"
) -> list[list[Part]]:
    """遍历目录下所有 DXF 文件并解析。

    Args:
        directory: 目录路径。
        encoding: 文件编码。

    Returns:
        二维列表，每个元素是一个 DXF 文件中解析出的 Part 列表。
    """
    directory = Path(directory)
    results: list[list[Part]] = []

    for filepath in sorted(directory.rglob("*.dxf")):
        try:
            parts = read_dxf(filepath, encoding=encoding)
            if parts:
                results.append(parts)
        except Exception as e:
            logger.error("解析失败 %s: %s", filepath.name, e)

    return results


def _explode_inserts(doc) -> None:
    """打散所有 INSERT 块引用。

    将 INSERT 实体替换为其引用的 BLOCK 中的几何实体，
    应用 INSERT 的变换矩阵。不存在的 BLOCK 引用跳过。
    重复执行直到没有 INSERT 剩余（处理嵌套块）。
    """
    import copy as _copy
    from ezdxf.math import Matrix44 as _Matrix44

    msp = doc.modelspace()
    block_table = doc.blocks

    for _iteration in range(10):  # 最多10层嵌套
        inserts = [e for e in msp if e.dxftype() == "INSERT"]
        if not inserts:
            return

        for ins in inserts:
            block_name = ins.dxf.name
            try:
                block = block_table.get(block_name)
            except Exception:
                continue

            insert_pt = ins.dxf.insert
            ix, iy = insert_pt.x, insert_pt.y
            iz = insert_pt.z if len(insert_pt) > 2 else 0.0
            scale_x = ins.dxf.xscale if ins.dxf.hasattr("xscale") else 1.0
            scale_y = ins.dxf.yscale if ins.dxf.hasattr("yscale") else 1.0
            scale_z = ins.dxf.zscale if ins.dxf.hasattr("zscale") else 1.0
            rotation = math.radians(ins.dxf.rotation if ins.dxf.hasattr("rotation") else 0.0)

            cos_r, sin_r = math.cos(rotation), math.sin(rotation)
            matrix = _Matrix44(
                [scale_x * cos_r, -scale_y * sin_r, 0, ix],
                [scale_x * sin_r, scale_y * cos_r, 0, iy],
                [0, 0, scale_z, iz],
                [0, 0, 0, 1],
            )

            for be in block:
                dtype = be.dxftype()
                if dtype in ("LINE", "CIRCLE", "ARC", "TEXT", "MTEXT", "LWPOLYLINE", "REGION"):
                    try:
                        new_entity = _copy.deepcopy(be)
                        new_entity.transform(matrix)
                        msp.add_entity(new_entity)
                    except Exception:
                        continue

            msp.delete_entity(ins)


def _extract_geometry_entities(msp) -> list:
    """从 modelspace 提取所有几何实体。

    支持 LINE、CIRCLE、ARC、LWPOLYLINE、REGION 类型。
    REGION 实体会被展开为其 ACIS 边界 LINE 线段。
    这些实体通常位于图层 0 或 Part。
    """
    entities = []
    for e in msp:
        dtype = e.dxftype()
        if dtype in ("LINE", "ARC", "CIRCLE", "LWPOLYLINE"):
            entities.append(e)
        elif dtype == "REGION":
            entities.extend(_explode_region_to_entities(e))
    return entities


def _associate_entities_to_texts(
    texts: list[Text], geom_entities: list, dxf_filename: str
) -> list[Part]:
    """按 TEXT 坐标将几何实体就近关联到板件名称。

    关联策略：
    1. 对每个 TEXT 提取其坐标和文本内容。
    2. 判断文本是腹板（含"腹"）还是翼板（含"翼"）。
    3. 使用连通分量 + Y 排序匹配分配几何实体：
       - 构建 LINE 实体连通图（基于端点距离）
       - 将连通分量按 Y 坐标排序后与 TEXT 标签按 Y 排序一一配对
       - 小分量/非 LINE 实体按 Y 距离就近分配
    4. 不含"腹"或"翼"的 TEXT 跳过。

    对于仅含一块板（无歧义）的情况，所有实体直接归给该板。
    """
    # 解析 TEXT -> 板件信息
    part_infos: list[dict] = []
    for t in texts:
        name = _decode_dxf_text(t.dxf.text).strip()
        if not name:
            continue
        pos = (t.dxf.insert.x, t.dxf.insert.y)
        is_web = "腹" in name
        is_flange = "翼" in name
        if not is_web and not is_flange:
            logger.debug("跳过非板件文本: %s", name)
            continue
        part_infos.append({
            "name": name,
            "pos": pos,
            "is_web": is_web,
        })

    if not part_infos:
        return []

    parts = [
        Part(
            name=info["name"],
            dxf_file=dxf_filename,
            is_web=info["is_web"],
            text_position=info["pos"],
            entities=[],
        )
        for info in part_infos
    ]

    if not geom_entities:
        return parts

    # 只有一个板时，所有几何实体直接归给它
    if len(parts) == 1:
        parts[0].entities = list(geom_entities)
        return parts

    # 多块板时，使用连通分量 + Y 排序匹配分配
    _assign_entities_by_connectivity(geom_entities, parts)

    # 轮廓二次校验：将 CIRCLE/ARC 按轮廓包含关系重新分配
    # 解决距离分配将腹板孔洞误分给翼板的问题
    _reassign_entities_by_contour(parts)

    return parts


def _get_entity_center(entity) -> tuple[float, float]:
    """获取几何实体的中心坐标。"""
    dtype = entity.dxftype()
    if dtype == "LINE":
        sx, sy = entity.dxf.start.x, entity.dxf.start.y
        ex, ey = entity.dxf.end.x, entity.dxf.end.y
        return ((sx + ex) / 2, (sy + ey) / 2)
    elif dtype in ("CIRCLE", "ARC"):
        return (entity.dxf.center.x, entity.dxf.center.y)
    elif dtype == "LWPOLYLINE":
        pts = list(entity.get_points("xy"))
        if not pts:
            return (0.0, 0.0)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    return (0.0, 0.0)


def _assign_entities_by_connectivity(geom_entities: list, parts: list[Part]) -> None:
    """基于连通分量 + Y 排序匹配，将几何实体分配给板件。

    先构建 LINE + ARC 实体连通图（ARC 取其弦端点参与连通），
    将连通分量按 Y 坐标排序后，与 TEXT 标签按 Y 排序一一配对。
    这样即使 TEXT 位置与实体位置有偏移，只要相对 Y 排序一致，
    就能正确分配。ARC 参与连通可避免其被误分到不相邻的板件。

    对于 CIRCLE 及其他孤立实体，按中心坐标就近分配。
    """
    from collections import defaultdict

    # 分离 LINE/ARC 和其他实体
    line_like_entities: list = []  # LINE + ARC（参与连通图）
    other_entities: list = []
    for ge in geom_entities:
        if ge.dxftype() in ("LINE", "ARC"):
            line_like_entities.append(ge)
        else:
            other_entities.append(ge)

    if not line_like_entities:
        # 无 LINE/ARC 实体时回退到距离分配
        _assign_by_distance(geom_entities, parts)
        return

    # ── 构建 LINE + ARC 连通图 ──
    _SNAP = 0.5

    def _snap(v: tuple[float, float]) -> tuple[float, float]:
        return (round(v[0] / _SNAP) * _SNAP, round(v[1] / _SNAP) * _SNAP)

    vertex_to_entities: dict[tuple[float, float], list[int]] = defaultdict(list)

    for idx, e in enumerate(line_like_entities):
        if e.dxftype() == "LINE":
            s = (e.dxf.start.x, e.dxf.start.y)
            t = (e.dxf.end.x, e.dxf.end.y)
        elif e.dxftype() == "ARC":
            cx, cy = e.dxf.center.x, e.dxf.center.y
            r = e.dxf.radius
            sa = math.radians(e.dxf.start_angle)
            ea = math.radians(e.dxf.end_angle)
            s = (cx + r * math.cos(sa), cy + r * math.sin(sa))
            t = (cx + r * math.cos(ea), cy + r * math.sin(ea))
        else:
            continue
        if math.hypot(s[0] - t[0], s[1] - t[1]) < 1e-6:
            continue  # 跳过零长度线段
        sk = _snap(s)
        tk = _snap(t)
        vertex_to_entities[sk].append(idx)
        vertex_to_entities[tk].append(idx)

    parent = list(range(len(line_like_entities)))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: int, b: int) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[ra] = rb

    for indices in vertex_to_entities.values():
        if len(indices) > 1:
            for i in range(1, len(indices)):
                _union(indices[0], indices[i])

    # ── 收集连通分量 ──
    comp_map: dict[int, list] = defaultdict(list)
    for idx, e in enumerate(line_like_entities):
        root = _find(idx)
        comp_map[root].append(e)

    components: list[dict] = []
    for es in comp_map.values():
        ys = [(_get_entity_center(e)[1]) for e in es]
        xs = [(_get_entity_center(e)[0]) for e in es]
        mean_y = sum(ys) / len(ys) if ys else 0.0
        mean_x = sum(xs) / len(xs) if xs else 0.0
        components.append({"entities": es, "count": len(es),
                           "mean_y": mean_y, "mean_x": mean_x})

    # ── 每分量按 Y 距离分配给最近的 TEXT ──
    # 记录分量→板件映射，便于后续再分配。
    comp_assignments: list[int] = []  # comp_idx → part_idx
    for comp in components:
        cy = comp["mean_y"]
        best_idx = 0
        best_dist = float("inf")
        for i, p in enumerate(parts):
            d = abs(p.text_position[1] - cy)
            if d < best_dist:
                best_dist = d
                best_idx = i
        comp_assignments.append(best_idx)

    # ── 再分配：空板件从最近的非空板件获取实体 ──
    _redistribute_components(components, comp_assignments, parts)

    # ── 应用分配 ──
    for comp, part_idx in zip(components, comp_assignments):
        for ge in comp["entities"]:
            parts[part_idx].entities.append(ge)

    # ── 非 LINE 实体按中心坐标就近分配 ──
    _assign_by_distance(other_entities, parts)


def _redistribute_components(
    components: list[dict],
    comp_assignments: list[int],
    parts: list[Part],
) -> None:
    """将空板件的分量从最近的多余板件重新分配。

    当 Y 距离分配导致某些板件无实体时（如同一 Y 位置有多个板件），
    使用 X 距离从最近的非空板件转移分量。
    """
    empty_indices = [i for i in range(len(parts))
                     if not any(a == i for a in comp_assignments)]
    if not empty_indices:
        return

    for empty_idx in empty_indices:
        empty_part = parts[empty_idx]
        # 找分配了最多分量的非空板件（作为"捐献者"候选）
        donor_counts: dict[int, int] = {}
        for ci, ai in enumerate(comp_assignments):
            if ai != empty_idx:
                donor_counts[ai] = donor_counts.get(ai, 0) + 1
        if not donor_counts:
            continue

        # 在捐献者中，找在 X 方向上距离空板件 TEXT 最近的分量
        best_comp_idx = -1
        best_x_dist = float("inf")
        for ci, ai in enumerate(comp_assignments):
            donor_count = donor_counts.get(ai, 0)
            if donor_count <= 1:
                continue  # 只有一个分量，捐出后自己就空了
            cx = components[ci]["mean_x"]
            x_dist = abs(cx - empty_part.text_position[0])
            if x_dist < best_x_dist:
                best_x_dist = x_dist
                best_comp_idx = ci

        if best_comp_idx >= 0:
            old_donor = comp_assignments[best_comp_idx]
            comp_assignments[best_comp_idx] = empty_idx
            logger.debug("再分配分量 (%d边) 从 %s 到 %s",
                         components[best_comp_idx]["count"],
                         parts[old_donor].name, empty_part.name)


def _reassign_entities_by_contour(parts: list[Part]) -> None:
    """基于轮廓包含关系重新分配 CIRCLE/ARC 实体。

    初始基于距离的分配可能将某块板轮廓内的 CIRCLE（孔洞）
    错误分配给 TEXT 更近的另一块板。此函数提取每块板的外轮廓后，
    将所有 CIRCLE/ARC 重新分配给包含其中心的板件。

    仅处理 dxftype 为 CIRCLE 或 ARC 的实体，不影响 LINE/LWPOLYLINE
    等轮廓构建实体（它们已经通过连通分量正确分配）。
    """
    from yikongzhe.geometry import extract_outer_contour
    from shapely.geometry import Point as ShapelyPoint

    # 提取每块板的外轮廓
    part_polys: list[tuple[int, object | None]] = []
    for i, part in enumerate(parts):
        if not part.entities:
            part_polys.append((i, None))
            continue
        try:
            _, poly = extract_outer_contour(part.entities)
        except Exception:
            poly = None
        part_polys.append((i, poly))

    # 收集所有 CIRCLE/ARC 实体
    entity_map: list[tuple[int, object]] = []  # (current_part_idx, entity)
    for i, part in enumerate(parts):
        for e in part.entities:
            if e.dxftype() in ("CIRCLE", "ARC"):
                entity_map.append((i, e))

    if not entity_map:
        return

    # 对每个 CIRCLE/ARC，检查应归属于哪块板
    for current_idx, entity in entity_map:
        cx = entity.dxf.center.x
        cy = entity.dxf.center.y
        pt = ShapelyPoint(cx, cy)

        # 检查当前所属板的轮廓是否已包含它
        current_poly = part_polys[current_idx][1]
        if current_poly is not None and current_poly.contains(pt):
            continue  # 分配正确，无需重分配

        # 查找包含此中心点的其他板件轮廓
        best_idx = -1
        for part_idx, poly in part_polys:
            if part_idx == current_idx:
                continue
            if poly is not None and poly.contains(pt):
                best_idx = part_idx
                break

        if best_idx >= 0:
            # 从当前板移除，加入目标板
            try:
                parts[current_idx].entities.remove(entity)
                parts[best_idx].entities.append(entity)
                logger.debug(
                    "轮廓重分配 CIRCLE(%s,%s) %s → %s",
                    cx, cy,
                    parts[current_idx].name,
                    parts[best_idx].name,
                )
            except ValueError:
                pass  # 实体已被移除（不应发生）


def _assign_by_distance(geom_entities: list, parts: list[Part]) -> None:
    """按加权二维距离将实体分配给最近的板件。"""
    Y_WEIGHT = 3.0
    for ge in geom_entities:
        cx, cy = _get_entity_center(ge)
        best_idx = 0
        best_dist = float("inf")
        for i, p in enumerate(parts):
            tx, ty = p.text_position
            dist = math.hypot(cx - tx, Y_WEIGHT * (cy - ty))
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        parts[best_idx].entities.append(ge)