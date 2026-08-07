"""独立核验 BOX 主视图腹板 + 俯视图上腹/下腹。

不经 box_reader 分析器，直接读 DXF 实体，重点核验腹板左右进。
用法: cd Stages/BOX左右进读取 && uv run python tools/verify_web_boxes.py
"""
from __future__ import annotations

import re
from collections import defaultdict
from math import cos, sin
from pathlib import Path

import ezdxf

BOX_SPEC = re.compile(
    r"\bBOX\s*(\d+)\s*[*xX×]\s*(\d+)\s*[*xX×]\s*(\d+)\s*[*xX×]\s*(\d+)\b", re.IGNORECASE
)

DXF_DIR = Path("/home/Creeken/Paper/CAD_research/complete_framework/BOX拆板前分类/BOX拆板前_BOX_dxf")
FILES = [
    "BYSJ@零件图@h-9-cb-72_拆板前.dxf",
    "BYSJ@零件图@h-9-cb-73_拆板前.dxf",
    "BYSJ@零件图@h-9-cb-279_拆板前.dxf",
    "BYSJ@零件图@h-9-cb-94_拆板前.dxf",
]


def read_blocks(path: Path):
    doc = ezdxf.readfile(path)
    insunits = int(doc.header.get("$INSUNITS", 0))
    blocks: dict[str, list] = {}
    for b in doc.blocks:
        if b.name.startswith("*") and "Model" not in b.name and "Paper" not in b.name:
            ents = [e for e in b if e.dxf.get("layer", "") == "Part"]
            if ents:
                blocks.setdefault(b.name, []).extend(ents)
    specs = []
    for b in doc.blocks:
        for e in b:
            if e.dxftype() in ("TEXT", "MTEXT"):
                m = BOX_SPEC.search(e.dxf.text)
                if m:
                    specs.append(tuple(int(g) for g in m.groups()))
    return insunits, blocks, specs[0] if specs else None


