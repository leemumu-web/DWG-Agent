"""Tests for profile.py"""

import pandas as pd

from multi_split.profile import (
    split_profile_df,
    _detect_profile_type,
    _parse_four_num,
    _parse_plate,
)


def test_detect_bh():
    assert _detect_profile_type("BH300*200*6*8") == "BH"
    assert _detect_profile_type("HA250*150*5*6") == "BH"
    assert _detect_profile_type("bh200*100*4*6") == "BH"


def test_detect_i():
    assert _detect_profile_type("I300*150*6*8") == "I"
    assert _detect_profile_type("HI250*125*5*7") == "I"
    assert _detect_profile_type("i400*200*8*10") == "I"


def test_detect_bt():
    assert _detect_profile_type("BT200*150*6*8") == "BT"


def test_detect_plate():
    assert _detect_profile_type("PL10*2000") == "PL"
    assert _detect_profile_type("-15*3000") == "PL"
    assert _detect_profile_type("pl5*1500") == "PL"


def test_detect_none():
    assert _detect_profile_type("L50*5") is None
    assert _detect_profile_type("普通钢板") is None


def test_parse_four_num():
    dims = _parse_four_num("BH300*200*6*8")
    assert dims == [300, 200, 6, 8]

    dims = _parse_four_num("I250*150*5*7")
    assert dims == [250, 150, 5, 7]


def test_parse_plate():
    dims = _parse_plate("PL10*2000")
    assert dims == [10, 2000]

    dims = _parse_plate("-15*3000")
    assert dims == [15, 3000]

    # Should sort smaller first
    dims = _parse_plate("PL2000*10")
    assert dims == [10, 2000]


def test_split_bh():
    df = pd.DataFrame({
        "规格": ["BH300*200*6*8", "PL10*2000"],
        "宽度": [300, 10],
        "数量": [1, 5],
        "零件类型": ["H型钢", "板材"],
    })
    result = split_profile_df(df, modes=["BH"])
    # BH row should be split into 2 (web + flange), PL row unchanged → 3 total
    assert len(result) == 3
    assert "H腹板" in "".join(result["零件类型"].astype(str))
    assert "H翼缘" in "".join(result["零件类型"].astype(str))


def test_split_i_beam():
    df = pd.DataFrame({
        "规格": ["I300*150*6*8"],
        "宽度": [300],
        "数量": [1],
        "零件类型": ["工字钢"],
    })
    result = split_profile_df(df, modes=["I"])
    assert len(result) == 2
    assert "工腹板" in "".join(result["零件类型"].astype(str))
    assert "工翼缘" in "".join(result["零件类型"].astype(str))


def test_split_plate():
    df = pd.DataFrame({
        "规格": ["PL2000*10", "PL5*1500"],
        "宽度": [2000, 5],
        "数量": [3, 2],
        "零件类型": ["钢板", "钢板"],
    })
    result = split_profile_df(df, modes=["PL"])
    # 10 vs 2000 → spec=10, width=2000 (sorted)
    assert result.iloc[0]["规格"] == "10"
    assert result.iloc[0]["宽度"] == "2000"


def test_split_default_modes():
    """Default: BH + I + PL, all three enabled."""
    df = pd.DataFrame({
        "规格": ["BH300*200*6*8", "I250*150*5*7", "PL10*2000"],
        "宽度": [300, 250, 10],
        "数量": [1, 1, 5],
        "零件类型": ["", "", ""],
    })
    result = split_profile_df(df)  # default modes
    # BH → 2 rows, I → 2 rows, PL → 1 row = 5 rows
    assert len(result) == 5


def test_split_no_duplicate_original():
    """BH split should produce exactly 2 rows, not 3 (original row removed)."""
    df = pd.DataFrame({
        "规格": ["BH300*200*6*8"],
        "宽度": [300],
        "数量": [1],
        "零件类型": [""],
    })
    result = split_profile_df(df, modes=["BH"])
    assert len(result) == 2


def test_split_bt():
    df = pd.DataFrame({
        "规格": ["BT200*150*6*8"],
        "宽度": [200],
        "数量": [2],
        "零件类型": [""],
    })
    result = split_profile_df(df, modes=["BT"])
    assert len(result) == 2
    # BT web: H - tf = 200 - 8 = 192
    web = result[result["零件类型"].str.contains("T腹")]
    assert "192" in web["宽度"].values[0]
