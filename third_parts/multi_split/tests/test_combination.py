"""Tests for combination.py"""

import pandas as pd

from multi_split.combination import combination_check, combination_merge


def test_combination_check_unique():
    """All baseline values unique → can merge."""
    df = pd.DataFrame({
        "Key": ["A", "B", "C"],
        "Val1": [1, 2, 3],
        "Val2": [10, 20, 30],
    })
    result = combination_check(df, "Key", ["Val1", "Val2"])
    assert result["can_merge"] is True
    assert len(result["differences"]) == 0


def test_combination_check_diff():
    """Same baseline, different check values → differences."""
    df = pd.DataFrame({
        "Key": ["A", "A", "B"],
        "Val1": [1, 2, 3],
        "Val2": [10, 20, 30],
    })
    result = combination_check(df, "Key", ["Val1", "Val2"])
    assert result["can_merge"] is False
    assert len(result["differences"]) > 0


def test_combination_merge_basic():
    df = pd.DataFrame({
        "Group": ["X", "X", "Y"],
        "Qty": [1, 2, 3],
        "Weight": [10.0, 20.0, 30.0],
        "Note": ["a", "a", "b"],
    })
    result = combination_merge(df, ["Group"], ["Qty", "Weight"])

    x_row = result[result["Group"] == "X"].iloc[0]
    assert x_row["Qty"] == 3  # 1+2
    assert x_row["Weight"] == 30.0  # 10+20

    y_row = result[result["Group"] == "Y"].iloc[0]
    assert y_row["Qty"] == 3
    assert y_row["Weight"] == 30.0


def test_combination_merge_overlap_error():
    df = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
    try:
        combination_merge(df, ["A"], ["A"])
        assert False, "Should have raised"
    except ValueError:
        pass
