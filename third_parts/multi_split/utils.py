"""Shared utility functions for SunFire processing.

These are pure functions extracted from the repeated patterns found across
every VBA UserForm_Initialize and various event handlers.
"""

import pandas as pd


def detect_header_row(df: pd.DataFrame) -> int:
    """Detect which row is the header row in a DataFrame.

    Port of the VBA header detection algorithm (appears in every form):
      For each row in the top half, count non-empty cells.
      Return the first row where >= 87.5% of cells are non-empty (excluding
      truly empty cells; NaN counts as empty).

    Args:
        df: DataFrame with header=None (raw data, no column names set).

    Returns:
        0-based row index of the detected header row.
    """
    total_cols = len(df.columns)
    if total_cols == 0:
        return 0

    threshold = total_cols - total_cols // 8  # >= ceil(7/8 * total_cols)
    top_half = max(1, len(df) // 2)

    for i in range(top_half):
        non_null = int(df.iloc[i].notna().sum())
        if non_null >= threshold:
            return i

    return 0


def strip_newlines(value) -> str:
    """Strip Chr(10)/Chr(13) from a cell value.

    The VBA code frequently does:
        For cclen = 1 To clen
            If Mid(cell.Value, cclen, 1) <> Chr(10) Then
                strcell = strcell & Mid(...)
            End If
        Next cclen

    This is equivalent to removing \\n and \\r.
    """
    if pd.isna(value):
        return ""
    return str(value).replace("\n", "").replace("\r", "")


def get_column_headers(df: pd.DataFrame, header_row: int) -> list[str]:
    """Extract cleaned column names from a header row.

    If a header cell is empty, generates '按X列' where X is the column letter
    (matching VBA behavior: colcode → "按" & colcode1 & "列").

    Also strips Chr(10) / newlines from header values.

    Args:
        df: Raw DataFrame (header=None).
        header_row: 0-based row index of the header.

    Returns:
        List of clean column name strings.
    """
    from openpyxl.utils import get_column_letter

    headers = []
    for j in range(len(df.columns)):
        cell_val = df.iloc[header_row, j]
        if pd.isna(cell_val):
            col_letter = get_column_letter(j + 1)
            headers.append(f"按{col_letter}列")
        else:
            headers.append(strip_newlines(cell_val))
    return headers


def resolve_column(df: pd.DataFrame, selector) -> str:
    """Resolve a column selector to a DataFrame column name.

    Args:
        df: DataFrame with named columns.
        selector: Can be:
            - str: exact column name
            - int: 0-based column index

    Returns:
        The resolved column name string.
    """
    if isinstance(selector, int):
        return df.columns[selector]
    if isinstance(selector, str):
        if selector in df.columns:
            return selector
        # Try substring match
        for col in df.columns:
            if selector in str(col):
                return col
    raise KeyError(f"Column not found: {selector}")


def resolve_columns(df: pd.DataFrame, selectors: list) -> list[str]:
    """Resolve a list of column selectors to DataFrame column names."""
    return [resolve_column(df, s) for s in selectors]


def detect_data_region(
    df: pd.DataFrame,
    header_row: int | None = None,
) -> tuple[pd.DataFrame, int, int]:
    """Detect header row and return cleaned DataFrame with proper column names.

    This is the Python equivalent of the combined UserForm_Initialize pattern:
        1. Detect CurrentRegion (already done by reading the sheet)
        2. Detect headrow
        3. Set column names from headrow
        4. Return data-only rows (below headrow)

    Args:
        df: Raw DataFrame (header=None).
        header_row: If provided, use this as header row. If None, auto-detect.

    Returns:
        Tuple of (cleaned_df, headrow_index, data_start_row).
        cleaned_df has proper column names and only data rows.
    """
    if header_row is None:
        header_row = detect_header_row(df)

    # Get column names from the header row
    col_names = get_column_headers(df, header_row)

    # Extract data rows (below header)
    data_df = df.iloc[header_row + 1:].copy()
    data_df.columns = col_names
    data_df = data_df.reset_index(drop=True)

    return data_df, header_row, header_row + 1
