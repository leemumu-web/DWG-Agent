"""独立核验 BOX 腹板左右进：不依赖 box_reader 分析器。

直接从 DXF 实体几何出发：
  1. 定位主视图区域（h-4 系列为 *A2 块内 y∈[9900,10700] 的前视图；3t1 为 *A1 块）。
  2. 识别顶线/底线及翼板内线（内线距边 = tf），得到腹板带 [下内线, 上内线]。
  3. 在腹板带内收集非水平源边（竖线/斜线），裁剪到腹板带，取 X 极值 => 腹板左右边界。
  4. 主视图 X 参考 = 各板左右极值的并集（与读取器"板件源边极值"口径一致）。
  5. 输出 左进 = 板最左 - 主最左；右进 = 主最右 - 板最右。

用法：uv run python tools/verify_web_independent.py <dxf路径> [dxf路径...]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import ezdxf

BOX_SPEC = re.compile(
    r"\bBOX\s*(\d+)\s*[*xX×]\s*(\d+)\s*[*xX×]\s*(\d+)\s*[*xX×]\s*(\d+)\b", re.IGNORECASE
)


def collect_lines(doc, block_name: str):
    """Return list of (x1,y1,x2,y2) for LINE entities in a block."""
    lines = []
    block = doc.blocks.get(block_name)
    if block is None:
        return lines
    for e in block:
        if e.dxftype() != "LINE":
            continue
        s, en = e.dxf.start, e.dxf.end
        lines.append((s.x, s.y, en.x, en.y))
    return lines


def horizontal_lines(lines, min_len=10.0):
    """Merge horizontal lines by y; return [(y, x_lo, x_hi)]."""
    from collections import defaultdict

    by_y = defaultdict(list)
    for x1, y1, x2, y2 in lines:
        if abs(y1 - y2) > 0.5:
            continue
        if abs(x2 - x1) < min_len:
            continue
        by_y[round(0.5 * (y1 + y2), 1)].append((min(x1, x2), max(x1, x2)))
    result = []
    for y, intervals in by_y.items():
        ordered = sorted(intervals)
        merged = [list(ordered[0])]
        for a, b in ordered[1:]:
            if a <= merged[-1][1] + 1.0:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        for a, b in merged:
            result.append((y, a, b))
    return sorted(result)


def clip_line_to_band(x1, y1, x2, y2, y_lo, y_hi):
    """Clip a segment to a y-band; return (min_x, max_x) within band or None."""
    xs = []
    if y1 == y2:
        if y_lo <= y1 <= y_hi:
            xs.extend((x1, x2))
    else:
        t_lo = (y_lo - y1) / (y2 - y1)
        t_hi = (y_hi - y1) / (y2 - y1)
        t1, t2 = sorted((t_lo, t_hi))
        t1 = max(0.0, t1)
        t2 = min(1.0, t2)
        if t2 >= t1:
            xs.append(x1 + t1 * (x2 - x1))
            xs.append(x1 + t2 * (x2 - x1))
    return (min(xs), max(xs)) if xs else None


def web_extents_from_lines(lines, y_lo, y_hi, skel_lo, skel_hi, tf):
    """Collect non-horizontal source edges that belong to the web plate, clip to
    the web band, and return (min_x, max_x) or None.

    独立口径：
      - 腹板带内、X 中心靠近构件翼板骨架（长水平线并集）的非水平线视为腹板源边；
      - 越出腹板带过远、或 X 中心远在骨架之外（多视图块连到邻视图的连接线）
        视为外轮廓/过渡线，不计入腹板边界；
      - 翼板内线端点（腹板四角）单独并入。
    """
    band_span = y_hi - y_lo
    band_tol = 0.25 * band_span          # 仅排除明显整高外轮廓
    skel_margin = max(3.0 * tf, 80.0)    # 连接线可伸出的安全距离
    xs: list[float] = []
    for x1, y1, x2, y2 in lines:
        if abs(y1 - y2) <= 0.5:
            continue  # 水平表面线归翼板
        lo, hi = min(y1, y2), max(y1, y2)
        if lo > y_hi + band_tol or hi < y_lo - band_tol:
            continue
        if lo < y_lo - band_tol or hi > y_hi + band_tol:
            continue  # 越出腹板带过远 => 外轮廓/整高斜切，排除
        center_x = 0.5 * (x1 + x2)
        if not (skel_lo - skel_margin <= center_x <= skel_hi + skel_margin):
            continue  # X 中心在构件骨架外 => 连接线，排除
        span = min(hi, y_hi) - max(lo, y_lo)
        if span < 0.3 * band_span:
            continue
        clipped = clip_line_to_band(x1, y1, x2, y2, y_lo, y_hi)
        if clipped:
            xs.extend(clipped)
    return (min(xs), max(xs)) if xs else None


def analyze(path: Path):
    try:
        doc = ezdxf.readfile(path)
    except Exception as exc:
        print(f"### {path.name}: READ ERROR {exc}")
        return
    insunits = int(doc.header.get("$INSUNITS", 0))

    # 规格
    spec = None
    for b in doc.blocks:
        for e in b:
            if e.dxftype() in ("TEXT", "MTEXT"):
                m = BOX_SPEC.search(e.dxf.text)
                if m:
                    spec = tuple(int(g) for g in m.groups())
                    break
        if spec:
            break
    if not spec:
        print(f"### {path.name}: 无规格")
        return
    H, _W, _tw, tf = spec

    # 选择含主视图的块并确定主视图 y 带
    # 3t1: 整块高度≈H；h-4: *A2 块内 y∈[9900,10700] 的视图带
    front_block = None
    front_region = None  # (y_lo, y_hi)
    for blk in doc.blocks:
        name = blk.name
        if not (name.startswith("*") and "Model" not in name and "Paper" not in name):
            continue
        lines = collect_lines(doc, name)
        if not lines:
            continue
        xs = [v for ln in lines for v in (ln[0], ln[2])]
        ys = [v for ln in lines for v in (ln[1], ln[3])]
        if not xs:
            continue
        h = max(ys) - min(ys)
        if abs(h - H) <= 2:
            front_block = name
            front_region = (min(ys), max(ys))
            break
    if front_block is None:
        # 多视图块：用长水平线间距≈H 切分
        for blk in doc.blocks:
            name = blk.name
            if not (name.startswith("*") and "Model" not in name and "Paper" not in name):
                continue
            lines = collect_lines(doc, name)
            if not lines:
                continue
            hlines = horizontal_lines(lines)
            found = None
            for i, (y1, _a, _b) in enumerate(hlines):
                for y2, _c, _d in hlines[i + 1:]:
                    if abs(abs(y2 - y1) - H) <= 2:
                        found = (min(y1, y2), max(y1, y2))
                        break
                if found:
                    break
            if found:
                front_block = name
                front_region = found
                break
    if front_block is None:
        print(f"### {path.name}: 未定位主视图")
        return

    lines = collect_lines(doc, front_block)
    # 仅保留主视图 y 带内的线（h-4 多视图块过滤邻视图连接线）
    if front_region:
        yl, yh = front_region
        view_lines = [ln for ln in lines if min(ln[1], ln[3]) <= yh + 1 and max(ln[1], ln[3]) >= yl - 1]
    else:
        view_lines = lines
    hlines = horizontal_lines(view_lines)
    if not hlines:
        print(f"### {path.name}: 主视图无长水平线")
        return

    top = max(hlines, key=lambda h: h[0])
    bottom = min(hlines, key=lambda h: h[0])
    upper_inner = min(hlines, key=lambda h: abs(h[0] - (top[0] - tf)))
    lower_inner = min(hlines, key=lambda h: abs(h[0] - (bottom[0] + tf)))

    # 翼板 X 极值
    upper_lo = min(top[1], upper_inner[1])
    upper_hi = max(top[2], upper_inner[2])
    lower_lo = min(bottom[1], lower_inner[1])
    lower_hi = max(bottom[2], lower_inner[2])

    # 骨架 = 所有长水平线 X 并集（翼板材料区间），连接线判别基准
    skel_lo = min(line[1] for line in hlines)
    skel_hi = max(line[2] for line in hlines)

    # 腹板带
    web_y_lo = bottom[0] + tf
    web_y_hi = top[0] - tf
    web = web_extents_from_lines(view_lines, web_y_lo, web_y_hi, skel_lo, skel_hi, tf)
    # 翼板内线端点作为腹板四角并入（斜切端时腹板右上角 = 上翼内线右端）
    web_corner_lo = min(lower_inner[1], upper_inner[1])
    web_corner_hi = max(lower_inner[2], upper_inner[2])
    if web is None:
        web = (web_corner_lo, web_corner_hi)
    else:
        web = (min(web[0], web_corner_lo), max(web[1], web_corner_hi))

    # 主视图 X 参考 = 各板极值并集
    all_lo = min(upper_lo, lower_lo, web[0])
    all_hi = max(upper_hi, lower_hi, web[1])

    def offs(lo, hi):
        return (max(0.0, lo - all_lo), max(0.0, all_hi - hi))

    up_l, up_r = offs(upper_lo, upper_hi)
    lo_l, lo_r = offs(lower_lo, lower_hi)
    wb_l, wb_r = offs(web[0], web[1])

    print(f"### {path.name}  $INSUNITS={insunits} 规格={spec} 主视图块={front_block} 带={front_region}")
    print(f"  顶线 y={top[0]:.1f} X[{top[1]:.2f},{top[2]:.2f}]  内线 y={upper_inner[0]:.1f} X[{upper_inner[1]:.2f},{upper_inner[2]:.2f}]")
    print(f"  底线 y={bottom[0]:.1f} X[{bottom[1]:.2f},{bottom[2]:.2f}]  内线 y={lower_inner[0]:.1f} X[{lower_inner[1]:.2f},{lower_inner[2]:.2f}]")
    print(f"  腹板带 y∈[{web_y_lo:.1f},{web_y_hi:.1f}]  腹板源边 X[{web[0]:.2f},{web[1]:.2f}]")
    print(f"  上翼 X[{upper_lo:.2f},{upper_hi:.2f}] 左进={up_l:.2f} 右进={up_r:.2f}")
    print(f"  下翼 X[{lower_lo:.2f},{lower_hi:.2f}] 左进={lo_l:.2f} 右进={lo_r:.2f}")
    print(f"  腹板 X[{web[0]:.2f},{web[1]:.2f}] 左进={wb_l:.2f} 右进={wb_r:.2f}")
    print(f"  主视图 X参考 [{all_lo:.2f},{all_hi:.2f}]")


def main(paths):
    for p in paths:
        analyze(Path(p))


if __name__ == "__main__":
    main(sys.argv[1:])
