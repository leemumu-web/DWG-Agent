"""Write output sheets: 初始表 pipeline produces Tekla-style sheets,
Tekla pipeline gets a 'part' sheet appended.

Sheet set (both pipelines): 原表, 整理表, 构件表, 整理表_拆板后, part
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter

# ── Part sheet format (matches reference Cpart.xlsx) ──────────────

PART_SHEET_HEADERS = [
    "构件号", "零件号", "规格", "宽度", "长度(mm)", "材质", "数量", "重量", "班组", "形状", "备注",
]

PART_SHEET_WIDTHS = {
    1: 14, 2: 12, 3: 8, 4: 8, 5: 10, 6: 12, 7: 8, 8: 12, 9: 8, 10: 8, 11: 12,
}
from reader_init import ComponentInfo
from utils import safe_str, safe_float

log = logging.getLogger(__name__)

# ── 整理表_拆板后 headers (Tekla-compatible, 27 cols) ─────────────

RESULT_HEADERS = [
    "序号", "构件编号", "构件数", "类型", "零件号", "截面型材",
    "规格", "宽度", "长度(mm)", "左进(mm)", "右进(mm)", "下料长度(mm)",
    "材质", "数量", "总数", "总长(mm)", "比重(kg/m)", "理单重(kg)",
    "理总重(kg)", "单净重(kg)", "总净重(kg)", "表净重(kg)",
    "单毛重(kg)", "总毛重(kg)", "表毛重(kg)", "单表面积(㎡)", "总表面积(㎡)",
]

RESULT_COL_WIDTHS = {
    1: 6, 2: 14, 3: 8, 4: 8, 5: 10, 6: 14, 7: 8, 8: 8, 9: 10,
    10: 6, 11: 6, 12: 10, 13: 8, 14: 6, 15: 8, 16: 10, 17: 8,
    18: 10, 19: 10, 20: 10, 21: 10, 22: 10, 23: 10, 24: 10, 25: 10,
    26: 12, 27: 12,
}

# headers for 整理表 (pre-split, simplified)
SORTED_HEADERS = [
    "序号", "构件编号", "构件数", "类型", "零件号", "截面型材",
    "规格", "宽度", "长度(mm)", "材质", "数量", "单重(kg)", "总重(kg)",
    "总面积(m2)", "备注",
]


# ── Public API ───────────────────────────────────────────────────


def write_init_output(
    output_path: Path,
    input_path: Path,
    pre_df: pd.DataFrame,
    post_df: pd.DataFrame,
    comp_info: ComponentInfo,
) -> None:
    """Write initial-table output with Tekla-style sheet set."""
    wb = Workbook()
    wb.remove(wb.active)

    # 原表 — copy input sheet
    ws_yuan = wb.create_sheet("原表")
    _copy_input_sheet(input_path, ws_yuan)

    # 整理表 — pre-split normalized data
    ws_sorted = wb.create_sheet("整理表")
    _write_sorted_sheet(ws_sorted, pre_df)

    # 构件表 — component summary
    ws_comp = wb.create_sheet("构件表")
    _write_init_comp_sheet(ws_comp, comp_info)

    # 整理表_拆板后 — post-split result (27 cols)
    ws_result = wb.create_sheet("整理表_拆板后")
    _write_result_sheet(ws_result, post_df, comp_info)

    # part — 下料板件表
    ws_part = wb.create_sheet("part")
    _write_init_part_sheet(ws_part, post_df)

    wb.save(output_path)
    log.info("Output: 原表, 整理表, 构件表, 整理表_拆板后 (%d rows), part → %s",
             len(post_df), output_path)


def add_part_sheets(wb, result_sheet: str, title: str = "") -> None:
    """Append a 'part' sheet (下料板件表) to a Tekla pipeline workbook."""
    _write_tekla_part_sheet(wb, result_sheet, title)
    log.info("Added part sheet to Tekla output.")


# ── Sheet writers for initial table pipeline ─────────────────────


def _copy_input_sheet(input_path: Path, ws) -> None:
    """Copy the 初始表 sheet from input into ws."""
    src_wb = load_workbook(input_path, read_only=True, data_only=True)
    if "初始表" in src_wb.sheetnames:
        src_ws = src_wb["初始表"]
    else:
        src_ws = src_wb.worksheets[0]
    for row_idx, row in enumerate(src_ws.iter_rows(values_only=True), start=1):
        for col_idx, value in enumerate(row, start=1):
            ws.cell(row=row_idx, column=col_idx, value=value if value is not None else "")
    src_wb.close()


def _write_sorted_sheet(ws, df: pd.DataFrame) -> None:
    """Write pre-split normalized data as 整理表."""
    _write_headers(ws, SORTED_HEADERS)
    for row_idx, (_, row) in enumerate(df.iterrows(), start=2):
        values = [
            row.get("原始序号", row_idx - 1),
            row.get("构件号", ""),
            row.get("构件数", ""),
            row.get("类型", ""),
            row.get("零件号", ""),
            row.get("截面型材", ""),
            _na_empty(row.get("规格", "")),
            _na_empty(row.get("宽度", "")),
            row.get("长度", ""),
            row.get("材质", ""),
            row.get("数量", ""),
            row.get("单重", ""),
            row.get("总重", ""),
            row.get("总面积", ""),
            _na_empty(row.get("备注", "")),
        ]
        _write_data_row(ws, row_idx, values)
    log.info("整理表: %d rows.", len(df))


def _write_init_comp_sheet(ws, comp_info: ComponentInfo) -> None:
    """Write single-row 构件表."""
    comp_headers = ["构件编号", "构件数", "构件总重", "零件号"]
    _write_headers(ws, comp_headers)
    values = [
        comp_info.component_no,
        comp_info.component_qty,
        round(comp_info.total_weight, 2),
        comp_info.raw_text,
    ]
    _write_data_row(ws, 2, values)
    log.info("构件表: 1 component.")


def _write_result_sheet(ws, df: pd.DataFrame, comp_info: ComponentInfo) -> None:
    """Write post-split result as 整理表_拆板后 (27 cols)."""
    _write_headers(ws, RESULT_HEADERS)
    _set_column_widths(ws, RESULT_COL_WIDTHS)

    for row_idx, (_, row) in enumerate(df.iterrows(), start=2):
        values = [
            row.get("原始序号", row_idx - 1),                    # 序号
            comp_info.component_no,                              # 构件编号
            comp_info.component_qty,                             # 构件数
            row.get("类型", ""),                                  # 类型
            row.get("零件号", ""),                                # 零件号
            row.get("截面型材", ""),                              # 截面型材
            _na_empty(row.get("规格", "")),                       # 规格
            _na_empty(row.get("宽度", "")),                       # 宽度
            row.get("下料长度", "") or row.get("长度", ""),       # 长度(mm)
            "",                                                  # 左进(mm)
            "",                                                  # 右进(mm)
            row.get("下料长度", "") or row.get("长度", ""),       # 下料长度(mm)
            row.get("材质", ""),                                  # 材质
            row.get("数量", ""),                                  # 数量
            row.get("总数", ""),                                  # 总数
            row.get("总长", ""),                                  # 总长(mm)
            row.get("比重", ""),                                  # 比重(kg/m)
            row.get("理单重", ""),                                # 理单重(kg)
            row.get("理总重", ""),                                # 理总重(kg)
            row.get("单重", ""),                                  # 单净重(kg) ← map from 单重
            row.get("总重", ""),                                  # 总净重(kg) ← map from 总重
            row.get("表总重", ""),                                # 表净重(kg)
            "",                                                  # 单毛重(kg) — not in init table
            "",                                                  # 总毛重(kg)
            "",                                                  # 表毛重(kg)
            row.get("总面积", ""),                                # 单表面积(㎡)
            row.get("总面积", ""),                                # 总表面积(㎡)
        ]
        _write_data_row(ws, row_idx, values)
    log.info("整理表_拆板后: %d rows.", len(df))


def _write_init_part_sheet(ws, df: pd.DataFrame) -> None:
    """Write part sheet in reference format (10 cols)."""
    # Title row
    ws.cell(row=1, column=1, value="零件清单").font = Font(bold=True, size=14)
    _write_headers(ws, PART_SHEET_HEADERS, row=2)
    _set_column_widths(ws, PART_SHEET_WIDTHS)

    # Filter cuttable types
    cuttable = {"PL", "BH", "BOX", "BT"}
    mask = df["_orig_type"].isin(cuttable) if "_orig_type" in df.columns else pd.Series(True, index=df.index)
    plate_df = df[mask].copy()

    # Split parts first, PL sorted by 零件号
    if "类型" in plate_df.columns:
        split_mask = plate_df["类型"].str.strip() != ""
        split_rows = plate_df[split_mask]
        pl_rows = plate_df[~split_mask].sort_values("零件号")
        ordered = pd.concat([split_rows, pl_rows])
    else:
        ordered = plate_df

    total_qty = 0
    total_weight = 0.0
    for row_idx, (_, row) in enumerate(ordered.iterrows(), start=3):
        spec, width = _parse_spec_width(row.get("规格", ""), row.get("宽度", ""))
        length = safe_float(row.get("长度", "") or row.get("下料长度", ""))
        qty = safe_float(row.get("数量", "")) or 1
        theo = safe_float(row.get("理总重", ""))
        comp_no = str(row.get("构件号", comp_info.component_no))
        try: total_qty += int(qty)
        except: pass
        if theo: total_weight += theo
        values = [
            comp_no,
            row.get("零件号", ""),
            int(spec) if spec and spec == int(spec) else (spec or ""),
            int(width) if width and width == int(width) else (width or ""),
            int(length) if length and length == int(length) else (length or ""),
            str(row.get("材质", "")).rstrip("-"),
            int(qty) if qty == int(qty) else qty,
            round(theo, 2) if theo else "",
            "", "", "",
        ]
        _write_data_row(ws, row_idx, values, wt_col=8)

    # Summary row
    _write_data_row(ws, row_idx + 1, [
        "", "", "", "", "", "", total_qty, round(total_weight, 2), "", "", "",
    ], wt_col=8)
    log.info("part: %d rows.", len(ordered) + 1)


# ── Sheet writer for Tekla pipeline ──────────────────────────────


def _write_tekla_part_sheet(wb, result_sheet: str, title: str = "") -> None:
    """Generate 'part' sheet from Tekla's 整理表_拆板后 (reference format)."""
    ws_src = wb[result_sheet]
    headers = [_safe_col(ws_src, 1, c) for c in range(1, ws_src.max_column + 1)]

    col_map = _build_col_map(headers)
    # Add additional columns
    for kw, key in [("理总重","theo_total"), ("数量","qty"), ("理单重","theo_unit"),
                     ("长度","length"), ("总长","total_len"), ("总数","total_count"),
                     ("构件编号","comp")]:
        if key not in col_map:
            for i, h in enumerate(headers):
                h_clean = h.split("(")[0].strip()
                if h_clean == kw or kw in h_clean:
                    col_map[key] = i + 1
                    break

    plate_rows = []
    for r in range(2, ws_src.max_row + 1):
        type_val = _safe_col(ws_src, r, col_map.get("type", 0))
        if not type_val:
            continue
        part = _safe_col(ws_src, r, col_map.get("part", 0))
        spec = _safe_col(ws_src, r, col_map.get("spec", 0))
        if not spec:
            continue
        plate_rows.append({
            "comp": _safe_col(ws_src, r, col_map.get("comp", 0)),
            "type": type_val,
            "part": part,
            "spec": spec,
            "width": _safe_col(ws_src, r, col_map.get("width", 0)),
            "length": _safe_col(ws_src, r, col_map.get("length", 0))
                      or _safe_col(ws_src, r, col_map.get("cutlen", 0)),
            "mat": _safe_col(ws_src, r, col_map.get("mat", 0)),
            "qty": _safe_col(ws_src, r, col_map.get("qty", 0)),
            "theo_total": _safe_col(ws_src, r, col_map.get("theo_total", 0)),
        })

    split_rows = [r for r in plate_rows if r["type"] != "零件"]
    normal_rows = sorted(
        [r for r in plate_rows if r["type"] == "零件"],
        key=lambda r: r["part"],
    )
    ordered = split_rows + normal_rows

    ws_part = wb.create_sheet("part")
    # Title row (use provided title or default)
    display_title = title if title else "零件清单"
    ws_part.cell(row=1, column=1, value=display_title).font = Font(bold=True, size=14)
    _write_headers(ws_part, PART_SHEET_HEADERS, row=2)
    _set_column_widths(ws_part, PART_SHEET_WIDTHS)

    total_qty = 0
    total_weight = 0.0
    for row_idx, row in enumerate(ordered, start=3):
        qty = safe_float(row["qty"]) or 1
        theo = safe_float(row["theo_total"])
        spec_raw = row.get("spec", "")
        width_raw = row.get("width", "")
        spec, width = _parse_spec_width(spec_raw, width_raw)
        length = safe_float(row["length"])
        try: total_qty += int(qty)
        except: pass
        if theo: total_weight += theo
        _write_data_row(ws_part, row_idx, [
            row.get("comp", ""),
            row["part"],
            int(spec) if spec and spec == int(spec) else (spec or ""),
            int(width) if width and width == int(width) else (width or ""),
            int(length) if length and length == int(length) else (length or ""),
            str(row["mat"]).rstrip("-"),
            int(qty) if qty == int(qty) else qty,
            round(theo, 2) if theo else "",
            "", "", "",
        ], wt_col=8)

    # Summary row — count all data rows (including those with missing spec)
    summary_row = len(ordered) + 3
    _write_data_row(ws_part, summary_row, [
        "", "", "", "", "", "", total_qty, round(total_weight, 2), "", "", "",
    ], wt_col=8)

    log.info("Tekla part: %d rows.", len(ordered) + 1)


