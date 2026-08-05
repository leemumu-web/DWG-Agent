#!/usr/bin/env python3
"""Merge before/after BH plate drawings into one sheet, N pairs per row.

Each pair is [original plate drawing, split plate drawing].  The entities of
every source drawing are copied 1:1 (no scaling) into a dedicated block, which
is then INSERTed onto a single output sheet laid out as ``pairs_per_row``
pairs per row.  Block definitions are copied recursively with per-source
prefixing so anonymous Tekla blocks (*Axx) stay unique.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import ezdxf
from ezdxf import bbox

GAP_X = 60.0
GAP_Y = 60.0


def _safe_block_name(raw: str) -> str:
    # "*" is reserved by DXF block names; anonymous Tekla blocks (*Axx) must
    # become plain names.  Keep the rest unchanged for readability.
    return raw.replace("*", "B_")


def _copy_styles(main: ezdxf.document.Drawing, src_doc: ezdxf.document.Drawing) -> None:
    """Copy source drawing text styles so Chinese TEXT/MTEXT keeps its font.

    A merged sheet that reuses a source style name without defining it falls
    back to the default SHX font, which renders Chinese as boxes.  Merge each
    missing style (font + bigfont) once; first source wins for duplicate names.
    """
    for s in src_doc.styles:
        name = s.dxf.name
        if name in main.styles:
            continue
        try:
            ns = main.styles.new(name=name)
            ns.dxf.font = s.dxf.get("font", "")
            ns.dxf.bigfont = s.dxf.get("bigfont", "")
        except Exception:
            continue


def copy_block_recursive(main: ezdxf.document.Drawing, target, entity, src_doc, prefix: str, renamed: dict) -> None:
    """Copy one entity into ``target`` (a block or modelspace), cloning any
    INSERT-referenced block definition under a source-prefixed unique name."""
    if entity.dxftype() == "INSERT":
        name = entity.dxf.name
        key = (prefix, name)
        new_name = renamed.get(key)
        if new_name is None and key not in renamed:
            src_block = src_doc.blocks.get(name)
            if src_block is not None and not src_block.name.startswith("*Model_Space"):
                new_name = f"{prefix}_{_safe_block_name(name)}"
                new_blk = main.blocks.new(name=new_name)
                for sub in src_block:
                    copy_block_recursive(main, new_blk, sub, src_doc, prefix, renamed)
            else:
                new_name = None
            renamed[key] = new_name
        if new_name is not None:
            copy = entity.copy()
            copy.dxf.name = new_name
            target.add_entity(copy)
        return
    target.add_entity(entity.copy())


def _translate_block(blk, dx: float, dy: float) -> None:
    for e in blk:
        if e.dxftype() == "INSERT":
            e.dxf.insert = (e.dxf.insert.x + dx, e.dxf.insert.y + dy)
        elif e.dxftype() in {"LINE", "ARC", "CIRCLE", "TEXT", "MTEXT", "LWPOLYLINE", "POINT", "SOLID", "3DFACE", "HATCH"}:
            try:
                e.translate(dx, dy, 0)
            except Exception:
                pass
        elif e.dxftype() == "ATTRIB":
            try:
                e.dxf.insert = (e.dxf.insert.x + dx, e.dxf.insert.y + dy)
            except Exception:
                pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("manifest", type=Path, help="batch CLI result JSON (list of summaries)")
    ap.add_argument("-o", "--output", type=Path, default=Path("merged_sheet.dxf"))
    ap.add_argument("--pairs-per-row", type=int, default=7, help="pairs per row")
    ap.add_argument("--limit", type=int, default=0, help="only first N pairs (0=all)")
    args = ap.parse_args()

    results = json.load(open(args.manifest, encoding="utf-8"))
    auto = [r for r in results if r.get("automation_route") == "auto_accepted"]
    if args.limit:
        auto = auto[: args.limit]
    pairs = [(Path(r["input"]), Path(r["production_clean"])) for r in auto]
    print(f"pairs: {len(pairs)}")

    # Pass 1: read every drawing once to learn each cell extents.
    cells = []  # (path, w, h)
    max_w = max_h = 0.0
    for before, after in pairs:
        for p in (before, after):
            doc = ezdxf.readfile(p)
            ext = bbox.extents(doc.modelspace())
            if not ext.has_data:
                w = h = 100.0
            else:
                w = ext.extmax[0] - ext.extmin[0]
                h = ext.extmax[1] - ext.extmin[1]
            cells.append((p, w, h))
            max_w = max(max_w, w)
            max_h = max(max_h, h)
    cell_w = max_w + GAP_X
    cell_h = max_h + GAP_Y

    main = ezdxf.new("R2010")
    main.units = ezdxf.units.MM
    msp = main.modelspace()
    renamed: dict = {}

    for pi in range(len(pairs)):
        for ji in range(2):
            p, w, h = cells[pi * 2 + ji]
            col = (pi % args.pairs_per_row) * 2 + ji
            row = pi // args.pairs_per_row
            x = col * cell_w
            y = -row * cell_h
            prefix = f"P{pi}"
            blk = main.blocks.new(name=f"{prefix}_IMG{ji}")
            doc = ezdxf.readfile(p)
            _copy_styles(main, doc)
            for e in doc.modelspace():
                copy_block_recursive(main, blk, e, doc, prefix, renamed)
            # Align the block's own bbox to the grid cell.
            bext = bbox.extents(blk)
            if bext.has_data:
                _translate_block(blk, x - bext.extmin[0], y - bext.extmin[1])
            msp.add_blockref(f"{prefix}_IMG{ji}", (x, y))
        if pi % 20 == 0:
            print(f"  {pi+1}/{len(pairs)}")

    main.saveas(args.output)
    print(f"merged {len(pairs)} pairs -> {args.output}")


if __name__ == "__main__":
    main()
