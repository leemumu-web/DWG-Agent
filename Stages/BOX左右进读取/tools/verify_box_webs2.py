"""独立核验 BOX 俯视图上腹/下腹 —— 用条带内/外缘水平线分别测。

web 条带 = tw 厚：
  上腹(下侧条带) y∈[v_lo, v_lo+tw]  内缘线=y_lo+tw(板的真正内表面), 外缘=y_lo(箱体外表面,可能与翼缘共享)
  下腹(上侧条带) y∈[v_hi-tw, v_hi]  内缘线=y_hi-tw, 外缘=y_hi
主视图腹板：只用带内源边(不含翼板内线端点)，另列翼板内线端点影响。
"""
from __future__ import annotations

import re
import sys
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
        ents = [e for e in b if e.dxf.get("layer", "") == "Part"]
        if ents:
            blocks[b.name] = ents
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
                xs.append(v[0]); ys.append(v[1])
        elif e.dxftype() == "ARC":
            c, r = e.dxf.center, e.dxf.radius
            for a in (0, 90, 180, 270):
                xs.append(c.x + r); ys.append(c.y + r)
        elif e.dxftype() == "CIRCLE":
            c, r = e.dxf.center, e.dxf.radius
            xs += [c.x - r, c.x + r]; ys += [c.y - r, c.y + r]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def horizontal_lines(entities, tol=0.5, min_len=5.0):
    from collections import defaultdict
    by_y = defaultdict(list)
    for e in entities:
        if e.dxftype() != "LINE":
            continue
        s, en = e.dxf.start, e.dxf.end
        if abs(s.y - en.y) > tol:
            continue
        if abs(s.x - en.x) < min_len:
            continue
        by_y[round(0.5 * (s.y + en.y), 1)].append((min(s.x, en.x), max(s.x, en.x)))
    out = []
    for y, intervals in sorted(by_y.items()):
        ordered = sorted(intervals)
        merged = [list(ordered[0])]
        for a, b in ordered[1:]:
            if a <= merged[-1][1] + 1.0:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        for a, b in merged:
            out.append((y, a, b))
    return out


def front_web_edges(entities, bbox, tf):
    """主视图腹板带内非水平源边（不含翼板内线端点）。返回 (edges, flange_inner_端点)。"""
    x1, y1, x2, y2 = bbox
    hlines = horizontal_lines(entities)
    top = max(hlines, key=lambda h: h[0])
    bottom = min(hlines, key=lambda h: h[0])
    band_lo, band_hi = bottom[0] + tf, top[0] - tf
    web_span = band_hi - band_lo
    edges = []
    for e in entities:
        if e.dxftype() != "LINE":
            continue
        s, en = e.dxf.start, e.dxf.end
        if abs(s.y - en.y) <= 0.5:
            continue
        y_lo, y_hi = min(s.y, en.y), max(s.y, en.y)
        span = min(y_hi, band_hi) - max(y_lo, band_lo)
        if span < 0.30 * web_span:
            continue
        if y_lo < band_lo - 5.0 or y_hi > band_hi + 5.0:
            continue
        # clip x to band
        t1 = max(0.0, (band_lo - y_lo) / (y_hi - y_lo))
        t2 = min(1.0, (band_hi - y_lo) / (y_hi - y_lo))
        if t2 >= t1:
            edges.append((s.x + t1 * (en.x - s.x), s.x + t2 * (en.x - s.x)))
    inner_tol = max(2.0, 0.25 * tf)
    flange_inner = [(line[1], line[2]) for line in hlines
                    if abs(line[0] - band_lo) <= inner_tol or abs(line[0] - band_hi) <= inner_tol]
    return edges, flange_inner


def main(paths):
    for path in paths:
        p = Path(path)
        try:
            insunits, blocks, spec = read_parts(p)
        except Exception as exc:
            print(f"### {p.name}: READ ERROR {exc}")
            continue
        print(f"\n{'='*78}\n### {p.name}")
        if not spec:
            print("  无规格")
            continue
        H, W, tw, tf = spec
        print(f"  spec BOX{H}*{W}*{tw}*{tf}")
        front = top = None
        for name, ents in blocks.items():
            bb = block_bbox(ents)
            if not bb:
                continue
            h = bb[3] - bb[1]
            if abs(h - H) <= 2 and front is None:
                front = (name, ents, bb)
            elif abs(h - W) <= 2 and top is None:
                top = (name, ents, bb)
        if not front or not top:
            print("  缺主视图/俯视图")
            continue
        fname, fents, fbb = front
        tname, tents, tbb = top

        # 主视图腹板
        edges, flange_inner = front_web_edges(fents, fbb, tf)
        if edges:
            lo = min(min(a, b) for a, b in edges)
            hi = max(max(a, b) for a, b in edges)
            print(f"  主视图腹板带内源边: {sorted(set(round(v,1) for pair in edges for v in pair))}")
            print(f"    -> 源边边界 X[{lo:.1f},{hi:.1f}] 左进={max(0.0,lo-fbb[0]):.1f} 右进={max(0.0,fbb[2]-hi):.1f}")
        else:
            print("  主视图腹板带内无源边")
        print(f"  翼板内线端点: {sorted(set(round(v,1) for pair in flange_inner for v in pair))}")

        # 俯视图条带
        v_lo, v_hi = tbb[1], tbb[3]
        shift = fbb[0] - tbb[0]
        thlines = horizontal_lines(tents)
        print(f"  俯视图 y∈[{v_lo:.1f},{v_hi:.1f}]")
        for band_name, ylo, yhi in (("上腹(下侧)", v_lo, v_lo + tw), ("下腹(上侧)", v_hi - tw, v_hi)):
            lines = [ln for ln in thlines if ylo - 1.0 <= ln[0] <= yhi + 1.0]
            info = []
            for y, a, b in lines:
                tag = "外缘" if abs(y - ylo) <= 0.8 else ("内缘" if abs(y - yhi) <= 0.8 else "?")
                info.append(f"{tag} y={y:.1f} X[{a+shift:.1f},{b+shift:.1f}]")
            if info:
                ext_lo = min(ln[1] for ln in lines) + shift
                ext_hi = max(ln[2] for ln in lines) + shift
                print(f"  {band_name} y∈[{ylo:.1f},{yhi:.1f}] 水平线:")
                for s in info:
                    print(f"      {s}")
                print(f"    全部水平线并集 X[{ext_lo:.1f},{ext_hi:.1f}] 左进={max(0.0,ext_lo-fbb[0]):.1f} 右进={max(0.0,fbb[2]-ext_hi):.1f}")
                # 只取内缘线
                inner = [ln for ln in lines if abs(ln[0] - yhi) <= 0.8]
                if inner:
                    ilo = min(ln[1] for ln in inner) + shift
                    ihi = max(ln[2] for ln in inner) + shift
                    print(f"    [内缘仅] X[{ilo:.1f},{ihi:.1f}] 左进={max(0.0,ilo-fbb[0]):.1f} 右进={max(0.0,fbb[2]-ihi):.1f}")
            else:
                print(f"  {band_name} y∈[{ylo:.1f},{yhi:.1f}] 无水平线")


if __name__ == "__main__":
    main(sys.argv[1:])