# ── Helpers ──────────────────────────────────────────────────────


def _build_col_map(headers: list[str]) -> dict[str, int]:
    col_map = {}
    kw_map = [("序号","seq"), ("构件编号","comp"), ("类型","type"),
              ("零件号","part"), ("规格","spec"), ("宽度","width"),
              ("下料长度","cutlen"), ("材质","mat"), ("总数","total"),
              ("长度","length")]
    # exact first
    for i, h in enumerate(headers):
        h_clean = h.split("(")[0].strip()
        for kw, key in kw_map:
            if h_clean == kw:
                col_map[key] = i + 1
                break
    # substring fallback
    for i, h in enumerate(headers):
        h_clean = h.split("(")[0].strip()
        for kw, key in kw_map:
            if key not in col_map and kw in h_clean:
                col_map[key] = i + 1
    return col_map


def _safe_col(ws, row: int, col: int) -> str:
    if col < 1:
        return ""
    return safe_str(ws.cell(row=row, column=col).value)


def _thin_border() -> Border:
    side = Side(style="thin")
    return Border(left=side, right=side, top=side, bottom=side)


def _write_headers(ws, headers: list[str], row: int = 1) -> None:
    font = Font(bold=True)
    align = Alignment(horizontal="center", vertical="center")
    border = _thin_border()
    for col_idx, name in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=col_idx, value=name)
        cell.font = font
        cell.alignment = align
        cell.border = border


