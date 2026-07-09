"""Tests for bom.py"""

import pandas as pd
import pytest

from multi_split.bom import qdmade, _map_standard_columns, _combine_profiles
from multi_split.models import ColumnMapping


def test_map_standard_columns():
    headers = ["图号", "构件号", "规格", "长度", "材质", "零件类型", "总重",
               "制作单位", "构件数量", "零件号", "宽度", "零件总数"]
    result = _map_standard_columns(headers, ColumnMapping())
    assert result["drawing_no"] == "图号"
    assert result["component_no"] == "构件号"
    assert result["spec"] == "规格"


def test_map_missing_keyword():
    headers = ["图号", "构件号"]
    with pytest.raises(ValueError, match="未找到标题"):
        _map_standard_columns(headers, ColumnMapping())


def test_combine_profiles_flagq_0():
    spec, length, mat = _combine_profiles([], 0)
    assert spec == ""
    assert length == 0.0


def test_combine_profiles_flagq_1_plate():
    mats = [{"spec": 10, "width": 200, "length": 3000, "count": 1,
             "is_numeric": True, "material": "Q235B"}]
    spec, length, mat = _combine_profiles(mats, 1)
    assert spec == "PL10*200"
    assert length == 3000
    assert mat == "Q235B"


def test_combine_profiles_flagq_1_named():
    mats = [{"spec": "L50*5", "width": 0, "length": 3000, "count": 1,
             "is_numeric": False, "material": "Q235B"}]
    spec, length, mat = _combine_profiles(mats, 1)
    assert spec == "L50*5"


def test_combine_profiles_flagq_1_with_count():
    mats = [{"spec": 10, "width": 200, "length": 3000, "count": 2,
             "is_numeric": True, "material": "Q235B"}]
    spec, length, mat = _combine_profiles(mats, 1)
    assert spec == "2PL10*200"


def test_combine_profiles_flagq_2_bh():
    """Web 6mm, flange 8mm, flange count = 2x web count"""
    mats = [
        {"spec": 6, "width": 284, "length": 3000, "count": 1,
         "is_numeric": True, "material": "Q235B"},
        {"spec": 8, "width": 200, "length": 3000, "count": 2,
         "is_numeric": True, "material": "Q235B"},
    ]
    spec, length, mat = _combine_profiles(mats, 2)
    assert spec == "BH300*200*6*8"  # 284 + 2*8 = 300


def test_combine_profiles_flagq_2_bt():
    """Equal counts of 1 → BT"""
    mats = [
        {"spec": 6, "width": 192, "length": 3000, "count": 1,
         "is_numeric": True, "material": "Q235B"},
        {"spec": 8, "width": 200, "length": 3000, "count": 1,
         "is_numeric": True, "material": "Q235B"},
    ]
    spec, length, mat = _combine_profiles(mats, 2)
    assert spec == "BT200*200*6*8"  # 192 + 8 = 200


def test_combine_profiles_flagq_2_pl_fallback():
    """Non-standard ratio → PL"""
    mats = [
        {"spec": 6, "width": 200, "length": 3000, "count": 1,
         "is_numeric": True, "material": "Q235B"},
        {"spec": 20, "width": 300, "length": 2500, "count": 3,
         "is_numeric": True, "material": "Q345B"},
    ]
    spec, length, mat = _combine_profiles(mats, 2)
    assert spec.startswith("PL")


def test_combine_profiles_flagq_3_bh():
    """Three plates, all count=1, form BH"""
    mats = [
        {"spec": 6, "width": 284, "length": 3000, "count": 1,
         "is_numeric": True, "material": "Q235B"},
        {"spec": 8, "width": 200, "length": 3000, "count": 1,
         "is_numeric": True, "material": "Q235B"},
        {"spec": 8, "width": 200, "length": 3000, "count": 1,
         "is_numeric": True, "material": "Q235B"},
    ]
    spec, length, mat = _combine_profiles(mats, 3)
    assert spec.startswith("BH")


def test_combine_profiles_flagq_4_plus():
    """Four+ materials → pick max length"""
    mats = [
        {"spec": 10, "width": 200, "length": 3000, "count": 1,
         "is_numeric": True, "material": "Q235B"},
        {"spec": 20, "width": 200, "length": 5000, "count": 1,
         "is_numeric": True, "material": "Q345B"},
        {"spec": 15, "width": 200, "length": 4000, "count": 1,
         "is_numeric": True, "material": "Q235B"},
        {"spec": 25, "width": 200, "length": 3500, "count": 1,
         "is_numeric": True, "material": "Q235B"},
    ]
    spec, length, mat = _combine_profiles(mats, 4)
    assert length == 5000.0  # max length
    assert mat == "Q345B"


def test_qdmade_full(sample_parts_df):
    """Full BOM generation smoke test."""
    result = qdmade(sample_parts_df, other_cols=[], unique_cols=[])
    assert len(result) >= 1
    assert "主材规格" in result.columns
    assert "出厂附件" in result.columns
    assert "单重" in result.columns
    # GJ-1 should have attachments (连接板, 附件)
    gj1 = result[result["构件号"] == "GJ-1"]
    if len(gj1) > 0:
        assert "P1" in gj1["出厂附件"].iloc[0] or "P3" in gj1["出厂附件"].iloc[0]
