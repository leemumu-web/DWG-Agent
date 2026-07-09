"""Steel profile splitter -- splits composite spec strings into components.

Port of the VBA `bhsplit` function + FRMSPLIT form.

Supported profile types (default: all three enabled):
  H-beam (BH/HA):  welded H-section  → web + 2×flange
  I-beam (I/HI):   hot-rolled I-beam  → web + flange
  Plate  (PL/-):   steel plate        → sorted dimensions

Parses specification strings like:
  BH300*200*6*8   →  4 numbers: H=300, B=200, tw=6, tf=8
  I300*150*6*8    →  4 numbers: H=300, B=150, tw=6, tf=8
  PL10*2000        →  2 numbers: t=10, w=2000 (sorted smaller first)
  -15*3000         →  same as PL15*3000
"""

from __future__ import annotations

import re
import logging
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# Regex pattern for H/I-beam specs: prefix + 4 numbers separated by *
_FOUR_NUM_PATTERN = re.compile(
    r"^([A-Za-z]*)\s*([\d.]+)\s*\*\s*([\d.]+)\s*\*\s*([\d.]+)\s*\*\s*([\d.]+)"
)
# Plate pattern: optional prefix + 2 numbers separated by *
_PLATE_PATTERN = re.compile(
    r"^([A-Za-z]*|-)\s*([\d.]+)\s*\*\s*([\d.]+)"
)

# Default profile type groups
DEFAULT_MODES = ["BH", "I", "PL"]

# Detection prefix groups
_BH_PREFIXES = {"BH", "HA"}
_I_PREFIXES = {"I", "HI"}
_BT_PREFIXES = {"BT"}
_PL_PREFIXES = {"PL", "-"}


def _detect_profile_type(spec: str) -> str | None:
    """Detect profile type from spec string prefix.

    Returns one of 'BH', 'I', 'BT', 'PL' or None.
    """
    if pd.isna(spec):
        return None
    s = str(spec).strip().upper()
    for pfx in _BH_PREFIXES:
        if s.startswith(pfx.upper()):
            return "BH"
    for pfx in _I_PREFIXES:
        if s.startswith(pfx.upper()):
            return "I"
    for pfx in _BT_PREFIXES:
        if s.startswith(pfx.upper()):
            return "BT"
    for pfx in _PL_PREFIXES:
        if s.startswith(pfx.upper()):
            return "PL"
    return None


def _parse_four_num(teststr: str) -> list[float] | None:
    """Parse a 4-number spec string into [H, B, tw, tf]."""
    m = _FOUR_NUM_PATTERN.match(str(teststr).strip())
    if not m:
        return None
    try:
        return [float(m.group(i)) for i in range(2, 6)]
    except (ValueError, IndexError):
        return None


def _parse_plate(teststr: str) -> list[float] | None:
    """Parse plate spec string into [smaller_dim, larger_dim]."""
    s = str(teststr).strip()
    m = _PLATE_PATTERN.match(s)
    if not m:
        # Try without prefix: "10*2000"
        m2 = re.match(r"^([\d.]+)\s*\*\s*([\d.]+)", s)
        if m2:
            try:
                a, b = float(m2.group(1)), float(m2.group(2))
                return sorted([a, b])
            except (ValueError, IndexError):
                return None
        return None
    try:
        a, b = float(m.group(2)), float(m.group(3))
    except (ValueError, IndexError):
        return None
    if a > b:
        return [b, a]
    return [a, b]


def _clean_number_str(s: str) -> str:
    """Convert float string to int string if it's a whole number."""
    try:
        f = float(s)
        if f == int(f):
            return str(int(f))
        return str(f)
    except ValueError:
        return s


