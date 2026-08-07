"""独立核验 BOX 主视图腹板 + 俯视图上腹/下腹。

不从 box_reader 走，直接用 ezdxf 读实体：
- 主视图(高度≈H)：web 带 y∈[底线+tf, 顶线-tf] 内非水平源边，裁剪到带，排除贯穿翼板带的
  轮廓斜切线；加上/下翼内线端点。
- 俯视图(高度≈W)：下侧/上侧条带(y∈[v_lo,v_lo+tw] / [v_hi-tw,v_hi])内水平线端点 X，对齐主视图。

用法：uv run python tools/verify_box_webs.py <dxf>...
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
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


def horizontal_lines(entities, tol=0.5, min_len=10.0):
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


def web_from_front_view(entities, bbox, tf):
    x1, y1, x2, y2 = bbox
    hlines = horizontal_lines(entities)
    if not hlines:
        return None, hlines
    top = max(hlines, key=lambda h: h[0])
    bottom = min(hlines, key=lambda h: h[0])
    band_lo = bottom[0] + tf
    band_hi = top[0] - tf
    web_span = band_hi - band_lo

    # 非水平源边，裁剪到带，排除贯穿翼板带(轮廓斜切)的线
    web_x = []
    band_hits = []
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
        # 排除延伸到翼板带内的轮廓斜切线（不是腹板自身的端）
        if y_lo < band_lo - 5.0 or y_hi > band_hi + 5.0:
            band_hits.append(("skip-outer", e, y_lo, y_hi))
            continue
        # 裁剪到带
        clipped_x = []
        if y_lo == y_hi:
            continue
        t1 = max(0.0, (band_lo - y_lo) / (y_hi - y_lo))
        t2 = min(1.0, (band_hi - y_lo) / (y_hi - y_lo))
        if t2 >= t1:
            clipped_x = [s.x + t1 * (en.x - s.x), s.x + t2 * (en.x - s.x)]
        web_x.extend(clipped_x)
        band_hits.append(("in-band", e, y_lo, y_hi))
    # 加上/下翼内线端点（腹板四角）
    inner_tol = max(2.0, 0.25 * tf)
    inner_lines = []
    for line in hlines:
        if abs(line[0] - band_lo) <= inner_tol or abs(line[0] - band_hi) <= inner_tol:
            inner_lines.append(line)
            web_x.extend((line[1], line[2]))
    if not web_x:
        return None, hlines
    return (min(web_x), max(web_x)), (hlines, top, bottom, band_lo, band_hi, inner_lines, band_hits)


def webs_from_top_view(entities, bbox, tw, shift):
    """俯视图下侧/上侧条带水平线端点 X（对齐主视图坐标）。"""
    x1, y1, x2, y2 = bbox
    hlines = horizontal_lines(entities)
    bands = {
        "下侧条带(上腹)": (y1, y1 + tw),
        "上侧条带(下腹)": (y2 - tw, y2),
    }
    result = {}
    for name, (ylo, yhi) in bands.items():
        xs = []
        for line in hlines:
            if ylo - 1.0 <= line[0] <= yhi + 1.0:
                xs.append(line[1] + shift)
                xs.append(line[2] + shift)
        if xs:
            result[name] = (min(xs), max(xs), (ylo, yhi))
    return result, hlines


def main(paths):
    for path in paths:
        p = Path(path)
        try:
            insunits, blocks, spec = read_parts(p)
        except Exception as exc:
            print(f"### {p.name}: READ ERROR {exc}")
            continue
        print(f"\n{'='*78}\n### {p.name}")
        print(f"  $INSUNITS={insunits}  规格={spec}")
        if not spec:
            print("  无规格")
            continue
        H, W, tw, tf = spec
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
        if front is None:
            print("  未找到主视图")
            continue
        fname, fents, fbb = front
        print(f"  主视图 {fname} X∈[{fbb[0]:.1f},{fbb[2]:.1f}] Y∈[{fbb[1]:.1f},{fbb[3]:.1f}] h={fbb[3]-fbb[1]:.1f}")

        wres = web_from_front_view(fents, fbb, tf)
        if wres is None:
            print("  主视图腹板带无源边 -> 无结果")
        else:
            (w_lo, w_hi), detail = wres
            hlines, top_line, bottom_line, band_lo, band_hi, inner_lines, band_hits = detail
            print(f"  顶线 y={top_line[0]:.1f} X∈[{top_line[1]:.1f},{top_line[2]:.1f}]")
            print(f"  底线 y={bottom_line[0]:.1f} X∈[{bottom_line[1]:.1f},{bottom_line[2]:.1f}]")
            print(f"  腹板带 y∈[{band_lo:.1f},{band_hi:.1f}]")
            print("  腹板带内源边:")
            for tag, e, yl, yh in band_hits:
                s, en = e.dxf.start, e.dxf.end
                print(f"    [{tag:10s}] ({s.x:9.2f},{s.y:9.2f})->({en.x:9.2f},{en.y:9.2f}) y∈[{yl:7.1f},{yh:7.1f}]")
            print("  翼板内线端点行:")
            for line in inner_lines:
                print(f"    y={line[0]:.1f} X∈[{line[1]:.1f},{line[2]:.1f}]")
            print(f"  独立主视图腹板 X∈[{w_lo:.1f},{w_hi:.1f}]  左进={max(0.0, w_lo-fbb[0]):.1f}  右进={max(0.0, fbb[2]-w_hi):.1f}")

        if top is None:
            print("  未找到俯视图")
            continue
        tname, tents, tbb = top
        print(f"  俯视图 {tname} X∈[{tbb[0]:.1f},{tbb[2]:.1f}] Y∈[{tbb[1]:.1f},{tbb[3]:.1f}] h={tbb[3]-tbb[1]:.1f}")
        shift = fbb[0] - tbb[0]
        tres, thlines = webs_from_top_view(tents, tbb, tw, shift)
        print("  俯视图水平线 (对齐主视图 X):")
        for line in sorted(thlines):
            print(f"    y={line[0]:.1f} X∈[{line[1]+shift:.1f},{line[2]+shift:.1f}]")
        for name, (lo, hi, band) in tres.items():
            print(f"  {name} y∈[{band[0]:.1f},{band[1]:.1f}] X∈[{lo:.1f},{hi:.1f}]  左进={max(0.0, lo-fbb[0]):.1f}  右进={max(0.0, fbb[2]-hi):.1f}")


if __name__ == "__main__":
    main(sys.argv[1:])
