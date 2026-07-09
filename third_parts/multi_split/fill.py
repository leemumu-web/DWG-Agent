"""Fill blank cells with the value from the row above.

Port of the VBA `fillin` subroutine in 模块宏.bas.

VBA equivalent:
    Range("a1").CurrentRegion.SpecialCells(xlCellTypeBlanks).FormulaR1C1 = "=r[-1]c"
    Range("a1").CurrentRegion.Value = Range("a1").CurrentRegion.Value
"""

import pandas as pd


def fillin(df: pd.DataFrame) -> pd.DataFrame:
    """Fill all blank (NaN) cells with the value from the row above.

    This is the pandas equivalent of Excel's Ctrl+D or VBA's `=R[-1]C` fill.

    The VBA also first checks if any blanks exist.  We skip that check
    since ffill is a no-op when there are no NaN values.

    Args:
        df: DataFrame potentially containing NaN cells.

    Returns:
        DataFrame with NaN cells filled forward.
    """
    return df.ffill()
