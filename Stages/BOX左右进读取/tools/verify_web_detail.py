"""独立核验 BOX 腹板左右进：直接从 DXF 主视图几何逐条分析。

重点：主视图 y∈[底线+tf, 顶线-tf] 腹板带内，非水平竖板线/斜切线的 X 边界
（裁剪到带，排除超出腹板带的轮廓斜切线），加上/下翼内线端点。
"""
from __future__ import annotations

import re
import sys
from math import cos, sin
from pathlib import Path

import ezdxf

BOX_SPEC = re.compile(
    r"\bBOX\s*(\d+)\s*[*xX×]\s*(\d+)\s*[*xX×]\s*(\d+)\s*[*xX×]\s*(\d+)\b", re.IGNORECASE
)


def read_parts(path: Path):
    doc = ezdxf.readfile(path)
    insunits = int(doc.header.get("$INSUNITS", 0))
    blocks = {}
    for b in doc.blocks:
        if not (b.name.startswith("*") and "Model" not in b.name and "Paper" not in b.name):
            continue
        ents = []
        for e in b:
            if e.dxf.get("layer", "") != "Part":
                continue
            ents.append(e)
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


def block_bbox(entities):
    xs, ys = [], []
    for e in entities:
        if e.dxftype() == "LINE":
            xs += [e.dxf.start.x, e.dxf.end.x]
            ys += [e.dxf.start.y, e.dxf.end.y]
        elif e.dxftype() in ("LWPOLYLINE", "POLYLINE"):
            for v in e.vertices():
                xs.append(v[0])
                ys.append(v[1])
        elif e.dxftype() == "ARC":
            c = e.dxf.center
            r = e.dxf.radius
            for angle in (0, 90, 180, 270):
                xs.append(c.x + r * cos(angle))
                ys.append(c.y + r * sin(angle))
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def long_horizontal_lines(entities, tol=0.5):
    from collections import defaultdict

    by_y = defaultdict(list)
    for e in entities:
        if e.dxftype() != "LINE":
            continue
        s, en = e.dxf.start, e.dxf.end
        if abs(s.y - en.y) > tol:
            continue
        if abs(s.x - en.x) < 10:
            continue
        by_y[round(0.5 * (s.y + en.y), 1)].append((min(s.x, en.x), max(s.x, en.x)))
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


def line_segments(entities):
    """Return all LINE and polyline edge segments (a,b)."""
    segs = []
    for e in entities:
        if e.dxftype() == "LINE":
            segs.append((e.dxf.start, e.dxf.end))
        elif e.dxftype() == "LWPOLYLINE":
            pts = list(e.get_points("xy"))
            for i in range(len(pts) - 1):
                segs.append(((pts[i][0], pts[i][1]), (pts[i + 1][0], pts[i + 1][1])))
        elif e.dxftype() == "POLYLINE":
            pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices()]
            for i in range(len(pts) - 1):
                segs.append((pts[i], pts[i + 1]))
    return segs


def clip_seg_to_yband(a, b, y_lo, y_hi):
    """Clip segment (a,b) to y-band, return (x1,x2) or None."""
    ax, ay = a[0], a[1]
    bx, by = b[0], b[1]
    if ay == by:
        if y_lo <= ay <= y_hi:
            return (min(ax, bx), max(ax, bx))
        return None
    t_lo = (y_lo - ay) / (by - ay)
    t_hi = (y_hi - ay) / (by - ay)
    t1, t2 = sorted((t_lo, t_hi))
    t1 = max(0.0, t1)
    t2 = min(1.0, t2)
    if t2 >= t1:
        x1 = ax + t1 * (bx - ax)
        x2 = ax + t2 * (bx - ax)
        return (min(x1, x2), max(x1, x2))
    return None


