"""独立核验工具：不经 box_reader 分析器，直接从 DXF 实体识别 BOX 主视图板件边界。

供子 agent 逐图交叉核验左右进。用法：
    cd Stages/BOX左右进读取 && uv run python tools/verify_independent.py <dxf路径> [dxf路径...]
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
        for e in b:
            if e.dxf.get("layer", "") != "Part":
                continue
            blocks.setdefault(b.name, []).append(e)
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
        elif e.dxftype() == "CIRCLE":
            c = e.dxf.center
            xs += [c.x - e.dxf.radius, c.x + e.dxf.radius]
            ys += [c.y - e.dxf.radius, c.y + e.dxf.radius]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def long_horizontal_lines(entities):
    """Return [(y, x_lo, x_hi)] merged long horizontal lines."""
    from collections import defaultdict

    by_y = defaultdict(list)
    for e in entities:
        if e.dxftype() != "LINE":
            continue
        s, en = e.dxf.start, e.dxf.end
        if abs(s.y - en.y) > 0.5:
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


def main(paths):
    for path in paths:
        p = Path(path)
        try:
            insunits, blocks, spec = read_parts(p)
        except Exception as exc:
            print(f"### {p.name}: READ ERROR {exc}")
            continue
        print(f"### {p.name}")
        print(f"  $INSUNITS={insunits}  规格={spec}")
        if not spec:
            print("  无规格")
            continue
        H, _width, _tw, n4 = spec
        # 主视图 = 高度≈H 的块
        front = None
        for name, ents in blocks.items():
            bb = block_bbox(ents)
            if not bb:
                continue
            h = bb[3] - bb[1]
            if abs(h - H) <= 2:
                front = (name, ents, bb)
                break
        if front is None:
            print("  未找到主视图")
            continue
        name, ents, (x1, y1, x2, y2) = front
        print(f"  主视图 {name}: X∈[{x1:.1f},{x2:.1f}] Y∈[{y1:.1f},{y2:.1f}] h={y2-y1:.1f}")
        hlines = long_horizontal_lines(ents)
        if not hlines:
            print("  无长水平线")
            continue
        top = max(hlines, key=lambda h: h[0])
        bottom = min(hlines, key=lambda h: h[0])
        tf = n4  # 主视图内线距边 = 翼板厚
        upper_inner = min(hlines, key=lambda h: abs(h[0] - (top[0] - tf)))
        lower_inner = min(hlines, key=lambda h: abs(h[0] - (bottom[0] + tf)))
        print(f"  顶线 y={top[0]:.1f} X∈[{top[1]:.1f},{top[2]:.1f}]")
        print(f"  上翼内线 y={upper_inner[0]:.1f} X∈[{upper_inner[1]:.1f},{upper_inner[2]:.1f}] (期望 {top[0]-tf:.1f})")
        print(f"  下翼内线 y={lower_inner[0]:.1f} X∈[{lower_inner[1]:.1f},{lower_inner[2]:.1f}] (期望 {bottom[0]+tf:.1f})")
        print(f"  底线 y={bottom[0]:.1f} X∈[{bottom[1]:.1f},{bottom[2]:.1f}]")
        # 板件边界（相对主视图最左/最右 X）
        upper_lo = min(top[1], upper_inner[1])
        upper_hi = max(top[2], upper_inner[2])
        lower_lo = min(bottom[1], lower_inner[1])
        lower_hi = max(bottom[2], lower_inner[2])
        print(f"  上翼板 X∈[{upper_lo:.1f},{upper_hi:.1f}] 左进={max(0,upper_lo-x1):.1f} 右进={max(0,x2-upper_hi):.1f}")
        print(f"  下翼板 X∈[{lower_lo:.1f},{lower_hi:.1f}] 左进={max(0,lower_lo-x1):.1f} 右进={max(0,x2-lower_hi):.1f}")
        # 腹板：中间竖板带内线的 X 范围
        web_lo = max(bottom[0] + tf, y1)
        web_hi = min(top[0] - tf, y2)
        web_x = []
        for e in ents:
            if e.dxftype() != "LINE":
                continue
            s, en = e.dxf.start, e.dxf.end
            ey_lo, ey_hi = min(s.y, en.y), max(s.y, en.y)
            if ey_lo > web_hi or ey_hi < web_lo:
                continue
            if abs(s.y - en.y) <= 0.5:
                continue  # 水平表面线已归翼板
            span = min(ey_hi, web_hi) - max(ey_lo, web_lo)
            if span >= 0.30 * (web_hi - web_lo):
                web_x.extend((s.x, en.x))
        if web_x:
            w_lo, w_hi = min(web_x), max(web_x)
            print(f"  腹板   X∈[{w_lo:.1f},{w_hi:.1f}] 左进={max(0,w_lo-x1):.1f} 右进={max(0,x2-w_hi):.1f}")
        print(f"  主视图全长 X∈[{x1:.1f},{x2:.1f}] len={x2-x1:.1f}")


if __name__ == "__main__":
    main(sys.argv[1:])
