"""Transform 初始表 format data into 整理表_拆板后-ready DataFrame.

Core pipeline:
  1. build_df()       — PartRows → normalized DataFrame with classified specs
  2. apply_split()    — multi_split.split_profile_df(modes=["BH","BT","BOX","I","PL"])
  3. fix_post_split() — clear flange weights
  4. apply_ordering() — PL+split (original order) → D19 → D8 → M20
  5. calculate()      — theory weights, totals, D8 density
  6. generate_ids()   — import part IDs, import component IDs
"""

from __future__ import annotations

import logging

import pandas as pd

from multi_split import split_profile_df
from spec_parser import classify_spec, parse_plate_dims, D8_DENSITY
from reader_init import ComponentInfo, PartRow

log = logging.getLogger(__name__)


# ── Step orchestration ───────────────────────────────────────────


def transform(part_rows: list[PartRow], comp_info: ComponentInfo) -> pd.DataFrame:
    """Run the full 初始表 transform pipeline.

    Returns a DataFrame ready for writing to 整理表_拆板后.
    """
    df = build_df(part_rows, comp_info)
    log.info("Built DataFrame: %d rows, %d cols.", len(df), len(df.columns))

    df = apply_split(df)
    log.info("After multi_split: %d rows.", len(df))

    df = fix_post_split(df)
    df = apply_ordering(df)
    df = calculate(df)
    df = generate_ids(df, comp_info)

    return df


# ── Step 1: Build DataFrame ──────────────────────────────────────


def build_df(part_rows: list[PartRow], comp_info: ComponentInfo) -> pd.DataFrame:
    """Convert PartRow list to a normalized DataFrame ready for multi_split.

    PL specs are pre-parsed: spec→thickness(numeric), width→width(numeric).
    BOX specs preserved as-is (multi_split handles them natively).
    """
    rows = []
    for pr in part_rows:
        spec_type = classify_spec(pr.spec)
        spec_val = pr.spec
        width_val: str | float | None = None

        if spec_type == "PL":
            dims = parse_plate_dims(pr.spec)
            if dims:
                spec_val = str(int(dims[0]) if dims[0] == int(dims[0]) else dims[0])
                width_val = int(dims[1]) if dims[1] == int(dims[1]) else dims[1]

        rows.append({
            "零件号": pr.part_no,
            "截面型材": pr.spec,
            "规格": spec_val,
            "宽度": width_val,
            "长度": pr.length,
            "材质": pr.material,
            "数量": pr.qty,
            "单重": pr.unit_weight,
            "总重": pr.total_weight,
            "总面积": pr.surface_area,
            "备注": pr.note if pr.note else "",
            "类型": "",
            "_orig_type": spec_type,
            "原始序号": pr.original_seq,
            "构件号": comp_info.component_no,
            "构件数": comp_info.component_qty,
            "构件总重": comp_info.total_weight,
        })

    return pd.DataFrame(rows)


# ── Step 2: Split profiles ───────────────────────────────────────


def apply_split(df: pd.DataFrame) -> pd.DataFrame:
    """Apply multi_split to split BH/BT/BOX profiles.

    PL rows pass through (already parsed to numeric spec/width).
    D8/D19/M20 rows pass through unchanged.
    """
    result = split_profile_df(
        df,
        spec_col="规格",
        width_col="宽度",
        qty_col="数量",
        part_type_col="类型",
        modes=["BH", "BT", "BOX", "I", "PL"],  # full profile suite
    )
    return result


# ── Step 3: Post-split fixes ─────────────────────────────────────


def fix_post_split(df: pd.DataFrame) -> pd.DataFrame:
    """Clear weight/area for flange rows (type contains 翼).

    Note: BOX web/flange qty×2 is already handled by multi_split.
    """
    if "类型" not in df.columns:
        return df

    cover_mask = df["类型"].str.contains("翼", na=False)
    if cover_mask.any():
        wt_cols = ["单重", "总重", "总面积"]
        for col in wt_cols:
            if col in df.columns:
                df.loc[cover_mask, col] = None
        log.info("Cleared weights for %d flange rows.", cover_mask.sum())

    return df


# ── Step 4: Row ordering ─────────────────────────────────────────


def apply_ordering(df: pd.DataFrame) -> pd.DataFrame:
    """Order rows: PL+split parts (original order) → D19 → D8 → M20+.

    Split-part pairs stay adjacent because they share the same 原始序号.
    """
    def _sort_group(t: str) -> int:
        if t in ("PL", "BH", "BOX", "BT", "I", "UNKNOWN"):
            return 0
        if t in ("D19",):
            return 1
        if t in ("D8",):
            return 2
        if t in ("M20",):
            return 3
        return 0

    df = df.copy()
    df["_sort_group"] = df["_orig_type"].apply(_sort_group)
    df = df.sort_values(["_sort_group", "原始序号"]).reset_index(drop=True)
    df = df.drop(columns=["_sort_group"])
    return df


