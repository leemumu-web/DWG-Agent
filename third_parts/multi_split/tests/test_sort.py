"""Tests for sort.py"""

import pandas as pd
import pytest

from multi_split.models import SortSpec
from multi_split.sort import multisort, multisort_from_strings


def test_sort_single_asc(simple_df):
    result = multisort(simple_df, [SortSpec("Score", True)])
    scores = result["Score"].tolist()
    assert scores == sorted(scores)


def test_sort_single_desc(simple_df):
    result = multisort(simple_df, [SortSpec("Score", False)])
    scores = result["Score"].tolist()
    assert scores == sorted(scores, reverse=True)


def test_sort_multi(simple_df):
    result = multisort(simple_df, [
        SortSpec("Name", True),
        SortSpec("Score", False),
    ])
    # Same Name groups should have descending Score
    alice = result[result["Name"] == "Alice"]
    assert alice["Score"].iloc[0] >= alice["Score"].iloc[1]


def test_sort_from_strings(simple_df):
    result = multisort_from_strings(simple_df, ["Name:asc", "Score:desc"])
    assert list(result["Name"]) == ["Alice", "Alice", "Bob", "Bob", "Charlie"]  # may vary by tie, but Name grouped


def test_sort_max_conditions(simple_df):
    with pytest.raises(ValueError, match="Maximum 5"):
        multisort(simple_df, [SortSpec("Name")] * 6)
