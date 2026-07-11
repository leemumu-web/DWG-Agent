"""Step 0-1: Load .xls (TSV or space-delimited), detect encoding, find header,
create workbook.

Produces an openpyxl Workbook with two sheets:
  - 原表 (preserved original, cleaned)
  - 整理表 (working copy)
"""

from __future__ import annotations

import logging
import re as _re
from pathlib import Path

import pandas as pd
import openpyxl

from config import KW_批次, KW_构件编号, KW_零件号, KW_数量, KW_材质
from utils import safe_str, remove_all_spaces

log = logging.getLogger(__name__)

# Keywords for detecting steel-table content (encoding confirmation)
_CONTENT_KWS = ["构件编号", "零件", "规格", "长度", "材质", "数量", "型材", "型 材"]


def _try_read(input_file: Path, sep: str, enc: str) -> pd.DataFrame | None:
    """Try reading with given separator and encoding. Returns None on failure."""
    try:
        engine = "python" if sep == r"\s+" else "c"
        df = pd.read_csv(
            input_file, sep=sep, encoding=enc, header=None, dtype=str,
            engine=engine,
        )
        if df.shape[1] > 2:
            sample = " ".join(
                str(df.iloc[min(i, len(df) - 1), j])
                for i in range(min(10, len(df)))
                for j in range(min(17, len(df.columns)))
            )
            if any(kw in sample for kw in _CONTENT_KWS):
                return df
    except (UnicodeDecodeError, UnicodeError, pd.errors.ParserError):
        pass
    return None


def _merge_split_headers(headers: list[str]) -> list[str]:
    """Merge adjacent single-CJK-char headers split by whitespace separator.

    E.g. ["型","材","构件名称","材","质"] → ["型材","构件名称","材质"]
    """
    _CJK = _re.compile(r"^[一-鿿]$")
    merged = []
    skip = False
    for i, h in enumerate(headers):
        if skip:
            skip = False
            continue
        if _CJK.match(h) and i + 1 < len(headers) and _CJK.match(headers[i + 1]):
            merged.append(h + headers[i + 1])
            skip = True
        else:
            merged.append(h)
    return merged


