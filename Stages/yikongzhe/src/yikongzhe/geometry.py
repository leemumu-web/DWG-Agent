"""几何分析模块。

提供外轮廓提取、矩形判断、内部孔洞检测等功能。
使用 shapely 进行稳健的几何计算。
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from itertools import combinations

from shapely import (
    LinearRing,
    LineString,
    Point,
    Polygon,
)
from shapely.ops import linemerge, polygonize

logger = logging.getLogger(__name__)

# 矩形判断容差
_RECT_ANGLE_TOLERANCE = 1.0  # 角度偏差容忍度（度）
_RECT_LENGTH_RATIO_TOLERANCE = 0.02  # 对边长度相对偏差 < 2%
# 坐标合并容差（用于连接相邻线段端点）
_SNAP_TOLERANCE = 0.5


def extract_outer_contour(
    entities: list,
) -> tuple[list[tuple[float, float]], Polygon | None]:
    """从几何实体中提取外轮廓。

    使用图论方法：将 LINE 端点以容差合并（snap），
    构建邻接图，找出所有闭合环，面积最大的即为外轮廓。

    Args:
        entities: 几何实体列表。

    Returns:
        (顶点列表, shapely Polygon) 二元组。
        顶点按顺序排列（含闭合点）。如果提取失败则顶点列表为空。
    """
    if not entities:
        return [], None

    # 从所有 LINE 和 LWPOLYLINE 提取边的端点对
    edges = _collect_edge_pairs(entities)
    if len(edges) < 3:
        return [], None

    # 用 snap 容差合并端点，构建邻接图
    graph, vertex_map = _build_snapped_graph(edges)

    if len(graph) < 4:
        return [], None

    # 找出所有闭合环
    cycles = _find_all_cycles(graph)

    if not cycles:
        logger.warning("未找到任何闭合环")
        return [], None

    # 按面积排序，取最大的作为外轮廓
    max_area = 0.0
    best_contour = []
    best_poly = None

    for cycle in cycles:
        if len(cycle) < 4:
            continue
        try:
            poly = Polygon(cycle)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_valid and poly.area > max_area:
                max_area = poly.area
                # 闭合轮廓
                best_contour = list(cycle) + [cycle[0]]
                best_poly = poly
        except Exception:
            continue

    if best_poly is None:
        logger.warning("无法构建有效的外轮廓多边形")
        return [], None

    return best_contour, best_poly


def _collect_edge_pairs(
    entities: list,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """从实体列表中收集端点对。

    支持 LINE（直接取端点）、ARC（取弧端点作为边）、
    LWPOLYLINE（展开为线段）。

    Returns [(start, end), ...] 列表。
    """
    pairs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for e in entities:
        dtype = e.dxftype()
        if dtype == "LINE":
            s = (e.dxf.start.x, e.dxf.start.y)
            t = (e.dxf.end.x, e.dxf.end.y)
            if math.hypot(s[0] - t[0], s[1] - t[1]) > 1e-6:
                pairs.append((s, t))
        elif dtype == "ARC":
            # ARC: 取弧端点作为边的两端
            cx, cy = e.dxf.center.x, e.dxf.center.y
            r = e.dxf.radius
            sa = math.radians(e.dxf.start_angle)
            ea = math.radians(e.dxf.end_angle)
            s = (cx + r * math.cos(sa), cy + r * math.sin(sa))
            t = (cx + r * math.cos(ea), cy + r * math.sin(ea))
            if math.hypot(s[0] - t[0], s[1] - t[1]) > 1e-6:
                pairs.append((s, t))
        elif dtype == "LWPOLYLINE":
            pts = list(e.get_points("xy"))
            for i in range(len(pts) - 1):
                p1, p2 = pts[i], pts[i + 1]
                if math.hypot(p1[0] - p2[0], p1[1] - p2[1]) > 1e-6:
                    pairs.append(((p1[0], p1[1]), (p2[0], p2[1])))
            if e.closed and len(pts) > 2:
                p1, p2 = pts[-1], pts[0]
                if math.hypot(p1[0] - p2[0], p1[1] - p2[1]) > 1e-6:
                    pairs.append(((p1[0], p1[1]), (p2[0], p2[1])))
    return pairs


def _snap_coord(value: float) -> float:
    """将坐标值 snap 到合理精度。"""
    return round(value, 2)


def _build_snapped_graph(
    edges: list[tuple[tuple[float, float], tuple[float, float]]],
) -> tuple[
    dict[tuple[float, float], set[tuple[float, float]]],
    dict[tuple[float, float], tuple[float, float]],
]:
    """构建 snap 后的邻接图。

    对每个边的端点做坐标快照，将误差范围内重合的端点视为同一顶点。

    Returns:
        (邻接表, {快照坐标: 精确坐标} 映射)。
    """
    graph: dict[tuple[float, float], set[tuple[float, float]]] = defaultdict(set)
    # 精确坐标 → 快照坐标的映射（用于保留原始坐标精度）
    exact_map: dict[tuple[float, float], tuple[float, float]] = {}

    for s, t in edges:
        sk = (_snap_coord(s[0]), _snap_coord(s[1]))
        tk = (_snap_coord(t[0]), _snap_coord(t[1]))

        # 保存精确坐标（用快照后坐标作为 key）
        exact_map[sk] = s
        exact_map[tk] = t

        if sk != tk:
            graph[sk].add(tk)
            graph[tk].add(sk)

    return dict(graph), exact_map


def _find_all_cycles(
    graph: dict[tuple[float, float], set[tuple[float, float]]],
) -> list[list[tuple[float, float]]]:
    """找出图中所有简单环。

    使用 DFS 检测所有环，去重后返回。
    只返回度数 ≥ 2 的顶点的环（忽略悬挂的杂边）。
    """
    all_cycles: list[list[tuple[float, float]]] = []
    visited_edges: set[tuple[tuple[float, float], tuple[float, float]]] = set()

    # 过滤掉度数为 1 的顶点（悬挂边）
    core_vertices = {v for v, neighbors in graph.items() if len(neighbors) >= 2}
    if not core_vertices:
        return []

    for start in core_vertices:
        for nxt in graph.get(start, set()):
            edge_key = (start, nxt) if start <= nxt else (nxt, start)
            if edge_key in visited_edges:
                continue
            cycle = _dfs_find_cycle_from_edge(graph, start, nxt, core_vertices)
            if cycle:
                all_cycles.append(cycle)
                # 标记环中所有边已访问
                for i in range(len(cycle)):
                    a = cycle[i]
                    b = cycle[(i + 1) % len(cycle)]
                    ek = (a, b) if a <= b else (b, a)
                    visited_edges.add(ek)

    return all_cycles


def _dfs_find_cycle_from_edge(
    graph: dict[tuple[float, float], set[tuple[float, float]]],
    start: tuple[float, float],
    nxt: tuple[float, float],
    core_vertices: set[tuple[float, float]],
    max_depth: int = 200,
) -> list[tuple[float, float]] | None:
    """从指定边开始，尝试回到 start 找到环。

    始终选择"最左转"的边前进，从而沿外边界行走。
    这样找到的第一个环就是外轮廓。
    """
    path = [start, nxt]
    visited_v: set[tuple[float, float]] = {start, nxt}
    current = start
    prev = nxt

    for _ in range(max_depth):
        neighbors = [v for v in graph.get(prev, set())
                     if v in core_vertices and v != current]
        if not neighbors:
            return None

        # 选择"最右转"（即外边界方向的逆时针遍历）
        next_vertex = _pick_ccw_next(current, prev, neighbors)

        if next_vertex == start and len(path) >= 3:
            return path

        if next_vertex in visited_v:
            return None  # 死胡同

        visited_v.add(next_vertex)
        path.append(next_vertex)
        current, prev = prev, next_vertex

    return None


def _pick_ccw_next(
    current: tuple[float, float],
    prev: tuple[float, float],
    candidates: list[tuple[float, float]],
) -> tuple[float, float]:
    """从候选顶点中选择最逆时针（最右转）的下一顶点。

    这确保我们沿着外边界逆时针行走。
    """
    vx = prev[0] - current[0]
    vy = prev[1] - current[1]

    best = candidates[0]
    best_angle = -10.0  # -π 到 π 范围外

    for c in candidates:
        wx = c[0] - prev[0]
        wy = c[1] - prev[1]
        angle = math.atan2(wy * vx - wx * vy, wx * vx + wy * vy)
        if angle > best_angle:
            best_angle = angle
            best = c

    return best


def _collect_segments(entities: list) -> list[LineString]:
    """从实体列表中收集所有线段。

    支持 LINE、LWPOLYLINE 类型。
    LINE 实体提取为单个线段，LWPOLYLINE 展开为其各边。
    """
    segments: list[LineString] = []
    for e in entities:
        dtype = e.dxftype()
        if dtype == "LINE":
            segments.append(
                LineString([
                    (e.dxf.start.x, e.dxf.start.y),
                    (e.dxf.end.x, e.dxf.end.y),
                ])
            )
        elif dtype == "LWPOLYLINE":
            pts = list(e.get_points("xy"))
            if len(pts) >= 2:
                for i in range(len(pts) - 1):
                    segments.append(LineString([pts[i], pts[i + 1]]))
                # 如果闭合，补上最后一条边
                if e.closed and len(pts) > 2:
                    segments.append(LineString([pts[-1], pts[0]]))
    return segments


def is_rectangle(
    contour: list[tuple[float, float]], *, tolerance: float = _RECT_ANGLE_TOLERANCE
) -> bool:
    """判断轮廓是否为矩形。

    判定条件：
    1. 顶点数 == 4（排除首尾重复点后）。
    2. 对边长度相等（相对偏差 < 2%）。
    3. 相邻边夹角 ≈ 90°（偏差 < tolerance 度）。
    4. 对边向量近似反向平行。

    Args:
        contour: 外轮廓顶点列表（含首尾重复点）。
        tolerance: 角度容差（度），默认 1.0。

    Returns:
        True 表示外轮廓为矩形。
    """
    if len(contour) < 5:
        return False

    # 去掉首尾重复点，保留 4 个顶点
    vertices = list(contour)
    if vertices[0] == vertices[-1]:
        vertices = vertices[:-1]

    if len(vertices) != 4:
        return False

    # 提取四条边向量
    edges = []
    for i in range(4):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i + 1) % 4]
        edges.append((x2 - x1, y2 - y1))

    lengths = [math.hypot(dx, dy) for dx, dy in edges]

    # 对边：edges[0] vs edges[2]，edges[1] vs edges[3]
    for i in range(2):
        l1, l2 = lengths[i], lengths[i + 2]
        if l1 < 1e-6 or l2 < 1e-6:
            return False
        ratio = abs(l1 - l2) / max(l1, l2)
        if ratio > _RECT_LENGTH_RATIO_TOLERANCE:
            return False

    # 检查相邻边夹角 ≈ 90°
    for i in range(4):
        dx1, dy1 = edges[i]
        dx2, dy2 = edges[(i + 1) % 4]
        dot = dx1 * dx2 + dy1 * dy2
        l1 = lengths[i]
        l2 = lengths[(i + 1) % 4]
        if l1 < 1e-6 or l2 < 1e-6:
            return False
        cos_a = dot / (l1 * l2)
        cos_a = max(-1.0, min(1.0, cos_a))
        angle = math.degrees(math.acos(abs(cos_a)))
        if abs(90.0 - angle) > tolerance:
            return False

    # 检查对边反向平行
    for i in range(2):
        dx1, dy1 = edges[i]
        dx2, dy2 = edges[i + 2]
        l1, l2 = lengths[i], lengths[i + 2]
        dot = dx1 * dx2 + dy1 * dy2
        cos_a = dot / (l1 * l2)
        if cos_a > -0.99:
            return False

    return True


def has_internal_holes(
    outer_contour: list[tuple[float, float]], all_entities: list
) -> bool:
    """判断外轮廓内部是否存在孔洞。

    检测策略：
    1. 用外轮廓构建 Polygon。
    2. 检查 CIRCLE/ARC 实体：若中心在外轮廓内部，视为孔洞。
    3. 检查 LINE/LWPOLYLINE 构成的闭合环：若某闭合环完全
       位于外轮廓内部，视为孔洞（无论孔的大小）。

    Args:
        outer_contour: 外轮廓顶点列表。
        all_entities: 所有几何实体。

    Returns:
        True 表示存在内部孔洞。
    """
    if not outer_contour or len(outer_contour) < 4:
        return False

    try:
        outer_poly = Polygon(outer_contour)
    except Exception:
        return False

    for e in all_entities:
        dtype = e.dxftype()

        if dtype == "CIRCLE":
            center = Point(e.dxf.center.x, e.dxf.center.y)
            if outer_poly.contains(center):
                return True

        elif dtype == "ARC":
            center = Point(e.dxf.center.x, e.dxf.center.y)
            if outer_poly.contains(center):
                return True

    # 检测 LINE/LWPOLYLINE 构成的内部闭合环
    edges = _collect_edge_pairs(all_entities)
    if len(edges) < 3:
        return False

    graph, _ = _build_snapped_graph(edges)
    if len(graph) < 4:
        return False

    cycles = _find_all_cycles(graph)
    for cycle in cycles:
        if len(cycle) < 4:
            continue
        try:
            poly = Polygon(cycle)
            if poly.is_valid and poly.area > 0 and outer_poly.contains_properly(poly):
                return True
        except Exception:
            continue

    return False


def build_geometry_graph(
    lines: list,
) -> dict[tuple[float, float], list[tuple[float, float]]]:
    """从 LINE 列表构建邻接图。

    将端点坐标四舍五入到合理精度（0.01mm），
    建立顶点之间的邻接关系。

    Args:
        lines: ezdxf LINE 实体列表。

    Returns:
        {顶点坐标: [相邻顶点列表]} 的邻接表。
    """
    def _round(pt: tuple[float, float]) -> tuple[float, float]:
        return (round(pt[0], 2), round(pt[1], 2))

    graph: dict[tuple[float, float], set[tuple[float, float]]] = defaultdict(set)
    for e in lines:
        if e.dxftype() != "LINE":
            continue
        s = _round((e.dxf.start.x, e.dxf.start.y))
        t = _round((e.dxf.end.x, e.dxf.end.y))
        if s != t:
            graph[s].add(t)
            graph[t].add(s)

    return {k: list(v) for k, v in graph.items()}