"""Multi-condition sorting.

Port of the VBA `multisort` function + SortCriteria form.

VBA equivalent (SortCriteria.ok_Click):
    For each condition from 5 down to 1:
        If selarray(n) <> -1:
            selrange.Sort key1:=Cells(..., firstcol + selarray(n)),
                         order1:=xlAscending/xlDescending,
                         header:=xlNo, Orientation:=xlSortColumns

In pandas, multi-key sort is a single sort_values() call.  The VBA sorts
from lowest-priority to highest-priority so the highest-priority sort
wins ties.  pandas handles this natively by the order of the `by` list.
"""

import pandas as pd

from .models import SortSpec
from .utils import resolve_column


def multisort(
    df: pd.DataFrame,
    sort_specs: list[SortSpec],
) -> pd.DataFrame:
    """Sort a DataFrame by one or more columns.

    Args:
        df: DataFrame to sort.  Should have proper column names set.
        sort_specs: List of sort conditions in priority order (first = primary key).
                    Maximum 5 conditions (matching VBA limit).

    Returns:
        Sorted DataFrame.

    Raises:
        ValueError: If conditions have gaps or duplicates.
    """
    if not sort_specs:
        return df

    if len(sort_specs) > 5:
        raise ValueError(f"Maximum 5 sort conditions allowed, got {len(sort_specs)}")

    # Resolve columns
    columns = []
    asc_flags = []
    seen_cols = set()

    for i, spec in enumerate(sort_specs):
        col = resolve_column(df, spec.column)
        # VBA: First condition (selarray(1)) cannot be empty
        if i == 0 and col is None:
            raise ValueError(
                "未选择任何数据区域，缺少主要关键词！！！"
            )
        # VBA: No duplicate columns
        if col in seen_cols:
            raise ValueError(
                "选择重复关键词，请重新选择！！！"
            )
        seen_cols.add(col)
        columns.append(col)
        asc_flags.append(spec.ascending)

    # VBA: No gaps in conditions (you can't skip conditions)
    # This is enforced by SortSpec list ordering -- the user provides a contiguous list.

    return df.sort_values(by=columns, ascending=asc_flags).reset_index(drop=True)


def multisort_from_strings(
    df: pd.DataFrame,
    sort_strings: list[str],
) -> pd.DataFrame:
    """Convenience function: parse sort specs from 'column:asc' / 'column:desc' strings.

    Args:
        df: DataFrame to sort.
        sort_strings: List of strings like '构件号:asc' or '零件号:desc'.
                      If ':asc' or ':desc' is omitted, defaults to ascending.

    Returns:
        Sorted DataFrame.
    """
    specs = []
    for s in sort_strings:
        parts = s.rsplit(":", 1)
        col = parts[0].strip()
        asc = True
        if len(parts) == 2:
            asc = parts[1].strip().lower() != "desc"
        specs.append(SortSpec(column=col, ascending=asc))
    return multisort(df, specs)