def point_list(e):
    kind = e.dxftype()
    if kind == "LINE":
        return [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
    if kind in ("LWPOLYLINE", "POLYLINE"):
        pts = []
        for v in e.vertices():
            pts.append((float(v[0]), float(v[1])))
        return pts
    if kind == "ARC":
        import math

        c = e.dxf.center
        r = e.dxf.radius
        a0, a1 = e.dxf.start_angle, e.dxf.end_angle
        s, en = math.radians(a0), math.radians(a1)
        pts = []
        steps = max(8, int(abs(en - s) / 0.13))
        for i in range(steps + 1):
            ang = s + (en - s) * i / steps
            pts.append((c.x + r * cos(ang), c.y + r * sin(ang)))
        return pts
    return []


def block_bbox(entities):
    xs, ys = [], []
    for e in entities:
        for p in point_list(e):
            xs.append(p[0])
            ys.append(p[1])
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def merged_hlines(entities):
    """Return [(y, x_lo, x_hi)] merged long horizontal lines."""
    by_y = defaultdict(list)
    for e in entities:
        pts = point_list(e)
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            if abs(a[1] - b[1]) > 0.5:
                continue
            if abs(a[0] - b[0]) < 10:
                continue
            by_y[round(0.5 * (a[1] + b[1]), 1)].append((min(a[0], b[0]), max(a[0], b[0])))
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
    return result


def clip_segment_to_band(a, b, y_lo, y_hi):
    """Clip line segment a-b to horizontal band [y_lo, y_hi]; return (xlo, xhi) or None."""
    ax, ay = a
    bx, by = b
    xs = []
    if ay == by:
        if y_lo <= ay <= y_hi:
            xs.extend((ax, bx))
        return (min(xs), max(xs)) if xs else None
    t_lo = (y_lo - ay) / (by - ay)
    t_hi = (y_hi - ay) / (by - ay)
    t1, t2 = sorted((t_lo, t_hi))
    t1 = max(0.0, t1)
    t2 = min(1.0, t2)
    if t2 > t1:
        xs.append(ax + t1 * (bx - ax))
        xs.append(ax + t2 * (bx - ax))
    return (min(xs), max(xs)) if xs else None


def main():
    for fname in FILES:
        path = DXF_DIR / fname
        insunits, blocks, spec = read_blocks(path)
        print(f"\n### {fname}")
        print(f"  $INSUNITS={insunits}  规格={spec}")
        if not spec:
            continue
        H, W, tw, tf = spec

        # 找主视图（高度≈H）与俯视图（高度≈W）
        front = top = None
        for name, ents in blocks.items():
            bb = block_bbox(ents)
            if not bb:
                continue
            h = bb[3] - bb[1]
            if front is None and abs(h - H) <= 2:
                front = (name, ents, bb)
            elif top is None and abs(h - W) <= max(2, 0.02 * W):
                top = (name, ents, bb)
        if front is None or top is None:
            print("  未找到主/俯视图")
            continue
        fname_b, fents, (fx1, fy1, fx2, fy2) = front
        tname_b, tents, (tx1, ty1, tx2, ty2) = top
        print(f"  主视图 {fname_b}: X[{fx1:.1f},{fx2:.1f}] Y[{fy1:.1f},{fy2:.1f}] h={fy2-fy1:.1f}")
        print(f"  俯视图 {tname_b}: X[{tx1:.1f},{tx2:.1f}] Y[{ty1:.1f},{ty2:.1f}] h={ty2-ty1:.1f}")

        # ---- 主视图：顶/底/内线骨架 ----
        hlines = merged_hlines(fents)
        top_line = max(hlines, key=lambda h: h[0])
        bottom_line = min(hlines, key=lambda h: h[0])
        upper_inner = min(hlines, key=lambda h: abs(h[0] - (top_line[0] - tf)))
        lower_inner = min(hlines, key=lambda h: abs(h[0] - (bottom_line[0] + tf)))
        top_fl = (min(top_line[1], upper_inner[1]), max(top_line[2], upper_inner[2]))
        bot_fl = (min(bottom_line[1], lower_inner[1]), max(bottom_line[2], lower_inner[2]))
        print(f"  主视图顶线 y={top_line[0]:.1f} X[{top_line[1]:.1f},{top_line[2]:.1f}] 内线 y={upper_inner[0]:.1f} X[{upper_inner[1]:.1f},{upper_inner[2]:.1f}]")
        print(f"  主视图底线 y={bottom_line[0]:.1f} X[{bottom_line[1]:.1f},{bottom_line[2]:.1f}] 内线 y={lower_inner[0]:.1f} X[{lower_inner[1]:.1f},{lower_inner[2]:.1f}]")

        # ---- 主视图腹板带 [底线+tf, 顶线-tf] ----
        web_lo = bottom_line[0] + tf
        web_hi = top_line[0] - tf
        web_span = web_hi - web_lo
        web_xs: list[float] = []
        web_clip_hits = []  # debug
        for e in fents:
            pts = point_list(e)
            for i in range(len(pts) - 1):
                a, b = pts[i], pts[i + 1]
                if abs(a[1] - b[1]) <= 0.5:
                    continue  # 水平线归翼板
                ey_lo, ey_hi = min(a[1], b[1]), max(a[1], b[1])
                if ey_lo > web_hi or ey_hi < web_lo:
                    continue
                span = min(ey_hi, web_hi) - max(ey_lo, web_lo)
                if span >= 0.30 * web_span:
                    clipped = clip_segment_to_band(a, b, web_lo, web_hi)
                    if clipped:
                        web_xs.extend(clipped)
                        web_clip_hits.append((round(clipped[0], 1), round(clipped[1], 1)))
        # 翼板内线端点
        web_xs.extend((upper_inner[1], upper_inner[2], lower_inner[1], lower_inner[2]))
        # 参考系 = 全部板的源边极值（与读取器一致）
        ref_lo = min(top_fl[0], bot_fl[0], min(web_xs))
        ref_hi = max(top_fl[1], bot_fl[1], max(web_xs))
        print(f"  主视图腹板带 y∈[{web_lo:.1f},{web_hi:.1f}] span={web_span:.1f}")
        print(f"    竖/斜线裁剪点: {sorted(set(web_clip_hits))}")
        w_lo, w_hi = min(web_xs), max(web_xs)
        print(f"  独立主视图腹板 X[{w_lo:.1f},{w_hi:.1f}] 左进={max(0,w_lo-ref_lo):.1f} 右进={max(0,ref_hi-w_hi):.1f}")
        print(f"  上翼板 X[{top_fl[0]:.1f},{top_fl[1]:.1f}] 左进={max(0,top_fl[0]-ref_lo):.1f} 右进={max(0,ref_hi-top_fl[1]):.1f}")
        print(f"  下翼板 X[{bot_fl[0]:.1f},{bot_fl[1]:.1f}] 左进={max(0,bot_fl[0]-ref_lo):.1f} 右进={max(0,ref_hi-bot_fl[1]):.1f}")

        # ---- 俯视图上腹(下侧条带)/下腹(上侧条带) ----
        shift = ref_lo - tx1
        v_lo, v_hi = ty1, ty2

        def top_band(ylo, yhi, label):
            xs: list[float] = []
            hits = []
            for e in tents:
                pts = point_list(e)
                for i in range(len(pts) - 1):
                    a, b = pts[i], pts[i + 1]
                    mid_y = 0.5 * (a[1] + b[1])
                    if not (ylo - 1.0 <= mid_y <= yhi + 1.0):
                        continue
                    if abs(a[1] - b[1]) <= 0.5:
                        xs.extend((a[0] + shift, b[0] + shift))
                        hits.append((round(min(a[0], b[0]) + shift, 1), round(max(a[0], b[0]) + shift, 1)))
            if not xs:
                return None, hits
            return (min(xs), max(xs)), hits

        upper_web, uhits = top_band(v_lo, v_lo + tw, "上腹")
        lower_web, lhits = top_band(v_hi - tw, v_hi, "下腹")
        print(f"  俯视图条带 下侧[底,底+tw]=[{v_lo:.1f},{v_lo+tw:.1f}] 上侧[顶-tw,顶]=[{v_hi-tw:.1f},{v_hi:.1f}]  shift={shift:.1f}")
        for label, res, hits in (("上腹(下条带)", upper_web, uhits), ("下腹(上条带)", lower_web, lhits)):
            if res is None:
                print(f"    {label}: 无水平线")
            else:
                lo, hi = res
                print(f"    {label}: X[{lo:.1f},{hi:.1f}] 左进={max(0,lo-ref_lo):.1f} 右进={max(0,ref_hi-hi):.1f}  水平线: {sorted(set(hits))}")


if __name__ == "__main__":
    main()
