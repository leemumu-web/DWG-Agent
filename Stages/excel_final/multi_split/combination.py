"""Equal-condition data merge (合并).

Port of the VBA `combination` function + FrmCombination form.

Provides two modes:
  1. **Check mode** (checkbtn_Click): Find baseline values that have
     inconsistent check values across rows.
  2. **Merge mode** (newok_Click / ok_Click): Group rows by condition
     columns and sum the value columns.

The VBA has both a "legacy" merge (ok_Click) that does row-by-row in-place
merging with nested loops, and a "new" merge (newok_Click) that uses
AdvancedFilter + WorksheetFunction.Sum.  Both are functionally equivalent
to pandas groupby().sum().
"""

import pandas as pd

from .utils import resolve_column, resolve_columns


def combination_check(
    df: pd.DataFrame,
    baseline_col: str | int,
    check_cols: list[str | int],
) -> dict:
    """Check if equal-condition rows have consistent check values.

    The VBA algorithm:
      1. AdvancedFilter unique on [baseline_col] + check_cols
      2. Count occurrences of each baseline value in the unique set
      3. If any baseline value appears >1 time: there are differences
      4. Report the differences

    Args:
        df: Input DataFrame.
        baseline_col: The baseline (reference) column.
        check_cols: Columns to check for consistency.

    Returns:
        dict with:
          - 'can_merge': bool -- True if all baseline values have single unique combination
          - 'differences': dict -- maps baseline values to their differing check values
    """
    baseline_col = resolve_column(df, baseline_col)
    check_cols = resolve_columns(df, check_cols)

    cols = [baseline_col] + check_cols
    unique_combos = df[cols].drop_duplicates()

    # Count how many times each baseline value appears in unique set
    baseline_counts = unique_combos[baseline_col].value_counts()

    differences = {}
    for baseline_val, count in baseline_counts.items():
        if count > 1:
            # This baseline has different check values
            diff_rows = unique_combos[unique_combos[baseline_col] == baseline_val]
            diff_values = {}
            for cc in check_cols:
                vals = diff_rows[cc].dropna().unique().tolist()
                diff_values[cc] = vals
            differences[str(baseline_val)] = diff_values

    return {
        "can_merge": len(differences) == 0,
        "differences": differences,
    }


def combination_merge(
    df: pd.DataFrame,
    condition_cols: list[str | int],
    sum_cols: list[str | int],
    method: str = "new",
) -> pd.DataFrame:
    """Merge rows with equal conditions by summing value columns.

    This is the pandas equivalent of both VBA merge methods:
      - 'new' (newok_Click): AdvancedFilter unique → criteria filter → sum
      - 'legacy' (ok_Click): nested row-by-row match → sum → delete

    Both produce the same result: groupby condition columns, sum sum columns.

    Args:
        df: Input DataFrame.
        condition_cols: Columns that define equality (merge key).
        sum_cols: Columns whose values should be summed.
        method: Which VBA method to emulate ('new' or 'legacy').
                Both produce identical results with pandas groupby.

    Returns:
        Merged DataFrame with summed values.
    """
    condition_cols = resolve_columns(df, condition_cols)
    sum_cols = resolve_columns(df, sum_cols)

    # Validate: condition_cols and sum_cols must not overlap (VBA check)
    overlap = set(condition_cols) & set(sum_cols)
    if overlap:
        raise ValueError(
            f"所选择的合并项与选择的合并条件重复: {overlap}，请重新选择"
        )

    # All other columns (not in condition_cols or sum_cols)
    other_cols = [c for c in df.columns if c not in condition_cols and c not in sum_cols]

    # Process numeric conversion for sum columns
    df_work = df.copy()
    for sc in sum_cols:
        df_work[sc] = pd.to_numeric(df_work[sc], errors="coerce").fillna(0)

    # Group by condition columns, sum the sum columns
    agg_dict = {sc: "sum" for sc in sum_cols}

    # For other columns, take the first value in each group
    for oc in other_cols:
        agg_dict[oc] = "first"

    result = df_work.groupby(condition_cols, as_index=False, dropna=False).agg(agg_dict)

    # Reorder columns to match original order
    original_order = [c for c in df.columns if c in result.columns]
    result = result[original_order]

    return result.reset_index(drop=True)


def combination_merge_legacy(
    df: pd.DataFrame,
    condition_cols: list[str | int],
    sum_cols: list[str | int],
) -> pd.DataFrame:
    """Legacy merge method (same as combination_merge with method='legacy').

    Provided as a separate function for explicit backward compatibility.
    """
    return combination_merge(df, condition_cols, sum_cols, method="legacy")
