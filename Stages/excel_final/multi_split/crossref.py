"""Cross-reference table maker -- compares two sheets and merges content.

Port of the VBA `mddzb` function + frmDZB form.

Algorithm (from VBA frmDZB.ok_Click):
  1. Select baseline (standard) columns and content columns from source
  2. Point to a target sheet via RefEdit
  3. Match column headers between source and target (by name)
  4. For each row in source, find matching row in target by baseline columns
  5. Copy content columns where matched
  6. Append unmatched target rows at the bottom
  7. Output to a new DataFrame
"""

import pandas as pd

from .utils import resolve_columns, strip_newlines


def mddzb(
    source_df: pd.DataFrame,
    target_df: pd.DataFrame,
    standard_cols: list[str | int],
    content_cols: list[str | int],
) -> pd.DataFrame:
    """Create a cross-reference table comparing source to target.

    For each row in source_df, find the matching row in target_df by the
    standard (baseline) columns, and copy over the content columns.

    Rows in target_df that don't match any source row are appended at the
    end with NaN for source content columns.

    Args:
        source_df: Source DataFrame with data to compare from.
        target_df: Target DataFrame to match against.
        standard_cols: Column names/indices that form the match key.
        content_cols: Column names/indices whose values to compare.

    Returns:
        Merged DataFrame with:
          - source standard_cols + source content_cols
          - target content_cols (prefixed with "目标-")
    """
    # Resolve column selectors
    src_standard = resolve_columns(source_df, standard_cols)
    src_content = resolve_columns(source_df, content_cols)

    # Try to find matching columns in target by name (after stripping newlines)
    tgt_standard = []
    tgt_content = []
    tgt_col_map = {strip_newlines(c): c for c in target_df.columns}

    for col in src_standard:
        clean = strip_newlines(col)
        if clean in tgt_col_map:
            tgt_standard.append(tgt_col_map[clean])
        else:
            raise ValueError(f"在目标工作表标题行中未找到所选标题：{col}")

    for col in src_content:
        clean = strip_newlines(col)
        if clean in tgt_col_map:
            tgt_content.append(tgt_col_map[clean])
        else:
            raise ValueError(f"在目标工作表标题行中未找到所选标题：{col}")

    # Merge: outer join on standard columns
    # source rows get left side, target rows get right side
    merged = source_df[src_standard + src_content].merge(
        target_df[tgt_standard + tgt_content],
        left_on=src_standard,
        right_on=tgt_standard,
        how="outer",
        suffixes=("", "_target"),
        indicator=False,
    )

    # Build output columns
    out_cols = list(src_standard) + list(src_content)
    for tcol in tgt_content:
        out_name = f"目标-{tcol}"
        merged[out_name] = merged.get(f"{tcol}_target", merged.get(tcol))
        out_cols.append(out_name)

    return merged[out_cols].reset_index(drop=True)