def _write_data_row(ws, row_num: int, values: list, *, wt_col: int = 0) -> None:
    border = _thin_border()
    for col_idx, value in enumerate(values, start=1):
        cv = "" if value is None or (isinstance(value, float) and pd.isna(value)) else value
        cell = ws.cell(row=row_num, column=col_idx, value=cv)
        cell.border = border
        if isinstance(cv, (int, float)) and cv != "":
            cell.alignment = Alignment(horizontal="right")
            # Format weight column to 2 decimal places
            if wt_col and col_idx == wt_col:
                cell.number_format = '0.00'


def _set_column_widths(ws, widths: dict[int, int]) -> None:
    for col_idx, width in widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width


def _na_empty(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value)


def _parse_spec_width(spec_raw, width_raw):
    """Parse spec and width from potentially combined format.

    Handles:
      - Normal: spec=20, width=200  → (20, 200)
      - Merged: spec="6*30", width=None → (6, 30)
    """
    spec_str = safe_str(spec_raw)
    width_str = safe_str(width_raw)
    spec_val = safe_float(spec_str)
    width_val = safe_float(width_str)

    # If spec is already numeric and width is numeric, return as-is
    if spec_val is not None and width_val is not None:
        return (spec_val, width_val)

    # If spec is numeric but width is empty, check if spec string contains '*'
    if spec_val is not None and width_val is None:
        if "*" in spec_str:
            parts = spec_str.split("*")
            if len(parts) == 2:
                t = safe_float(parts[0])
                w = safe_float(parts[1])
                if t is not None and w is not None:
                    return (t, w)
        return (spec_val, width_val)

    # If spec is non-numeric, try parsing as "t*w"
    if spec_val is None and "*" in spec_str:
        parts = spec_str.split("*")
        if len(parts) == 2:
            t = safe_float(parts[0])
            w = safe_float(parts[1])
            if t is not None and w is not None:
                return (t, w)

    # If spec is non-numeric, just pass it through as-is (e.g. "TS10.9", "STUD")
    return (spec_val if spec_val is not None else spec_str,
            width_val)