# ── Step 5: Calculate weights ────────────────────────────────────


def calculate(df: pd.DataFrame) -> pd.DataFrame:
    """Compute derived columns: totals, lengths, theory weights, density.

    Uses the formula spec×width×length×7.85/1e6 for plates.
    D8 gets density=0.395 and linear-weight formula.
    """
    df = df.copy()

    # 总数 = 数量 (same in initial table; component_count already factored)
    if "数量" in df.columns:
        df["总数"] = pd.to_numeric(df["数量"], errors="coerce")

    # 总长 = 长度 × 总数
    if "长度" in df.columns and "总数" in df.columns:
        df["总长"] = pd.to_numeric(df["长度"], errors="coerce") * pd.to_numeric(df["总数"], errors="coerce")

    # 下料长度 = 长度 (no left/right inset in initial table)
    df["下料长度"] = pd.to_numeric(df["长度"], errors="coerce")

    # Theory weight for plates (numeric spec × numeric width × length × 7.85 / 1e6)
    df["理单重"] = None
    df["理总重"] = None
    df["比重"] = None

    for idx in df.index:
        spec = df.at[idx, "规格"] if "规格" in df.columns else None
        width = df.at[idx, "宽度"] if "宽度" in df.columns else None
        length = df.at[idx, "下料长度"]
        total = df.at[idx, "总数"]
        orig_type = df.at[idx, "_orig_type"] if "_orig_type" in df.columns else ""

        try:
            spec_num = float(spec) if spec is not None else None
        except (ValueError, TypeError):
            spec_num = None
        try:
            width_num = float(width) if width is not None else None
        except (ValueError, TypeError):
            width_num = None
        try:
            len_num = float(length) if length is not None and not pd.isna(length) else None
        except (ValueError, TypeError):
            len_num = None
        try:
            total_num = float(total) if total is not None and not pd.isna(total) else None
        except (ValueError, TypeError):
            total_num = None

        # Plates: spec*width*len*7.85/1e6
        if spec_num is not None and width_num is not None and len_num is not None:
            theo_unit = spec_num * width_num * len_num * 7.85 / 1_000_000
            df.at[idx, "理单重"] = round(theo_unit, 3)
            if total_num is not None:
                df.at[idx, "理总重"] = round(theo_unit * total_num, 2)

        # D8: density and linear weight
        if orig_type == "D8":
            df.at[idx, "比重"] = D8_DENSITY
            if len_num is not None:
                d8_unit = D8_DENSITY * len_num / 1000
                df.at[idx, "理单重"] = round(d8_unit, 3)
                if total_num is not None:
                    df.at[idx, "理总重"] = round(d8_unit * total_num, 2)

    # 表总重 = 总重 (web/normal), 0 (cover), None (D19/M20)
    df["表总重"] = None
    for idx in df.index:
        total_weight = df.at[idx, "总重"] if "总重" in df.columns else None
        row_type = df.at[idx, "类型"] if "类型" in df.columns else ""
        orig_type = df.at[idx, "_orig_type"] if "_orig_type" in df.columns else ""

        if isinstance(row_type, str) and "翼" in row_type:
            df.at[idx, "表总重"] = 0
        elif orig_type in ("D19", "M20") or (isinstance(row_type, str) and row_type == "" and orig_type in ("D19", "M20")):
            df.at[idx, "表总重"] = None
        else:
            try:
                tw = float(total_weight) if total_weight is not None and not pd.isna(total_weight) else None
                df.at[idx, "表总重"] = tw
            except (ValueError, TypeError):
                df.at[idx, "表总重"] = None

    return df


# ── Step 6: Generate import IDs ──────────────────────────────────


def generate_ids(df: pd.DataFrame, comp_info: ComponentInfo) -> pd.DataFrame:
    """Generate 导入零件号 and 导入构件号 columns.

    Split parts:  导入零件号 = 零件号 + 类型  (e.g. "b7-cb-71BH腹")
    Non-split:     导入零件号 = 零件号
    Split parts:  导入构件号 = comp_info.component_no
    Non-split:     导入构件号 = None
    """
    df = df.copy()

    df["导入零件号"] = df["零件号"].astype(str)
    df["导入构件号"] = None

    if "类型" in df.columns:
        split_mask = df["类型"].str.strip() != ""
        df.loc[split_mask, "导入零件号"] = (
            df.loc[split_mask, "零件号"].astype(str)
            + df.loc[split_mask, "类型"].astype(str)
        )
        df.loc[split_mask, "导入构件号"] = comp_info.component_no

    return df