def _resolve_col(df: pd.DataFrame, selector: Any) -> str:
    """Resolve a column selector (name or 0-based index) to a column name."""
    if isinstance(selector, int):
        return df.columns[selector]
    if isinstance(selector, str):
        if selector in df.columns:
            return selector
        for col in df.columns:
            if selector in str(col):
                return col
    raise KeyError(f"Column not found: {selector}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def split_profile_df(
    df: pd.DataFrame,
    spec_col: str | int = "规格",
    width_col: str | int = "宽度",
    qty_col: str | int = "数量",
    part_type_col: str | int = "零件类型",
    modes: list[str] | None = None,
) -> pd.DataFrame:
    """Split steel profile spec strings in a DataFrame.

    Core processing function.  Given a DataFrame with steel part rows,
    splits composite profile specifications (BH/I-beam/plate) into
    individual component rows.

    Args:
        df: Input DataFrame with steel parts.
        spec_col: Column containing spec strings (e.g. "BH300*200*6*8").
        width_col: Column for width / dimension.
        qty_col: Column for quantity.
        part_type_col: Column for part type / annotation.  Split rows get
                       annotations like "BH腹", "BH盖", "I腹", "I盖" etc.
        modes: Which profile types to split.  Default: all three
               ['BH', 'I', 'PL'] (H-beam, I-beam, plate).

    Returns:
        DataFrame with profiles split.  Row count increases for each
        BH/I/BT split (each produces 2 rows from 1).
    """
    if modes is None:
        modes = list(DEFAULT_MODES)

    spec_col = _resolve_col(df, spec_col)
    width_col = _resolve_col(df, width_col)
    qty_col = _resolve_col(df, qty_col)
    part_type_col = _resolve_col(df, part_type_col)

    # Annotation labels for each profile type
    LABELS = {
        "BH": ("H腹板", "H翼缘"),
        "I": ("工腹板", "工翼缘"),
        "BT": ("T腹板", "T翼缘"),
    }

    marker_col = "拆分标记"
    while marker_col in df.columns:
        marker_col = "_" + marker_col

    df = df.copy()
    df[marker_col] = ""

    new_rows = []
    num_split = 0

    for _idx, row in df.iterrows():
        spec_val = str(row[spec_col]) if not pd.isna(row[spec_col]) else ""
        ptype = _detect_profile_type(spec_val)
        split_done = False

        # ---- H-beam (BH/HA) or I-beam (I/HI) or BT split ----
        if ptype in ("BH", "I", "BT") and ptype in modes:
            dims = _parse_four_num(spec_val)
            if dims and len(dims) == 4:
                H, B, tw, tf = dims
                web_label, flange_label = LABELS.get(ptype, ("腹板", "翼缘"))

                if ptype == "BT":
                    web_height = H - tf   # VBA: bharray(1) - bharray(4)
                    flange_qty_mult = 1
                else:
                    web_height = H - 2 * tf  # VBA: bharray(1) - 2*bharray(4)
                    flange_qty_mult = 2

                # Row 1: web
                r_web = row.copy()
                r_web[spec_col] = _clean_number_str(str(tw))
                r_web[width_col] = _clean_number_str(str(web_height))
                r_web[part_type_col] = (
                    str(row[part_type_col]) + web_label
                    if not pd.isna(row[part_type_col]) else web_label
                )
                r_web[marker_col] = "拆"

                # Row 2: flange
                r_flange = row.copy()
                r_flange[spec_col] = _clean_number_str(str(tf))
                r_flange[width_col] = _clean_number_str(str(B))
                if flange_qty_mult != 1:
                    try:
                        r_flange[qty_col] = _clean_number_str(
                            str(flange_qty_mult * float(row[qty_col]))
                        )
                    except (ValueError, TypeError):
                        pass
                r_flange[part_type_col] = (
                    str(row[part_type_col]) + flange_label
                    if not pd.isna(row[part_type_col]) else flange_label
                )
                r_flange[marker_col] = "拆"

                new_rows.append(r_web)
                new_rows.append(r_flange)
                num_split += 1
                split_done = True

        # ---- Plate (PL/-) split ----
        if not split_done and ptype == "PL" and "PL" in modes:
            dims = _parse_plate(spec_val)
            if dims and len(dims) == 2:
                row[spec_col] = _clean_number_str(str(dims[0]))
                row[width_col] = _clean_number_str(str(dims[1]))
                row[marker_col] = ""
                num_split += 1

        if not split_done:
            new_rows.append(row)

    result = pd.DataFrame(new_rows, columns=df.columns).reset_index(drop=True)

    if result[marker_col].eq("").all():
        result = result.drop(columns=[marker_col])

    logger.info(f"拆分完成: 共拆分 {num_split} 行型钢/板材")
    return result


# ---------------------------------------------------------------------------
# Excel-level API
# ---------------------------------------------------------------------------


def split_profile_excel(
    excel_path: str | Path,
    sheet_name: str = "整理表",
    spec_col: str | int = "规格",
    width_col: str | int = "宽度",
    qty_col: str | int = "数量",
    part_type_col: str | int = "零件类型",
    modes: list[str] | None = None,
    output_sheet: str | None = None,
) -> str:
    """Process a sheet in an Excel file and add a new sheet with split profiles.

    Reads *sheet_name* from *excel_path*, splits steel profiles, and writes
    the result as a new sheet.  The original sheet is **not** modified.

    Args:
        excel_path: Path to the .xlsx file.
        sheet_name: Name of the sheet to process (default "整理表").
        spec_col: Column name/index for spec strings.
        width_col: Column name/index for width/dimension.
        qty_col: Column name/index for quantity.
        part_type_col: Column name/index for part type / annotation output.
        modes: Profile types to split.  Default: ['BH', 'I', 'PL'].
        output_sheet: Name of the output sheet.  Default: "{sheet_name}_拆板后".

    Returns:
        Name of the newly created output sheet.

    Example:
        >>> split_profile_excel("project.xlsx", sheet_name="整理表")
        '整理表_拆板后'
    """
    excel_path = Path(excel_path)
    if output_sheet is None:
        output_sheet = f"{sheet_name}_拆板后"

    # Read the input sheet
    raw_df = pd.read_excel(excel_path, sheet_name=sheet_name, header=None, dtype=str)
    from .utils import detect_data_region
    df, _, _ = detect_data_region(raw_df)

    # Process
    result = split_profile_df(
        df,
        spec_col=spec_col,
        width_col=width_col,
        qty_col=qty_col,
        part_type_col=part_type_col,
        modes=modes,
    )

    # Write to same file: read all existing sheets, add the new one
    _write_sheet_to_excel(excel_path, output_sheet, result)

    logger.info(f"已写入新子表: '{output_sheet}' (原表 '{sheet_name}' 保持不变)")
    return output_sheet


def _write_sheet_to_excel(
    excel_path: Path,
    sheet_name: str,
    df: pd.DataFrame,
) -> None:
    """Write a DataFrame as a new sheet to an existing Excel file.

    Preserves all existing sheets.  If the sheet already exists, it is
    replaced.
    """
    import openpyxl

    if excel_path.exists():
        # Load existing workbook
        wb = openpyxl.load_workbook(excel_path)
        # Remove the target sheet if it already exists
        if sheet_name in wb.sheetnames:
            del wb[sheet_name]
        wb.save(excel_path)
        wb.close()

    # Write the new sheet
    with pd.ExcelWriter(
        excel_path, engine="openpyxl", mode="a", if_sheet_exists="replace"
    ) as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)
