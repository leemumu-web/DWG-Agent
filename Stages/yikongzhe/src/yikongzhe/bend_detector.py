"""折弯特征检测模块。

判断腹板外轮廓是否存在"折弯"特征——即轮廓中存在成对的平行斜边，
将腹板分为不同横向位置的平行截面，且各截面高度大致相同。

算法：在腹板外轮廓中寻找成对的平行斜边（对角线），
要求两条斜边具有相反的垂直方向（一条向上、一条向下），
且空间距离相对于板件尺寸较近，形成完整的"阶梯"结构。
"""

from __future__ import annotations

import math

from yikongzhe.models import Part

# 折弯对角线自适应长度参数
_MIN_BEND_DIAGONAL_RATIO = 0.03   # 占包围盒对角线的比例
_MIN_BEND_DIAGONAL_FLOOR = 50.0   # 绝对下限（mm）
_MIN_BEND_DIAGONAL_CAP = 300.0    # 绝对上限（mm）

# 平行判定角度容差（度）
_PARALLEL_ANGLE_TOLERANCE = 5.0

# 对角线对最大距离（占板件对角线的比例）
_MAX_PAIR_DISTANCE_RATIO = 0.30


def detect_bend(web_part: Part) -> tuple[bool, float, float]:
    """检测腹板是否存在折弯特征。

    折弯特征定义：
    腹板外轮廓呈现"阶梯形"——存在两条平行且长度相近的斜边，
    将腹板分为两个不同横向位置的平行截面。

    算法思路：
    1. 从腹板的几何实体提取外轮廓顶点序列。
    2. 找出所有长度 >100mm 且前后边均为水平边（H-H）的斜边。
    3. 将平行斜边分组，检查是否存在"反向"配对
       （一条向上、一条向下，形成阶梯）。
    4. 验证配对斜边的空间距离合理（不超过板件对角线的30%）。

    Args:
        web_part: 腹板 Part 对象。

    Returns:
        (has_bend, A, B)：
        - has_bend: True 表示腹板存在折弯特征。
        - A: 水平边较长侧的长度。
        - B: 水平边较短侧的长度。
    """
    from yikongzhe.geometry import extract_outer_contour

    entities = web_part.entities
    if not entities:
        return False, 0.0, 0.0

    contour, poly = extract_outer_contour(entities)
    if not contour or len(contour) < 7:
        return False, 0.0, 0.0

    # 去掉闭合点
    vertices = list(contour)
    if len(vertices) >= 2 and vertices[0] == vertices[-1]:
        vertices = vertices[:-1]

    if len(vertices) < 6:
        return False, 0.0, 0.0

    return _has_bend_signature(vertices)


def _has_bend_signature(
    vertices: list[tuple[float, float]],
) -> tuple[bool, float, float]:
    """分析轮廓顶点序列，检测成对的阶梯形折弯斜边。

    核心逻辑：
    - 找出所有 H-D-H（水平-斜-水平）模式的斜边。
    - 将近似平行的斜边分组。
    - 在同一角度组中寻找"反向对"：一条的 H 邻边向上跳变，
      另一条的 H 邻边向下跳变，形成完整的阶梯。
    - 验证空间距离合理（相对板件尺寸不会太远）。

    Returns:
        (has_bend, A, B)：
        - has_bend: True 表示存在折弯特征。
        - A: 平行斜边两侧水平边中较长的长度。
        - B: 平行斜边两侧水平边中较短的长度。
          无折弯时 A、B 均为 0.0。
    """
    n = len(vertices)
    if n < 6:
        return False, 0.0, 0.0

    # 计算板件包围盒（用于距离归一化）
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    bbox_diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    if bbox_diag < 1e-6:
        return False, 0.0, 0.0

    max_pair_distance = _MAX_PAIR_DISTANCE_RATIO * bbox_diag
    min_diag_len = max(_MIN_BEND_DIAGONAL_FLOOR,
                       min(bbox_diag * _MIN_BEND_DIAGONAL_RATIO, _MIN_BEND_DIAGONAL_CAP))

    # 计算每条边的属性
    edges = []
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 1e-6:
            continue
        angle = abs(math.degrees(math.atan2(dy, dx)))
        angle = min(angle % 180, (180 - angle) % 180)
        angle = min(angle, 180 - angle)

        etype = "H" if angle < 5 else ("V" if angle > 85 else "D")
        edges.append({
            "dx": dx, "dy": dy, "length": length, "angle": angle,
            "type": etype, "x1": x1, "y1": y1, "x2": x2, "y2": y2,
        })

    m = len(edges)
    if m < 6:
        return False, 0.0, 0.0

    # 收集 H-D-H / V-D-V 模式的斜边候选
    candidates = []
    for i in range(m):
        e = edges[i]
        if e["type"] != "D":
            continue
        if e["length"] < min_diag_len:
            continue

        prev_edge = edges[(i - 1) % m]
        next_edge = edges[(i + 1) % m]

        # 支持 H-D-H（水平夹斜边）和 V-D-V（垂直夹斜边）
        if prev_edge["type"] == next_edge["type"] and prev_edge["type"] in ("H", "V"):
            flank_type = prev_edge["type"]
        else:
            continue

        # 跳变方向：H型边看Y坐标差，V型边看X坐标差
        if flank_type == "H":
            delta = next_edge["y1"] - prev_edge["y1"]
        else:
            delta = next_edge["x1"] - prev_edge["x1"]

        # 斜边中点
        mid_x = (e["x1"] + e["x2"]) / 2
        mid_y = (e["y1"] + e["y2"]) / 2

        candidates.append({
            "idx": i,
            "length": e["length"],
            "angle": round(e["angle"], 1),
            "delta": delta,
            "mid_x": mid_x,
            "mid_y": mid_y,
        })

    if len(candidates) < 2:
        return False, 0.0, 0.0

    # 按角度分组（平行斜边）
    angle_groups: dict[float, list[dict]] = {}
    for c in candidates:
        # 找到最接近的角度组
        matched = False
        for key in list(angle_groups.keys()):
            if abs(c["angle"] - key) <= _PARALLEL_ANGLE_TOLERANCE:
                angle_groups[key].append(c)
                matched = True
                break
        if not matched:
            angle_groups[c["angle"]] = [c]

    # 在每个角度组中查找"反向对"
    for group in angle_groups.values():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                c1, c2 = group[i], group[j]
                # 检查垂直方向相反（一条上、一条下）
                if c1["delta"] * c2["delta"] >= 0:
                    # 同向（都是上或都是下）→ 不是阶梯对
                    continue
                # 检查空间距离
                dist = math.hypot(c1["mid_x"] - c2["mid_x"],
                                  c1["mid_y"] - c2["mid_y"])
                if dist <= max_pair_distance:
                    # 取四个相邻 H 边长度
                    h_lengths = [
                        edges[(c1["idx"] - 1) % m]["length"],
                        edges[(c1["idx"] + 1) % m]["length"],
                        edges[(c2["idx"] - 1) % m]["length"],
                        edges[(c2["idx"] + 1) % m]["length"],
                    ]
                    A = max(h_lengths)
                    B = min(h_lengths)
                    return True, A, B

    return False, 0.0, 0.0