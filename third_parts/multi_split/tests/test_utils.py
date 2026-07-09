"""Tests for utils.py"""

import numpy as np
import pandas as pd

from multi_split.utils import (
    detect_header_row,
    strip_newlines,
    get_column_headers,
    resolve_column,
    detect_data_region,
)


def test_detect_header_row_clear():
    """First row is clearly a header."""
    df = pd.DataFrame([
        ["Name", "Age", "City"],
        ["Alice", "30", "NYC"],
        ["Bob", "25", "LA"],
    ])
    assert detect_header_row(df) == 0


def test_detect_header_row_title_above():
    """Title in first row, header in second."""
    df = pd.DataFrame([
        ["Steel Parts List", np.nan, np.nan],
        ["构件号", "规格", "长度"],
        ["GJ-1", "BH300*200*6*8", "3000"],
        ["GJ-2", "PL10*2000", "5000"],
    ])
    assert detect_header_row(df) == 1


def test_strip_newlines():
    assert strip_newlines("BH300\n*200*6*8") == "BH300*200*6*8"
    assert strip_newlines("构件号\r\n") == "构件号"
    assert strip_newlines(np.nan) == ""


def test_get_column_headers():
    df = pd.DataFrame([
        ["构件号", np.nan, "规格"],
        ["GJ-1", "data", "BH300"],
    ])
    headers = get_column_headers(df, 0)
    assert headers[0] == "构件号"
    assert "按B列" in headers[1]  # empty → "按B列"
    assert headers[2] == "规格"


def test_resolve_column():
    df = pd.DataFrame({"构件号": [1], "规格": [2]})
    assert resolve_column(df, "构件号") == "构件号"
    assert resolve_column(df, 0) == "构件号"


def test_resolve_column_substring():
    df = pd.DataFrame({"构件号_extra": [1]})
    assert resolve_column(df, "构件号") == "构件号_extra"


def test_detect_data_region():
    df = pd.DataFrame([
        ["构件号", "规格"],
        ["GJ-1", "BH300*200*6*8"],
        ["GJ-2", "PL10*2000"],
    ])
    clean_df, header_row, data_start = detect_data_region(df)
    assert header_row == 0
    assert data_start == 1
    assert "构件号" in clean_df.columns
    assert len(clean_df) == 2