def analyze(path: Path):
    print(f"### {path.name}")
    _insunits, blocks, spec = read_parts(path)
    if not spec:
        print("  无规格")
        return
    H, W, tw, tf = spec
    print(f"  spec BOX{H}*{W}*{tw}*{tf}  (tf={tf} tw={tw})")

    # Find front (h≈H) and top (h≈W) views
    front = top = None
    for name, ents in blocks.items():
        bb = block_bbox(ents)
        if not bb:
            continue
        h = bb[3] - bb[1]
        if abs(h - H) <= 2:
            front = (name, ents, bb)
        elif abs(h - W) <= 2:
            top = (name, ents, bb)
    if front is None:
        print("  未找到主视图")
        return
    fname, fents, (fx1, fy1, fx2, fy2) = front
    print(f"  主视图 {fname}: X∈[{fx1:.2f},{fx2:.2f}] Y∈[{fy1:.2f},{fy2:.2f}] h={fy2-fy1:.1f}")
    hlines = long_horizontal_lines(fents)
    if not hlines:
        print("  无长水平线")
        return
    top_line = max(hlines, key=lambda h: h[0])
    bottom_line = min(hlines, key=lambda h: h[0])
    print(f"  顶线 y={top_line[0]:.2f} X∈[{top_line[1]:.2f},{top_line[2]:.2f}]")
    print(f"  底线 y={bottom_line[0]:.2f} X∈[{bottom_line[1]:.2f},{bottom_line[2]:.2f}]")
    upper_inner = min(hlines, key=lambda h: abs(h[0] - (top_line[0] - tf)))
    lower_inner = min(hlines, key=lambda h: abs(h[0] - (bottom_line[0] + tf)))
    print(f"  上翼内线 y={upper_inner[0]:.2f} X∈[{upper_inner[1]:.2f},{upper_inner[2]:.2f}] 期望 {top_line[0]-tf:.2f}")
    print(f"  下翼内线 y={lower_inner[0]:.2f} X∈[{lower_inner[1]:.2f},{lower_inner[2]:.2f}] 期望 {bottom_line[0]+tf:.2f}")

    # ---- Web band analysis ----
    web_lo = bottom_line[0] + tf
    web_hi = top_line[0] - tf
    print(f"  腹板带 y∈[{web_lo:.2f},{web_hi:.2f}]")
    band_tol = max(2.0, 0.25 * tf)
    segs = line_segments(fents)
    web_hits = []  # non-horizontal segs whose interior stays within band
    web_clip_hits = []  # all non-horizontal segs clipped to band
    for (a, b) in segs:
        ay, by = a[1], b[1]
        if abs(ay - by) <= 0.5:
            continue  # horizontal
        ymin, ymax = min(ay, by), max(ay, by)
        clipped = clip_seg_to_yband(a, b, web_lo, web_hi)
        if clipped is None:
            continue
        # overlap with band
        overlap = min(ymax, web_hi) - max(ymin, web_lo)
        if overlap <= 0:
            continue
        web_clip_hits.append((a, b, clipped, overlap))
        # "stays inside band" = the seg endpoints are within (or touching) band
        if ymin >= web_lo - band_tol and ymax <= web_hi + band_tol:
            web_hits.append((a, b, clipped, overlap))

    print(f"  -- 完全落在腹板带内的非水平线段 ({len(web_hits)}) --")
    for a, b, clip, ov in sorted(web_hits, key=lambda t: (t[2][0], t[2][1])):
        print(f"    线 {a} -> {b}  带内X∈[{clip[0]:.2f},{clip[1]:.2f}] overlap={ov:.1f}")
    print(f"  -- 与腹板带有重叠的非水平线段（含超出带斜线，裁剪到带）({len(web_clip_hits)}) --")
    for a, b, clip, ov in sorted(web_clip_hits, key=lambda t: (t[2][0], t[2][1])):
        flag = "带内" if any(a == h[0] and b == h[1] for h in web_hits) else "超带"
        print(f"    [{flag}] 线 {a} -> {b}  带内X∈[{clip[0]:.2f},{clip[1]:.2f}] overlap={ov:.1f}")

    # ---- Flange inner line endpoints as web corners ----
    print(f"  上翼内线端点 X: [{upper_inner[1]:.2f}, {upper_inner[2]:.2f}]")
    print(f"  下翼内线端点 X: [{lower_inner[1]:.2f}, {lower_inner[2]:.2f}]")

    # Web boundary candidates: clipped X of band-confined hits + inner line endpoints
    web_x_vals = []
    for _a, _b, clip, _ov in web_hits:
        web_x_vals.extend(clip)
    web_x_vals.extend((upper_inner[1], upper_inner[2], lower_inner[1], lower_inner[2]))
    if web_x_vals:
        w_lo, w_hi = min(web_x_vals), max(web_x_vals)
        print(f"  ==> 腹板(带内线裁剪+翼内线端点) X∈[{w_lo:.2f},{w_hi:.2f}]")
        print(f"      左进(相对主视图X左端{fx1:.2f})={max(0, w_lo - fx1):.2f}  右进(相对主视图X右端{fx2:.2f})={max(0, fx2 - w_hi):.2f}")

    # ---- Top view web bands ----
    if top:
        tname, tents, (tx1, ty1, tx2, ty2) = top
        print(f"  俯视图 {tname}: X∈[{tx1:.2f},{tx2:.2f}] Y∈[{ty1:.2f},{ty2:.2f}] h={ty2-ty1:.1f}")
        thlines = long_horizontal_lines(tents)
        ttop = max(thlines, key=lambda h: h[0]) if thlines else None
        tbot = min(thlines, key=lambda h: h[0]) if thlines else None
        if ttop and tbot:
            print(f"  俯视顶线 y={ttop[0]:.2f} X∈[{ttop[1]:.2f},{ttop[2]:.2f}]")
            print(f"  俯视底线 y={tbot[0]:.2f} X∈[{tbot[1]:.2f},{tbot[2]:.2f}]")
            # lower band [tbot, tbot+tw], upper band [ttop-tw, ttop]
            for label, lo, hi in (
                ("下腹带", tbot[0], tbot[0] + tw),
                ("上腹带", ttop[0] - tw, ttop[0]),
            ):
                xs = []
                for e in tents:
                    if e.dxftype() == "LINE":
                        s, en = e.dxf.start, e.dxf.end
                        if abs(s.y - en.y) > 0.5:
                            continue
                        midy = 0.5 * (s.y + en.y)
                        if lo - 1.0 <= midy <= hi + 1.0:
                            xs.extend((s.x, en.x))
                    elif e.dxftype() == "LWPOLYLINE":
                        pts = list(e.get_points("xy"))
                        for i in range(len(pts) - 1):
                            ay, by = pts[i][1], pts[i + 1][1]
                            if abs(ay - by) > 0.5:
                                continue
                            midy = 0.5 * (ay + by)
                            if lo - 1.0 <= midy <= hi + 1.0:
                                xs.extend((pts[i][0], pts[i + 1][0]))
                if xs:
                    xlo, xhi = min(xs), max(xs)
                    print(f"  {label} y∈[{lo:.1f},{hi:.1f}] 水平线端点 X∈[{xlo:.2f},{xhi:.2f}] 对齐主视图(shift {fx1-tx1:+.2f}) -> [{xlo + fx1 - tx1:.2f},{xhi + fx1 - tx1:.2f}]")
                else:
                    print(f"  {label} y∈[{lo:.1f},{hi:.1f}] 无水平线")
        else:
            print("  俯视图无长水平线")
    else:
        print("  未找到俯视图")
    print()


def main(paths):
    for p in paths:
        analyze(Path(p))


if __name__ == "__main__":
    main(sys.argv[1:])
