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

GAP_X = 3.0
GAP_Y = 3.0


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


def _copy_expanded(msp, entity, dx: float, dy: float, only_layers=None) -> None:
    """Recursively copy an entity into the modelspace, exploding INSERTs.

    ZWCAD (and other lightweight CADs) open plain entity sheets more reliably
    than deep nested-block references, so each source drawing is flattened to
    base primitives (LINE/ARC/CIRCLE/TEXT/LWPOLYLINE/...) translated into its
    grid cell centre.  ``only_layers`` keeps only matching layer entities
    (used to trim the original plate to its Part/Bolt member views instead of
    the whole Tekla sheet frame).
    """
    if entity.dxftype() == "INSERT":
        for ve in entity.virtual_entities():
            _copy_expanded(msp, ve, dx, dy, only_layers)
        return
    if only_layers is not None and entity.dxf.layer not in only_layers:
        return
    copy = entity.copy()
    try:
        copy.translate(dx, dy, 0)
    except Exception:
        pass
    msp.add_entity(copy)


def _entity_extent_layers(doc, layers):
    """Explode the modelspace and return the bbox of entities on ``layers``.

    Used to size an original Tekla sheet by its member view geometry (Part /
    Bolt) instead of the whole paper frame, so rows pack tightly.
    """
    shapes = []

    def walk(entity):
        if entity.dxftype() == "INSERT":
            for ve in entity.virtual_entities():
                walk(ve)
            return
        if layers is not None and entity.dxf.layer not in layers:
            return
        shapes.append(entity)

    for e in doc.modelspace():
        walk(e)
    if not shapes:
        return None
    return bbox.extents(shapes)


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
    ap.add_argument("--chunk-size", type=int, default=0, help="pairs per output sheet (0=all in one)")
    ap.add_argument("--limit", type=int, default=0, help="only first N pairs (0=all)")
    args = ap.parse_args()

    results = json.load(open(args.manifest, encoding="utf-8"))
    auto = [r for r in results if r.get("automation_route") == "auto_accepted"]
    if args.limit:
        auto = auto[: args.limit]
    pairs = [(Path(r["input"]), Path(r["production_clean"])) for r in auto]
    print(f"pairs: {len(pairs)}")

    chunk_size = args.chunk_size or len(pairs)
    for ci, start in enumerate(range(0, len(pairs), chunk_size), start=1):
        chunk = pairs[start : start + chunk_size]
        out = args.output.with_name(
            f"{args.output.stem}_{ci:02d}{args.output.suffix}"
        )
        merge_pairs(chunk, out, args.pairs_per_row)
        print(f"chunk {ci}: {len(chunk)} pairs -> {out}")


def merge_pairs(pairs: list, output: Path, pairs_per_row: int) -> None:
    """Merge one chunk of [before, after] pairs into a single output sheet."""
    # Pass 1: read every drawing once to learn each cell extents.  The
    # original plate is sized by its member view geometry (Part/Bolt), not the
    # whole Tekla sheet frame, so rows pack tightly.
    cells = []  # (path, w, h, ext)
    for before, after in pairs:
        for p, is_before in ((before, True), (after, False)):
            doc = ezdxf.readfile(p)
            if is_before:
                # 原图按全部图层计算范围（本次合图原图所有图层都合入）
                ext = _entity_extent_layers(doc, None)
            else:
                ext = bbox.extents(doc.modelspace())
            if ext is None or not ext.has_data:
                w = h = 100.0
            else:
                w = ext.extmax[0] - ext.extmin[0]
                h = ext.extmax[1] - ext.extmin[1]
            cells.append((p, w, h, ext))
    # Per-row horizontal packing: each cell starts right after the previous
    # one (tiny GAP_X), vertically centred on the row; rows stack by their own
    # height.  Drawings of very different widths pack tightly instead of being
    # stretched to a common column width.
    n_cells = len(cells)
    n_cols = pairs_per_row * 2
    row_x: list[list[float]] = []
    row_cell_w: list[list[float]] = []
    row_h: list[float] = []
    for start in range(0, n_cells, n_cols):
        row_slice = cells[start : start + n_cols]
        xs: list[float] = []
        widths: list[float] = []
        acc = 0.0
        for _, w, _, _ in row_slice:
            xs.append(acc)
            acc += w + GAP_X
            widths.append(w)
        row_x.append(xs)
        row_cell_w.append(widths)
        row_h.append(max(h for _, _, h, _ in row_slice) + GAP_Y)
    row_y: list[float] = []
    acc = 0.0
    for h in row_h:
        row_y.append(-acc)
        acc += h

    main = ezdxf.new("R2000")
    main.units = ezdxf.units.MM
    msp = main.modelspace()

    for pi in range(len(pairs)):
        for ji in range(2):
            p, w, h, ext = cells[pi * 2 + ji]
            row = pi // pairs_per_row
            col = (pi % pairs_per_row) * 2 + ji
            x = row_x[row][col]
            y = row_y[row]
            ccx = x + row_cell_w[row][col] / 2.0
            ccy = y + row_h[row] / 2.0
            doc = ezdxf.readfile(p)
            _copy_styles(main, doc)
            if ext is not None and ext.has_data:
                bcx = (ext.extmin[0] + ext.extmax[0]) / 2.0
                bcy = (ext.extmin[1] + ext.extmax[1]) / 2.0
            else:
                bcx = bcy = 0.0
            dx = ccx - bcx
            dy = ccy - bcy
            # 原图与拆后图均合并全部图层
            only_layers = None
            for e in doc.modelspace():
                _copy_expanded(msp, e, dx, dy, only_layers)
        if pi % 20 == 0:
            print(f"  {pi+1}/{len(pairs)}")

    main.saveas(output)
    print(f"merged {len(pairs)} pairs -> {output}")


if __name__ == "__main__":
    main()
