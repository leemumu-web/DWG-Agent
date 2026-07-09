"""Import SELX-generated TXT files.

Port of the VBA `transtxt` subroutine in 模块宏.bas.

VBA equivalent:
    With ActiveSheet.QueryTables.Add(Connection:="TEXT;" & .SelectedItems(i), ...)
        .TextFilePlatform = 936          ' GBK code page
        .TextFileTabDelimiter = True
        .TextFileSpaceDelimiter = True
        .TextFileConsecutiveDelimiter = True
        ...
    End With

The TXT files from SELX are GBK-encoded, tab+space delimited.
"""

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def transtxt(
    file_paths: list[str | Path],
    quantities: list[int] | None = None,
    encoding: str = "gbk",
) -> pd.DataFrame:
    """Import SELX-generated TXT files into a DataFrame.

    For each file:
      1. Read as GBK-encoded, tab/space delimited text
      2. Extract base filename as identifier
      3. Fill blank cells in the first 2 columns downward
      4. Optionally assign a quantity value to the last row

    Args:
        file_paths: List of paths to .txt files.
        quantities: Optional list of quantity values, one per file.
                    Corresponds to FRMTXT.TextBox2.Value in the VBA.
        encoding: File encoding (default 'gbk' for code page 936).

    Returns:
        Combined DataFrame of all imported files.
    """
    if quantities is None:
        quantities = [None] * len(file_paths)
    elif len(quantities) != len(file_paths):
        raise ValueError(
            f"quantities length ({len(quantities)}) must match "
            f"file_paths length ({len(file_paths)})"
        )

    frames = []

    for path, qty in zip(file_paths, quantities):
        path = Path(path)

        # Read GBK-encoded file.  Use python engine for multi-char sep.
        # Tab + Space with consecutive delimiters treated as one.
        df = pd.read_csv(
            path,
            encoding=encoding,
            sep=r"\s+",
            engine="python",
            header=None,
            dtype=str,
            on_bad_lines="skip",
        )

        # Extract filename (VBA trims path: Mid(value, 14, Len(value)-17))
        # We use the stem (filename without extension)
        base_name = path.stem

        # Add filename as first column value, repeated for all rows
        name_col = pd.Series([base_name] * len(df), dtype=str)

        # Fill blanks in first 2 columns downward (=R[-1]C pattern)
        for col_idx in range(min(2, len(df.columns))):
            df.iloc[:, col_idx] = df.iloc[:, col_idx].ffill()

        # If quantity provided, set last row's second column (index 1) to qty
        if qty is not None and len(df) > 0 and len(df.columns) >= 2:
            df.iloc[-1, 1] = str(qty)

        # Build output: name column first, then the imported data
        result = pd.concat(
            [name_col, df], axis=1, ignore_index=True
        )
        frames.append(result)

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, ignore_index=True)