def step_0_1_load_and_clean(input_file: Path, output_file: Path):
    """Load the .xls, convert to .xlsx, apply Steps 0-1.

    Returns (workbook, ws_整理表).
    """
    log.info("Loading %s ...", input_file)

    encodings = ["gbk", "gb2312", "gb18030", "utf-8", "latin-1"]

    # ---- Try tab-separated first (standard Tekla TSV) ----
    raw_df = None
    sep = None
    for enc in encodings:
        raw_df = _try_read(input_file, "\t", enc)
        if raw_df is not None:
            sep = "\t"
            log.info("  Detected: encoding=%s, tab-separated, %d cols.", enc, raw_df.shape[1])
            break

    # ---- Fallback: space-delimited ----
    if raw_df is None:
        for enc in encodings:
            raw_df = _try_read(input_file, r"\s+", enc)
            if raw_df is not None:
                sep = r"\s+"
                log.info("  Detected: encoding=%s, whitespace-separated, %d cols.",
                         enc, raw_df.shape[1])
                break

    # ---- Fallback: real Excel file (.xls/.xlsx binary) ----
    if raw_df is None:
        try:
            raw_df = pd.read_excel(input_file, header=None, dtype=str)
            if raw_df.shape[1] > 2:
                sep = "excel"
                log.info("  Detected: real Excel file, %d cols.", raw_df.shape[1])
        except Exception:
            pass

    if raw_df is None:
        raise ValueError(f"Cannot decode {input_file} with any known encoding/separator.")

    # ---- Detect header row by keyword scoring ----
    keywords = [KW_批次, KW_构件编号, KW_零件号, KW_数量, KW_材质,
                "零件编号", "型材", "型 材", "构件名称"]
    best_row, best_score = 0, 0
    for i in range(min(15, len(raw_df))):
        row_text = " ".join(
            safe_str(raw_df.iloc[i, j]) for j in range(min(17, len(raw_df.columns)))
        )
        score = sum(1 for kw in keywords if kw in row_text)
        if score > best_score:
            best_score = score
            best_row = i
    if best_score >= 2:
        header_row = best_row
    else:
        header_row = 5  # fallback
    log.info("  Detected header at row %d (score=%d/%d).", header_row, best_score, len(keywords))

    # ---- Extract headers ----
    headers = [safe_str(raw_df.iloc[header_row, j]) for j in range(len(raw_df.columns))]
    # Strip unit suffixes like (mm), (kg), (m2)
    headers = [_re.sub(r"\([^)]*\)", "", h).strip() for h in headers]
    # Normalize space-embedded headers: "型 材"→"型材", "材 质"→"材质", "备 注"→"备注"
    headers = [h.replace(" ", "") for h in headers]
    # Merge split single-char CJK headers (whitespace separator artifact)
    headers = _merge_split_headers(headers)

    # ---- Extract data rows ----
    data_rows = []
    for i in range(header_row + 1, len(raw_df)):
        row_vals = [raw_df.iloc[i, j] for j in range(len(raw_df.columns))]
        row_vals = [None if pd.isna(v) else v for v in row_vals]
        data_rows.append(row_vals)

    # ---- Normalize row structure for space-delimited format ----
    # In space-delimited files, col 0 is either 构件编号 (component rows)
    # or 零件编号 (part rows).  Normalize so that:
    #   component rows: col 0=构件编号, col 1=empty(零件号), col 2=型材, ...
    #   part rows:      col 0=empty(构件编号), col 1=零件号, col 2=型材, ...
    import re as _re2
    _PART_NO_RE2 = _re2.compile(
        r"^(\d+[A-Za-z]+-\d+.*|M\d+.*|[a-z]\d+-[a-z]-\d+.*|"
        r"(?!SKG-)[A-Z]{2,5}-\d+.*)$"
    )
    # Material-grade pattern for fallback row-type detection:
    #   starts with Q+3digits (Q345GJB, Q355B, etc.) OR is purely numeric
    #   (bolt grades like "60", non-PL specs like "48").  Must NOT match
    #   strings like "2GL" (构件名称) which start with a digit but contain
    #   letters — those are component names, not materials.
    _MAT_GRADE_RE = _re2.compile(r"^(Q\d{3})")  # Q345, Q355, etc.
    if sep == r"\s+" and len(headers) >= 3:
        norm_data = []
        norm_count = 0
        for row_vals in data_rows:
            c0 = safe_str(row_vals[0]) if len(row_vals) > 0 else ""
            if not c0:
                norm_data.append(row_vals)
                continue

            is_part = bool(_PART_NO_RE2.match(c0.strip()))

            # Fallback: if regex doesn't match, check col 2 for material pattern.
            # In space-delimited files with split-CJK headers (型 材, 材 质),
            # part rows have material (Q345GJB, Q355B) at col 2, component
            # rows have 构件名称 (2GL, albl_Top_f, WGL-, etc.) at col 2.
            if not is_part and len(row_vals) > 2:
                c2 = safe_str(row_vals[2])
                if _MAT_GRADE_RE.match(c2):
                    is_part = True
                elif c2 and c2.replace('.', '', 1).replace('-', '').isdigit():
                    # Pure numeric (or decimal) — bolt grade / non-PL spec
                    # e.g. "48", "0.3", "10.9" but NOT "2GL", "WGL-"
                    is_part = True

            if is_part:
                # Part row: insert empty 构件编号 at front + empty 构件名称 after spec
                # Part rows lack 构件名称 → shift: [part, spec, mat, len, qty, ...]
                # Normalize to:  [None, part, spec, None, mat, len, qty, ...]
                new_row = [None] + row_vals[:2] + [None] + row_vals[2:]
                norm_data.append(new_row)
                norm_count += 1
            else:
                # Component row: insert empty 零件号 at col 1
                new_row = [row_vals[0]] + [None] + row_vals[1:]
                norm_data.append(new_row)
                norm_count += 1
        if norm_count:
            data_rows = norm_data
            # Rename 零件编号→零件号 in header (don't insert duplicate)
            if "零件编号" in headers[1] if len(headers) > 1 else False:
                headers[1] = "零件号"
            log.info("  Normalized row structure: %d rows.", norm_count)

    # ---- Normalize column order to standard layout ----
    # Map variant columns (型材→规格, 单面积→单表面积, etc.) and reorder.
    _STD_KEYS = [
        ("批次","批次"), ("构件编号","构件编号"), ("零件","零件号"),
        ("型材","规格"), ("规格","规格"),
        ("长度","长度"), ("材质","材质"), ("数量","数量"),
        ("单净重","单净重"), ("总净重","总净重"),
        ("单毛重","单毛重"), ("总毛重","总毛重"),
        ("单面","单表面积"), ("总面","总表面积"),
    ]
    col_map = {}
    used_dst = set()
    for src_idx, h in enumerate(headers):
        for kw, dst_name in _STD_KEYS:
            if kw in h and dst_name not in used_dst:
                col_map[src_idx] = (len(col_map), dst_name)
                used_dst.add(dst_name)
                break
    # Only normalize space-delimited files (tab-separated = standard layout)
    if sep == r"\s+" and len(col_map) >= 5 and len(col_map) < len(headers):
        # Reorder to standard Tekla column order:
        # 构件编号, 零件号, 规格, 长度, 材质, 数量, 单净重, 总净重,
        # 单毛重, 总毛重, 单表面积, 总表面积
        _DST_ORDER = [
            "构件编号", "零件号", "规格", "长度", "材质", "数量",
            "单净重", "总净重", "单毛重", "总毛重", "单表面积", "总表面积",
        ]
        # Build lookup: dst_name → src_index
        name_to_src = {dst_name: src for src, (_d, dst_name) in col_map.items()}
        new_headers = []
        src_order = []
        for name in _DST_ORDER:
            if name in name_to_src:
                new_headers.append(name)
                src_order.append(name_to_src[name])
        if len(new_headers) >= 8:
            new_data_rows = []
            for row_vals in data_rows:
                new_data_rows.append([
                    row_vals[src] if src < len(row_vals) else None
                    for src in src_order
                ])
            headers = new_headers
            data_rows = new_data_rows
            log.info("  Normalized column layout: %d cols (Tekla order).", len(headers))

    log.info("  Headers: %s", headers)
    log.info("  Data rows: %d", len(data_rows))

    # ---- Create workbook with 原表 ----
    wb = openpyxl.Workbook()
    ws_raw = wb.active
    ws_raw.title = "原表"

    for j, h in enumerate(headers):
        ws_raw.cell(row=1, column=j + 1, value=h)
    for i, row_vals in enumerate(data_rows):
        for j, val in enumerate(row_vals):
            if val is not None:
                ws_raw.cell(row=i + 2, column=j + 1, value=val)

    # Step 0: Remove spaces from 原表
    remove_all_spaces(ws_raw)

    # Step 1: Copy 原表 → 整理表
    ws = wb.copy_worksheet(ws_raw)
    ws.title = "整理表"

    log.info("Steps 0-1: Loaded → '原表', copied → '整理表'. 原表 preserved.")
    return wb, ws
