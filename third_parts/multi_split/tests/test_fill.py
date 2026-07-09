"""Tests for fill.py"""

import numpy as np
import pandas as pd

from multi_split.fill import fillin


def test_fill_basic():
    df = pd.DataFrame({
        "A": ["x", np.nan, np.nan, "y"],
        "B": [1, np.nan, 2, np.nan],
    })
    result = fillin(df)
    assert result["A"].tolist() == ["x", "x", "x", "y"]
    assert result["B"].tolist() == [1.0, 1.0, 2.0, 2.0]


def test_fill_no_blanks():
    df = pd.DataFrame({"A": [1, 2, 3]})
    result = fillin(df)
    pd.testing.assert_frame_equal(result, df)


def test_fill_all_blank_column():
    df = pd.DataFrame({
        "A": ["x", np.nan, np.nan],
        "B": [np.nan, np.nan, np.nan],
    })
    result = fillin(df)
    assert result["A"].tolist() == ["x", "x", "x"]
    assert result["B"].isna().all()
