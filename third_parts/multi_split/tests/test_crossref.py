"""Tests for crossref.py"""

import pandas as pd
import pytest

from multi_split.crossref import mddzb


def test_crossref_basic_match():
    source = pd.DataFrame({
        "构件号": ["GJ-1", "GJ-2", "GJ-3"],
        "主材规格": ["BH300*200*6*8", "PL10*2000", "L50*5"],
        "长度": [3000, 5000, 2000],
    })
    target = pd.DataFrame({
        "构件号": ["GJ-1", "GJ-3"],
        "主材规格": ["BH300*200*6*8", "L50*5"],
        "长度": [3050, 2050],  # different lengths
    })
    result = mddzb(source, target, ["构件号"], ["主材规格", "长度"])
    assert len(result) >= 3  # 2 matched + at least GJ-2
    assert "主材规格" in result.columns
    assert "目标-主材规格" in result.columns


def test_crossref_missing_target_header():
    source = pd.DataFrame({"A": [1]})
    target = pd.DataFrame({"B": [2]})
    with pytest.raises(ValueError, match="未找到所选标题"):
        mddzb(source, target, ["A"], ["A"])
