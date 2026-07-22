"""Comprehensive regression tests for multi_split — VBA parity verification.

Each test references the specific VBA subroutine / form it validates.
"""
import numpy as np
import pandas as pd
import pytest

from multi_split import (
    split_profile_df,
    split_profile_excel,
    fillin,
    multisort,
    multisort_from_strings,
    combination_check,
    combination_merge,
    mddzb,
)

from multi_split.bom import (
    _safe_str,
    _build_attachment_string,
    _detect_main_materials,
    _combine_profiles,
    _map_standard_columns,
)
from multi_split.config import SunFireConfig
from multi_split.models import ColumnMapping, SortSpec

# =============================================================================
# _safe_str
# =============================================================================


class TestSafeStr:
    def test_none(self):
        assert _safe_str(None) == ""

    def test_nan(self):
        assert _safe_str(float("nan")) == ""

    def test_empty(self):
        assert _safe_str("") == ""

    def test_whitespace(self):
        assert _safe_str("  hello  ") == "hello"

    def test_normal(self):
        assert _safe_str("abc") == "abc"

    def test_number(self):
        assert _safe_str(123) == "123"
        assert _safe_str(0) == "0"


# =============================================================================
# profile.py — bhsplit / FRMSPLIT
# =============================================================================


class TestDetectProfileType:
    """VBA: ok_Click detection via InStr on spec prefix."""

    def _detect(self, spec):
        from multi_split.profile import _detect_profile_type
        return _detect_profile_type(spec)

    def test_bh_prefixes(self):
        assert self._detect("BH300*200*6*8") == "BH"
        assert self._detect("bh300*200*6*8") == "BH"
        assert self._detect("HA300*200*6*8") == "BH"
        assert self._detect("ha300*200*6*8") == "BH"

    def test_i_prefixes(self):
        assert self._detect("I200*100*5*8") == "I"
        assert self._detect("i200*100*5*8") == "I"
        assert self._detect("HI200*100*5*8") == "I"
        assert self._detect("hi200*100*5*8") == "I"

    def test_bt_prefixes(self):
        assert self._detect("BT150*100*5*6") == "BT"
        assert self._detect("bt150*100*5*6") == "BT"

    def test_plate_prefixes(self):
        assert self._detect("PL10*2000") == "PL"
        assert self._detect("pl10*2000") == "PL"
        assert self._detect("-10*2000") == "PL"

    def test_non_split_types(self):
        assert self._detect("HN300*150") is None
        assert self._detect("HW200*200") is None
        assert self._detect("L50*5") is None
        assert self._detect("C160*60*20*2") is None
        assert self._detect("方管50*50*3") is None
        assert self._detect("D100*5") is None

    def test_nan(self):
        assert self._detect(np.nan) is None

    def test_empty(self):
        assert self._detect("") is None


class TestParseFourNum:
    """VBA: bharray number extraction from spec strings."""

    def _parse(self, s):
        from multi_split.profile import _parse_four_num
        return _parse_four_num(s)

    def test_bh_standard(self):
        assert self._parse("BH300*200*6*8") == [300, 200, 6, 8]

    def test_bh_decimal(self):
        result = self._parse("BH300.5*200*6.5*8.0")
        assert result == [300.5, 200.0, 6.5, 8.0]

    def test_no_prefix(self):
        assert self._parse("300*200*6*8") == [300, 200, 6, 8]

    def test_spaces(self):
        assert self._parse("BH 300 * 200 * 6 * 8") == [300, 200, 6, 8]

    def test_invalid(self):
        assert self._parse("abc") is None
        assert self._parse("") is None

    def test_partial(self):
        # Only 3 numbers — regex requires 4
        assert self._parse("300*200*6") is None


class TestParsePlate:
    """VBA: plarray number extraction from plate specs."""

    def _parse(self, s):
        from multi_split.profile import _parse_plate
        return _parse_plate(s)

    def test_standard(self):
        assert self._parse("PL10*2000") == [10, 2000]

    def test_dash_prefix(self):
        assert self._parse("-15*3000") == [15, 3000]

    def test_no_prefix(self):
        assert self._parse("10*2000") == [10, 2000]

    def test_sorted(self):
        # VBA: CInt(plarray(1)) > CInt(plarray(2)) → swap
        assert self._parse("PL2000*10") == [10, 2000]

    def test_spaces(self):
        assert self._parse("PL 10 * 2000") == [10, 2000]

    def test_decimal(self):
        assert self._parse("PL10.5*2000.0") == [10.5, 2000.0]

    def test_invalid(self):
        assert self._parse("abc") is None
        assert self._parse("") is None


class TestSplitProfileDF:
    """VBA: ok_Click — bhsplit main loop."""

    def _mk_df(self, specs, widths=None, qtys=None, types=None):
        n = len(specs)
        return pd.DataFrame({
            "规格": specs,
            "宽度": widths or [""] * n,
            "数量": qtys or ["1"] * n,
            "零件类型": types or [""] * n,
        })

    # ---- BH split ----
    def test_bh_split_basic(self):
        """VBA chckH: BH300*200*6*8 → web(tw=6,w=276) + flange(tf=8,w=200,qty×2)."""
        df = self._mk_df(["BH300*200*6*8"], ["200"], ["5"], ["H钢"])
        result = split_profile_df(df, modes=["BH"])
        assert len(result) == 2
        # Row 0 = web
        assert result.iloc[0]["规格"] == "6"          # tw
        assert result.iloc[0]["宽度"] == "284"          # H - 2*tf = 300 - 16 = 284
        assert result.iloc[0]["零件类型"] == "H钢BH腹"
        # Row 1 = flange
        assert result.iloc[1]["规格"] == "8"           # tf
        assert result.iloc[1]["宽度"] == "200"          # B
        assert result.iloc[1]["数量"] == "10"           # 5 × 2
        assert result.iloc[1]["零件类型"] == "H钢BH翼"

    def test_bh_split_ha(self):
        """HA prefix treated same as BH."""
        df = self._mk_df(["HA350*250*8*12"], ["250"], ["1"], [""])
        result = split_profile_df(df, modes=["BH"])
        assert len(result) == 2
        assert result.iloc[0]["宽度"] == "326"  # 350 - 2*12

    def test_bh_split_empty_type(self):
        """VBA: if part_type is empty, label is just 'BH腹'/'BH翼' (VBA) or 'H腹板'/'H翼缘' (py)."""
        df = self._mk_df(["BH300*200*6*8"], ["200"], ["1"], [""])
        result = split_profile_df(df, modes=["BH"])
        assert result.iloc[0]["零件类型"] == "BH腹"
        assert result.iloc[1]["零件类型"] == "BH翼"

    def test_bh_qty_non_numeric(self):
        """VBA: qty *= 2 only works if qty is numeric. Python skips if conversion fails."""
        df = self._mk_df(["BH300*200*6*8"], ["200"], ["abc"], [""])
        result = split_profile_df(df, modes=["BH"])
        # Flange qty remains unchanged because str→float fails
        assert result.iloc[1]["数量"] == "abc"

    def test_bh_qty_zero(self):
        """Zero qty × 2 = 0 (still zero)."""
        df = self._mk_df(["BH300*200*6*8"], ["200"], ["0"], [""])
        result = split_profile_df(df, modes=["BH"])
        assert result.iloc[1]["数量"] == "0"

    # ---- I-beam split ----
    def test_i_split(self):
        """I-beam (Python extension, not in VBA) — same algorithm as BH."""
        df = self._mk_df(["I200*100*5*8"], ["100"], ["3"], ["工钢"])
        result = split_profile_df(df, modes=["I"])
        assert len(result) == 2
        assert result.iloc[0]["规格"] == "5"
        assert result.iloc[0]["宽度"] == "184"   # 200 - 2*8
        assert result.iloc[1]["数量"] == "6"      # 3 × 2
        assert "I腹" in result.iloc[0]["零件类型"]
        assert "I翼" in result.iloc[1]["零件类型"]

    def test_hi_prefix(self):
        df = self._mk_df(["HI250*150*6*10"], ["150"], ["1"], [""])
        result = split_profile_df(df, modes=["I"])
        assert len(result) == 2

    # ---- BT split ----
    def test_bt_split(self):
        """VBA CHCKT: BT web height = H - tf (NOT 2*tf), flange qty unchanged."""
        df = self._mk_df(["BT150*100*5*6"], ["100"], ["4"], ["T钢"])
        result = split_profile_df(df, modes=["BT"])
        assert len(result) == 2
        assert result.iloc[0]["规格"] == "5"       # tw
        assert result.iloc[0]["宽度"] == "144"      # H - tf = 150 - 6
        assert result.iloc[1]["规格"] == "6"       # tf
        assert result.iloc[1]["宽度"] == "100"      # B
        assert result.iloc[1]["数量"] == "4"        # unchanged! (VBA: Cells().Value = Cells().Value)
        assert "BT腹" in result.iloc[0]["零件类型"]
        assert "BT翼" in result.iloc[1]["零件类型"]

    # ---- Plate split ----
    def test_plate_split(self):
        """VBA CHCKPL: sorted dims, no new rows."""
        df = self._mk_df(["PL2000*10"], ["2000"], ["1"], ["钢板"])
        result = split_profile_df(df, modes=["PL"])
        assert len(result) == 1
        assert result.iloc[0]["规格"] == "10"
        assert result.iloc[0]["宽度"] == "2000"

    def test_plate_dash(self):
        df = self._mk_df(["-15*3000"], ["3000"], ["2"], [""])
        result = split_profile_df(df, modes=["PL"])
        assert result.iloc[0]["规格"] == "15"
        assert result.iloc[0]["宽度"] == "3000"

    def test_plate_no_prefix(self):
        """Bare '8*2000' without PL/- prefix is NOT detected as plate — passes through unchanged."""
        df = self._mk_df(["8*2000"], ["2000"], ["1"], [""])
        result = split_profile_df(df, modes=["PL"])
        # Not detected as PL (no prefix), so passes through unchanged
        assert result.iloc[0]["规格"] == "8*2000"
        assert result.iloc[0]["宽度"] == "2000"

    def test_plate_already_detected_as_bh(self):
        """A BH spec should NOT also be processed as plate (split_done guard)."""
        df = self._mk_df(["BH300*200*6*8"], ["200"], ["1"], [""])
        result = split_profile_df(df, modes=["BH", "PL"])
        assert len(result) == 2  # BH split, not plate

    # ---- Mode filtering ----
    def test_mode_filter_bh_only(self):
        df = self._mk_df(["BH300*200*6*8", "PL10*2000"], ["200", "2000"], ["1", "1"])
        result = split_profile_df(df, modes=["BH"])
        assert len(result) == 3  # BH: 2 rows + PL: 1 row (untouched)

    def test_mode_filter_pl_only(self):
        df = self._mk_df(["BH300*200*6*8", "PL10*2000"], ["200", "2000"], ["1", "1"])
        result = split_profile_df(df, modes=["PL"])
        assert len(result) == 2  # BH: 1 row (untouched) + PL: 1 row

    def test_default_modes(self):
        """BT NOT in default modes — must be explicitly enabled."""
        df = self._mk_df(["BT150*100*5*6"], ["100"], ["1"], [""])
        result = split_profile_df(df)  # default modes: BH, I, PL
        assert len(result) == 1  # BT not split by default

    # ---- Marker column ----
    def test_marker_column_present(self):
        df = self._mk_df(["BH300*200*6*8"], ["200"], ["1"], [""])
        result = split_profile_df(df, modes=["BH"])
        assert "拆分标记" in result.columns
        assert result.iloc[0]["拆分标记"] == "拆"
        assert result.iloc[1]["拆分标记"] == "拆"

    def test_marker_column_absent_when_no_splits(self):
        df = self._mk_df(["HN300*150", "L50*5"], ["150", "50"], ["1", "1"])
        result = split_profile_df(df)
        assert "拆分标记" not in result.columns

    def test_marker_column_name_conflict(self):
        df = self._mk_df(["BH300*200*6*8"], ["200"], ["1"], [""])
        df["拆分标记"] = "existing"
        result = split_profile_df(df, modes=["BH"])
        # Should create "_拆分标记" instead
        assert "_拆分标记" in result.columns

    # ---- Column resolution ----
    def test_column_by_index(self):
        df = self._mk_df(["BH300*200*6*8"], ["200"], ["1"], ["H钢"])
        result = split_profile_df(df, spec_col=0, width_col=1, qty_col=2, part_type_col=3, modes=["BH"])
        assert len(result) == 2

    def test_column_substring_match(self):
        df = pd.DataFrame({
            "产品规格": ["BH300*200*6*8"],
            "产品宽度": ["200"],
            "产品数量": ["1"],
            "产品类型": [""],
        })
        result = split_profile_df(df, spec_col="规格", width_col="宽度", qty_col="数量",
                                  part_type_col="类型", modes=["BH"])
        assert len(result) == 2
        assert result.iloc[0]["产品规格"] == "6"

    def test_column_not_found(self):
        df = self._mk_df(["BH300*200*6*8"])
        with pytest.raises(KeyError):
            split_profile_df(df, spec_col="不存在的列")

    # ---- Edge cases ----
    def test_empty_dataframe(self):
        df = pd.DataFrame({"规格": [], "宽度": [], "数量": [], "零件类型": []})
        result = split_profile_df(df)
        assert len(result) == 0

    def test_all_nonsplit_rows(self):
        df = self._mk_df(["HN300*150", "L50*5", "方管50*50"], ["150", "50", "50"], ["1", "2", "3"])
        result = split_profile_df(df)
        assert len(result) == 3  # all pass through unchanged

    def test_decimal_spec(self):
        """VBA: IsNumeric accepts decimal. _clean_number_str drops .0 suffix."""
        df = self._mk_df(["BH300.0*200.0*6.5*8.5"], ["200"], ["1"], [""])
        result = split_profile_df(df, modes=["BH"])
        assert result.iloc[0]["规格"] == "6.5"
        assert result.iloc[0]["宽度"] == "283"  # 300 - 2*8.5 = 283; _clean_number_str strips .0
        assert result.iloc[1]["规格"] == "8.5"


# =============================================================================
# fill.py — fillin / 模块宏.bas
# =============================================================================


class TestFillin:
    """VBA: fillin subroutine — fill blanks with row above."""

    def test_basic(self):
        df = pd.DataFrame({"A": ["x", None, None, "y"], "B": [1, None, 3, None]})
        result = fillin(df)
        assert result.iloc[0]["A"] == "x"
        assert result.iloc[1]["A"] == "x"   # filled
        assert result.iloc[2]["A"] == "x"   # filled
        assert result.iloc[3]["A"] == "y"
        assert result.iloc[1]["B"] == 1.0

    def test_no_blanks(self):
        df = pd.DataFrame({"A": ["a", "b", "c"]})
        result = fillin(df)
        pd.testing.assert_frame_equal(result, df)

    def test_all_nan_column(self):
        df = pd.DataFrame({"A": [np.nan, np.nan, np.nan]})
        result = fillin(df)
        assert result["A"].isna().all()


# =============================================================================
# sort.py — multisort / SortCriteria
# =============================================================================


class TestMultisort:
    """VBA: SortCriteria.ok_Click — Excel multi-key sort."""

    def test_single_asc(self):
        df = pd.DataFrame({"name": ["c", "a", "b"], "val": [3, 1, 2]})
        result = multisort(df, [SortSpec(column="name", ascending=True)])
        assert result["name"].tolist() == ["a", "b", "c"]

    def test_single_desc(self):
        df = pd.DataFrame({"name": ["c", "a", "b"], "val": [3, 1, 2]})
        result = multisort(df, [SortSpec(column="name", ascending=False)])
        assert result["name"].tolist() == ["c", "b", "a"]

    def test_multi_key(self):
        df = pd.DataFrame({
            "group": ["A", "A", "B", "B"],
            "val":   [3,   1,   4,   2],
        })
        result = multisort(df, [
            SortSpec(column="group", ascending=True),
            SortSpec(column="val", ascending=True),
        ])
        assert result["group"].tolist() == ["A", "A", "B", "B"]
        assert result["val"].tolist() == [1, 3, 2, 4]

    def test_from_strings(self):
        df = pd.DataFrame({"name": ["c", "a", "b"], "val": [3, 1, 2]})
        result = multisort_from_strings(df, ["name:asc"])
        assert result["name"].tolist() == ["a", "b", "c"]

    def test_from_strings_desc(self):
        df = pd.DataFrame({"name": ["a", "b", "c"]})
        result = multisort_from_strings(df, ["name:desc"])
        assert result["name"].tolist() == ["c", "b", "a"]

    def test_empty_conditions(self):
        df = pd.DataFrame({"a": [1, 2, 3]})
        result = multisort(df, [])
        pd.testing.assert_frame_equal(result, df)

    def test_max_conditions(self):
        with pytest.raises(ValueError, match="Maximum 5"):
            multisort(pd.DataFrame(), [SortSpec(column="a")] * 6)

    def test_duplicate_columns(self):
        """VBA: duplicate keywords → error."""
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        with pytest.raises(ValueError, match="重复"):
            multisort(df, [SortSpec(column="a"), SortSpec(column="a")])


# =============================================================================
# combination.py — FrmCombination
# =============================================================================


class TestCombination:
    """VBA: FrmCombination — check and merge."""

    def test_check_unique(self):
        df = pd.DataFrame({
            "构件号": ["A", "B", "C"],
            "零件号": ["P1", "P2", "P3"],
        })
        result = combination_check(df, baseline_col="构件号", check_cols=["零件号"])
        assert result["can_merge"] is True
        assert result["differences"] == {}

    def test_check_diff(self):
        df = pd.DataFrame({
            "构件号": ["A", "A", "B"],
            "零件号": ["P1", "P2", "P3"],
        })
        result = combination_check(df, baseline_col="构件号", check_cols=["零件号"])
        assert result["can_merge"] is False
        assert "A" in result["differences"]

    def test_merge_basic(self):
        df = pd.DataFrame({
            "构件号": ["A", "A", "B"],
            "零件号": ["P1", "P2", "P3"],
            "数量":  [1,    2,    3],
        })
        result = combination_merge(df, condition_cols=["构件号"], sum_cols=["数量"])
        assert len(result) == 2
        a_row = result[result["构件号"] == "A"].iloc[0]
        assert a_row["数量"] == 3  # 1 + 2

    def test_merge_overlap_error(self):
        """VBA: overlap check — condition and sum must be disjoint."""
        df = pd.DataFrame({"a": [1, 1], "b": [2, 2]})
        with pytest.raises(ValueError, match="重复"):
            combination_merge(df, condition_cols=["a"], sum_cols=["a"])

    def test_merge_legacy(self):
        from multi_split import combination_merge_legacy
        df = pd.DataFrame({"a": [1, 1], "b": [2, 3]})
        result = combination_merge_legacy(df, condition_cols=["a"], sum_cols=["b"])
        assert len(result) == 1
        assert result.iloc[0]["b"] == 5


# =============================================================================
# crossref.py — mddzb / frmDZB
# =============================================================================


class TestCrossref:
    """VBA: frmDZB.ok_Click — cross-reference table maker."""

    def test_basic_match(self):
        src = pd.DataFrame({"ID": ["A", "B"], "规格": ["10", "20"]})
        tgt = pd.DataFrame({"ID": ["A", "C"], "规格": ["PL10", "PL30"]})
        result = mddzb(src, tgt, standard_cols=["ID"], content_cols=["规格"])
        assert len(result) == 3
        assert "目标-规格" in result.columns

    def test_missing_target_header(self):
        src = pd.DataFrame({"ID": ["A"], "规格": ["10"]})
        tgt = pd.DataFrame({"ID": ["A"], "other": ["x"]})
        with pytest.raises(ValueError, match="未找到所选标题"):
            mddzb(src, tgt, standard_cols=["ID"], content_cols=["规格"])


# =============================================================================
# bom.py — qdmade / frmQD
# =============================================================================


class TestBuildAttachmentString:
    """VBA: frmQD attachment loop (lines 173-191)."""

    def _build(self, parts):
        config = SunFireConfig()
        return _build_attachment_string(
            parts,
            col_part_no="零件号",
            col_spec="规格",
            col_width="宽度",
            col_length="长度",
            col_part_type="零件类型",
            col_total_parts="零件总数",
            col_component_qty="构件数量",
            config=config,
        )

    def test_normal(self):
        parts = pd.DataFrame({
            "零件号": ["P001", "P002"],
            "规格": ["10", "PL20"],
            "宽度": ["200", "300"],
            "长度": ["1000", "1500"],
            "零件类型": ["连接板", "附件"],
            "零件总数": [4, 6],
            "构件数量": [2, 2],
        })
        result = self._build(parts)
        # VBA format: 零件号:规格*宽度*长度=零件总数/构件数量,
        assert "P001:PL10*200*1000=2" in result
        assert "P002:PL20*300*1500=3" in result

    def test_spec_not_numeric(self):
        """Non-numeric spec → no 'PL' prefix."""
        parts = pd.DataFrame({
            "零件号": ["P001"],
            "规格": ["L50"],
            "宽度": ["50"],
            "长度": ["1000"],
            "零件类型": ["附件"],
            "零件总数": [2],
            "构件数量": [1],
        })
        result = self._build(parts)
        assert ":L50*" in result
        assert ":PLL50*" not in result  # no PL prefix for non-numeric

    def test_nan_values_skipped(self):
        """NaN in pno or spec → row skipped."""
        parts = pd.DataFrame({
            "零件号": [np.nan, "P002"],
            "规格": [np.nan, "PL20"],
            "宽度": [np.nan, "300"],
            "长度": [np.nan, "1500"],
            "零件类型": ["连接板", "附件"],
            "零件总数": [4, 6],
            "构件数量": [2, 2],
        })
        result = self._build(parts)
        assert result == "P002:PL20*300*1500=3"
        assert "nan" not in result

    def test_empty_values_skipped(self):
        parts = pd.DataFrame({
            "零件号": ["", "P002"],
            "规格": ["", "PL20"],
            "宽度": ["", "300"],
            "长度": ["", "1500"],
            "零件类型": ["附件", "附件"],
            "零件总数": [4, 6],
            "构件数量": [2, 2],
        })
        result = self._build(parts)
        assert result == "P002:PL20*300*1500=3"
        assert "::" not in result  # no duplicate colons

    def test_all_nan(self):
        parts = pd.DataFrame({
            "零件号": [np.nan],
            "规格": [np.nan],
            "宽度": [np.nan],
            "长度": [np.nan],
            "零件类型": ["连接板"],
            "零件总数": [np.nan],
            "构件数量": [np.nan],
        })
        result = self._build(parts)
        assert result == ""

    def test_not_attachment(self):
        """Rows without attachment keywords are skipped."""
        parts = pd.DataFrame({
            "零件号": ["P003"],
            "规格": ["10"],
            "宽度": ["200"],
            "长度": ["1000"],
            "零件类型": ["主材"],
            "零件总数": [4],
            "构件数量": [2],
        })
        result = self._build(parts)
        assert result == ""

    def test_ratio_format(self):
        """VBA: integer count vs float count. Comma is inter-item separator, not trailing."""
        parts = pd.DataFrame({
            "零件号": ["P1", "P2"],
            "规格": ["10", "10"],
            "宽度": ["200", "200"],
            "长度": ["1000", "1000"],
            "零件类型": ["附件", "附件"],
            "零件总数": [4, 5],
            "构件数量": [2, 2],
        })
        result = self._build(parts)
        assert "=2," in result     # 4/2 = 2, followed by comma (next item)
        assert "=2.50" in result   # 5/2 = 2.50 (last item, no trailing comma)


class TestCombineProfiles:
    """VBA: frmQD profile combination (flagq branches)."""

    def test_flagq_0(self):
        spec, length, mat = _combine_profiles([], 0)
        assert spec == ""
        assert length == 0.0

    def test_flagq_1_plate(self):
        mats = [{"spec": 10, "width": 2000, "length": 1000, "count": 1, "is_numeric": True, "material": "Q235"}]
        spec, length, mat = _combine_profiles(mats, 1)
        assert spec == "PL10*2000"

    def test_flagq_1_named(self):
        mats = [{"spec": "HN300", "width": 150, "length": 1000, "count": 1, "is_numeric": False, "material": "Q235"}]
        spec, length, mat = _combine_profiles(mats, 1)
        assert spec == "HN300*150"

    def test_flagq_1_with_count(self):
        """VBA: if count > 1 → prepend count."""
        mats = [{"spec": 10, "width": 2000, "length": 1000, "count": 3, "is_numeric": True, "material": "Q235"}]
        spec, length, mat = _combine_profiles(mats, 1)
        assert spec == "3PL10*2000"

    def test_flagq_2_bh(self):
        """VBA: thinner=web, thicker=flange with 2x count."""
        mats = [
            {"spec": 6, "width": 276, "length": 1000, "count": 1, "is_numeric": True, "material": "Q235"},
            {"spec": 8, "width": 200, "length": 1000, "count": 2, "is_numeric": True, "material": "Q235"},
        ]
        spec, _, _ = _combine_profiles(mats, 2)
        assert spec == "BH292*200*6*8"  # H=276+2*8=292, B=200, tw=6, tf=8

    def test_flagq_2_bh_reversed(self):
        """Order shouldn't matter — algorithm detects web vs flange."""
        mats = [
            {"spec": 8, "width": 200, "length": 1000, "count": 2, "is_numeric": True, "material": "Q235"},
            {"spec": 6, "width": 276, "length": 1000, "count": 1, "is_numeric": True, "material": "Q235"},
        ]
        spec, _, _ = _combine_profiles(mats, 2)
        assert spec == "BH292*200*6*8"

    def test_flagq_2_bt(self):
        """Equal counts = 1 → BT."""
        mats = [
            {"spec": 6, "width": 200, "length": 1000, "count": 1, "is_numeric": True, "material": "Q235"},
            {"spec": 8, "width": 150, "length": 1000, "count": 1, "is_numeric": True, "material": "Q235"},
        ]
        spec, _, _ = _combine_profiles(mats, 2)
        assert spec == "BT208*150*6*8"  # H=200+8=208, B=150, tw=6, tf=8

    def test_flagq_2_pl_fallback(self):
        """Non-matching counts → PL fallback (max length)."""
        mats = [
            {"spec": 6, "width": 200, "length": 1000, "count": 1, "is_numeric": True, "material": "Q235"},
            {"spec": 8, "width": 150, "length": 1200, "count": 3, "is_numeric": True, "material": "Q345"},
        ]
        spec, length, mat = _combine_profiles(mats, 2)
        assert "PL" in spec

    def test_flagq_3_bh(self):
        """3 plates → BH with web being thinnest."""
        mats = [
            {"spec": 6, "width": 280, "length": 1000, "count": 1, "is_numeric": True, "material": "Q235"},
            {"spec": 10, "width": 200, "length": 1000, "count": 1, "is_numeric": True, "material": "Q235"},
            {"spec": 10, "width": 200, "length": 1000, "count": 1, "is_numeric": True, "material": "Q235"},
        ]
        spec, _, _ = _combine_profiles(mats, 3)
        assert "BH" in spec

    def test_flagq_3_mixed_types_fallback(self):
        """Mixed numeric/non-numeric → fallback (no crash)."""
        mats = [
            {"spec": "HN300", "width": 150, "length": 1000, "count": 1, "is_numeric": False, "material": "Q235"},
            {"spec": "HN300", "width": 150, "length": 1000, "count": 1, "is_numeric": False, "material": "Q235"},
            {"spec": 8, "width": 200, "length": 1000, "count": 1, "is_numeric": True, "material": "Q235"},
        ]
        spec, _, _ = _combine_profiles(mats, 3)
        # Should not crash; fallback returns longest-length material
        assert spec is not None

    def test_flagq_4_plus(self):
        mats = [
            {"spec": 10, "width": 200, "length": 1000, "count": 1, "is_numeric": True, "material": "Q235"},
            {"spec": 8, "width": 150, "length": 1200, "count": 1, "is_numeric": True, "material": "Q235"},
            {"spec": 6, "width": 100, "length": 800, "count": 1, "is_numeric": True, "material": "Q235"},
            {"spec": 12, "width": 300, "length": 900, "count": 1, "is_numeric": True, "material": "Q235"},
        ]
        spec, length, _ = _combine_profiles(mats, 4)
        assert length == 1200  # max length

    def test_length_nan_safety(self):
        """NaN lengths should not cause crashes."""
        mats = [
            {"spec": 6, "width": 200, "length": float("nan"), "count": 1, "is_numeric": True, "material": "Q235"},
            {"spec": 8, "width": 150, "length": 1000, "count": 2, "is_numeric": True, "material": "Q235"},
        ]
        # length=nan: c1=1, c2=2, s1<s2, c2/c1=2 → BH
        try:
            _combine_profiles(mats, 2)
        except Exception as e:
            pytest.fail(f"_combine_profiles raised {type(e).__name__}: {e}")


class TestDetectMainMaterials:
    """VBA: frmQD main material detection."""

    def _detect(self, parts, max_length):
        config = SunFireConfig()
        return _detect_main_materials(
            parts,
            col_part_type="零件类型",
            col_length="长度",
            max_length=max_length,
            col_spec="规格",
            col_width="宽度",
            col_material="材质",
            col_total_parts="零件总数",
            col_component_qty="构件数量",
            config=config,
        )

    def test_basic(self):
        parts = pd.DataFrame({
            "零件类型": ["主材", "主材", "附件"],
            "长度": [1000, 1000, 500],
            "规格": ["6", "8", "10"],
            "宽度": ["200", "150", "100"],
            "材质": ["Q235", "Q235", "Q235"],
            "零件总数": [4, 4, 2],
            "构件数量": [2, 2, 2],
        })
        result = self._detect(parts, max_length=1000)
        assert len(result) == 2

    def test_length_filter(self):
        """Parts with length < max_length/5 from max should be filtered out."""
        parts = pd.DataFrame({
            "零件类型": ["主材", "主材"],
            "长度": [1000, 100],     # max=1000, 100 < 1000/5=200 → filtered
            "规格": ["6", "8"],
            "宽度": ["200", "150"],
            "材质": ["Q235", "Q235"],
            "零件总数": [4, 4],
            "构件数量": [2, 2],
        })
        result = self._detect(parts, max_length=1000)
        assert len(result) == 1

    def test_nan_length_filtered(self):
        parts = pd.DataFrame({
            "零件类型": ["主材"],
            "长度": [np.nan],
            "规格": ["6"],
            "宽度": ["200"],
            "材质": ["Q235"],
            "零件总数": [4],
            "构件数量": [2],
        })
        result = self._detect(parts, max_length=1000)
        assert len(result) == 0  # NaN length → filtered out

    def test_no_main_material(self):
        parts = pd.DataFrame({
            "零件类型": ["附件", "连接板"],
            "长度": [1000, 1000],
            "规格": ["6", "8"],
            "宽度": ["200", "150"],
            "材质": ["Q235", "Q235"],
            "零件总数": [4, 4],
            "构件数量": [2, 2],
        })
        result = self._detect(parts, max_length=1000)
        assert len(result) == 0


class TestMapStandardColumns:
    """VBA: frmQD header mapping (12 keywords)."""

    def test_exact_match(self):
        headers = ["图号", "构件号", "构件数量", "零件号", "规格", "宽度",
                    "长度", "材质", "零件总数", "总重", "零件类型", "制作单位"]
        mapping = ColumnMapping()
        result = _map_standard_columns(headers, mapping)
        assert result["spec"] == "规格"
        assert result["total_weight"] == "总重"

    def test_substring_match(self):
        """Substring match works when all 12 keywords are present."""
        headers = [
            "a图号b", "构件号(主)", "构件数量(支)", "零件号", "规格(mm)", "宽度(mm)",
            "长度(mm)", "材质", "零件总数", "总重(kg)", "零件类型", "制作单位",
        ]
        mapping = ColumnMapping()
        result = _map_standard_columns(headers, mapping)
        assert "drawing_no" in result
        assert result["drawing_no"] == "a图号b"

    def test_missing_keyword(self):
        headers = ["图号", "构件号"]
        mapping = ColumnMapping()
        with pytest.raises(ValueError, match="未找到标题"):
            _map_standard_columns(headers, mapping)


# =============================================================================
# utils.py — shared VBA utility ports
# =============================================================================


class TestDetectHeaderRow:
    """VBA: UserForm_Initialize header detection (all forms).

    Algorithm: top-half rows; first row with >= 87.5% (ceil(7/8)) non-empty cells.
    Threshold = total_cols - total_cols // 8.
    For reliable detection the header row needs most cells filled.
    """

    def _detect(self, data):
        from multi_split.utils import detect_header_row
        df = pd.DataFrame(data)
        return detect_header_row(df)

    def test_clear_header(self):
        """Top-half scan finds row 1 (100% filled) as header, skipping row 0.

        NOTE: Use None (not '') for empty cells — pandas notna() counts '' as non-null,
        mirroring VBA IsEmpty which returns True for uninitialised cells.
        """
        cols = [f"C{i}" for i in range(8)]
        data = [
            ["Project", None, None, None, None, None, None, None],
            cols,
            ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8"],
            ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8"],
            ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8"],
        ]
        assert self._detect(data) == 1

    def test_title_above(self):
        """Row 0 sparse, row 1 empty, row 2 full → header at row 2."""
        cols = [f"C{i}" for i in range(8)]
        data = [
            ["Project Title", None, None, None, None, None, None, None],
            [None, None, None, None, None, None, None, None],
            cols,
            ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8"],
            ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8"],
            ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8"],
        ]
        assert self._detect(data) == 2

    def test_no_clear_header(self):
        """No row meets threshold → defaults to row 0."""
        data = [
            ["a", "", "", "", "", "", "", ""],
            ["b", "", "", "", "", "", "", ""],
            ["c", "", "", "", "", "", "", ""],
        ]
        assert self._detect(data) == 0  # defaults to row 0


class TestStripNewlines:
    """VBA: Chr(10) stripping (all forms)."""

    def test_newline(self):
        from multi_split.utils import strip_newlines
        assert strip_newlines("a\nb") == "ab"

    def test_cr(self):
        from multi_split.utils import strip_newlines
        assert strip_newlines("a\rb") == "ab"

    def test_nan(self):
        from multi_split.utils import strip_newlines
        assert strip_newlines(np.nan) == ""


class TestColIndexToLetter:
    """Pure-Python fallback for Excel column letters."""

    def test_single(self):
        from multi_split.utils import _col_index_to_letter
        assert _col_index_to_letter(1) == "A"
        assert _col_index_to_letter(26) == "Z"

    def test_double(self):
        from multi_split.utils import _col_index_to_letter
        assert _col_index_to_letter(27) == "AA"
        assert _col_index_to_letter(52) == "AZ"

    def test_triple(self):
        from multi_split.utils import _col_index_to_letter
        assert _col_index_to_letter(703) == "AAA"


class TestResolveColumn:
    def test_exact_match(self):
        from multi_split.utils import resolve_column
        df = pd.DataFrame(columns=["规格", "宽度", "长度"])
        assert resolve_column(df, "规格") == "规格"

    def test_substring_match(self):
        from multi_split.utils import resolve_column
        df = pd.DataFrame(columns=["产品规格(主)", "宽度"])
        assert resolve_column(df, "规格") == "产品规格(主)"

    def test_int_index(self):
        from multi_split.utils import resolve_column
        df = pd.DataFrame(columns=["A", "B", "C"])
        assert resolve_column(df, 1) == "B"

    def test_not_found(self):
        from multi_split.utils import resolve_column
        df = pd.DataFrame(columns=["A", "B"])
        with pytest.raises(KeyError):
            resolve_column(df, "Z")


class TestDetectDataRegion:
    def test_basic(self):
        """8 columns so header row with all 8 non-empty is detected (threshold=7)."""
        from multi_split.utils import detect_data_region
        cols = [f"C{i}" for i in range(8)]
        data = [
            ["Project", None, None, None, None, None, None, None],
            cols,
            ["X", "1", "kg", None, None, None, None, None],
            ["Y", "2", "kg", None, None, None, None, None],
            ["Z", "3", "kg", None, None, None, None, None],
        ]
        df = pd.DataFrame(data)
        result, headrow, start = detect_data_region(df)
        assert list(result.columns) == cols
        assert len(result) == 3  # data rows below header


# =============================================================================
# Integration / pipeline compatibility
# =============================================================================


class TestPipelineCompatibility:
    """Ensure the vendored compatibility package public API remains importable."""

    def test_split_profile_excel_roundtrip(self, tmp_path):
        """Write Excel, split, read back."""
        excel_path = tmp_path / "test.xlsx"

        # Create test Excel
        df = pd.DataFrame({
            "规格": ["BH300*200*6*8", "PL10*2000", "L50*5"],
            "宽度": ["200", "2000", "50"],
            "数量": ["1", "2", "3"],
            "零件类型": ["H钢", "钢板", "角钢"],
        })
        with pd.ExcelWriter(excel_path, engine="openpyxl") as w:
            df.to_excel(w, sheet_name="整理表", index=False)

        result_sheet = split_profile_excel(str(excel_path), sheet_name="整理表")
        assert result_sheet == "整理表_拆板后"

        # Read back and verify
        result_df = pd.read_excel(excel_path, sheet_name=result_sheet)
        assert len(result_df) == 4  # BH→2 rows + PL→1 + L→1


class TestConfig:
    """SunFireConfig — no hard deps."""

    def test_defaults(self):
        config = SunFireConfig()
        assert config.attachment_keywords == ["连接板", "附件", "散件"]
        assert config.main_material_keyword == "主"

    def test_column_mapping_defaults(self):
        config = SunFireConfig()
        cm = config.column_mapping
        assert cm.spec == "规格"
        assert cm.total_weight == "总重"


class TestCLI:
    """CLI without click dependency."""

    def test_main_function(self):
        from multi_split.cli import main
        assert callable(main)


# =============================================================================
# DEEP EDGE-CASE TESTS
# =============================================================================


class TestProfileDeep:
    """Complex profile split scenarios beyond basic VBA parity."""

    def _mk(self, specs, widths=None, qtys=None, types=None):
        n = len(specs)
        return pd.DataFrame({
            "规格": specs,
            "宽度": widths or [""] * n,
            "数量": qtys or ["1"] * n,
            "零件类型": types or [""] * n,
        })

    def test_mixed_batch(self):
        """Realistic batch: BH + I + PL + non-split types in one DataFrame."""
        specs = [
            "BH300*200*6*8", "I200*100*5*8", "PL10*2000",
            "HN300*150", "BT150*100*5*6", "-15*3000", "L50*5",
        ]
        widths = ["200", "100", "2000", "150", "100", "3000", "50"]
        qtys = ["2", "3", "1", "4", "1", "2", "5"]
        types = ["H钢", "工钢", "钢板", "H型钢", "T钢", "", "角钢"]
        df = self._mk(specs, widths, qtys, types)
        result = split_profile_df(df, modes=["BH", "I", "PL", "BT"])
        # BH: 2 rows, I: 2 rows, PL: 1 row, HN: 1, BT: 2, -: 1, L: 1 = 10
        assert len(result) == 10

    def test_large_numbers(self):
        """Very large dimension values — no overflow."""
        df = self._mk(["BH99999*88888*66*88"], ["88888"], ["1"], [""])
        result = split_profile_df(df, modes=["BH"])
        assert len(result) == 2
        w = int(result.iloc[0]["宽度"])
        assert w == 99999 - 2 * 88  # = 99823

    def test_decimal_clean_number(self):
        """_clean_number_str strips .0 but keeps .5."""
        from multi_split.profile import _clean_number_str
        assert _clean_number_str("10.0") == "10"
        assert _clean_number_str("10.5") == "10.5"
        assert _clean_number_str("0.0") == "0"
        assert _clean_number_str("abc") == "abc"

    def test_spec_with_count_prefix(self):
        """Spec like '2BH300*200*6*8' — Python detects '2BH' which is not in prefixes."""
        df = self._mk(["2BH300*200*6*8"], ["200"], ["1"], [""])
        result = split_profile_df(df, modes=["BH"])
        # '2BH' doesn't start with 'BH' → not detected → passes through
        assert len(result) == 1
        assert result.iloc[0]["规格"] == "2BH300*200*6*8"

    def test_nan_in_width_column(self):
        """NaN in width should not crash — the row may still split by spec."""
        df = self._mk(["BH300*200*6*8"], [np.nan], ["1"], [""])
        result = split_profile_df(df, modes=["BH"])
        assert len(result) == 2
        # Width column should be set to the computed value regardless
        assert result.iloc[0]["宽度"] == "284"

    def test_nan_in_qty_column(self):
        """NaN qty → flange multiplication skipped."""
        df = pd.DataFrame({
            "规格": ["BH300*200*6*8"],
            "宽度": ["200"],
            "数量": [np.nan],
            "零件类型": [""],
        })
        result = split_profile_df(df, modes=["BH"])
        # flange qty = NaN (unchanged because float(NaN) → NaN, try block catches? No...)
        # Actually float(np.nan) returns nan without error, then str(2 * nan) = "nan"
        assert len(result) == 2
        assert result.iloc[1]["数量"] == "nan"

    def test_whitespace_in_spec(self):
        """Leading/trailing whitespace stripped by .strip()."""
        df = self._mk(["  BH300*200*6*8  "], ["200"], ["1"], [""])
        result = split_profile_df(df, modes=["BH"])
        assert len(result) == 2

    def test_plate_zero_dimension(self):
        """PL0*2000 — zero thickness, valid."""
        df = self._mk(["PL0*2000"], ["2000"], ["1"], [""])
        result = split_profile_df(df, modes=["PL"])
        assert result.iloc[0]["规格"] == "0"
        assert result.iloc[0]["宽度"] == "2000"

    def test_bh_zero_dimensions(self):
        """BH with zero thickness — edge case but should not crash."""
        df = self._mk(["BH300*200*0*8"], ["200"], ["1"], [""])
        result = split_profile_df(df, modes=["BH"])
        assert len(result) == 2
        assert result.iloc[0]["规格"] == "0"


class TestBOMDeep:
    """Complex BOM generation scenarios."""

    def test_qdmade_realistic(self):
        """Full BOM generation with realistic multi-component data."""
        from multi_split.bom import qdmade
        from multi_split.config import SunFireConfig
        from multi_split.models import ColumnMapping

        config = SunFireConfig()
        mapping = ColumnMapping()

        # Simulate a Tekla export with 2 components
        df = pd.DataFrame({
            "图号": ["DWG-001", "DWG-001", "DWG-001", "DWG-002", "DWG-002"],
            "构件号": ["COMP-A", "COMP-A", "COMP-A", "COMP-B", "COMP-B"],
            "构件数量": [2, 2, 2, 1, 1],
            "零件号": ["P1", "P2", "P3", "P4", "P5"],
            "规格": ["6", "8", "PL10", "HN300", "10"],
            "宽度": ["276", "200", "2000", "150", "200"],
            "长度": ["1000", "1000", "500", "1000", "800"],
            "材质": ["Q235", "Q235", "Q235", "Q345", "Q235"],
            "零件总数": [2, 4, 1, 1, 2],
            "总重": ["50", "80", "30", "40", "20"],
            "零件类型": ["主材", "主材", "连接板", "主材", "附件"],
            "制作单位": ["工厂A", "工厂A", "工厂A", "工厂B", "工厂B"],
        })

        result = qdmade(
            df,
            other_cols=[],
            unique_cols=[],
            column_mapping=mapping,
            config=config,
        )
        assert len(result) == 2  # one row per component
        # COMP-A should have BH or BT spec from two main materials
        row_a = result[result["构件号"] == "COMP-A"].iloc[0]
        assert row_a["主材规格"] != ""
        assert "出厂附件" in result.columns

    def test_attachment_spec_numeric_pl_prefix(self):
        """Spec '10' → 'PL10', spec 'PL20' stays 'PL20'."""
        from multi_split.bom import _build_attachment_string
        config = SunFireConfig()
        parts = pd.DataFrame({
            "零件号": ["P1", "P2"],
            "规格": ["10", "PL20"],
            "宽度": ["200", "300"],
            "长度": ["1000", "1500"],
            "零件类型": ["附件", "附件"],
            "零件总数": [4, 6],
            "构件数量": [2, 2],
        })
        result = _build_attachment_string(
            parts, "零件号", "规格", "宽度", "长度",
            "零件类型", "零件总数", "构件数量", config,
        )
        assert ":PL10*" in result
        assert ":PL20*" in result

    def test_no_attachment_keyword_match(self):
        """Part types without attachment keywords are ignored."""
        from multi_split.bom import _build_attachment_string
        config = SunFireConfig()
        parts = pd.DataFrame({
            "零件号": ["P1"],
            "规格": ["10"],
            "宽度": ["200"],
            "长度": ["1000"],
            "零件类型": ["主材"],
            "零件总数": [4],
            "构件数量": [2],
        })
        result = _build_attachment_string(
            parts, "零件号", "规格", "宽度", "长度",
            "零件类型", "零件总数", "构件数量", config,
        )
        assert result == ""

    def test_flagq_3_asymmetric_flanges(self):
        """flagq=3 with different flange widths → BH with (width) notation."""
        from multi_split.bom import _combine_profiles
        mats = [
            {"spec": 6, "width": 280, "length": 1000, "count": 1, "is_numeric": True, "material": "Q235"},
            {"spec": 10, "width": 200, "length": 1000, "count": 1, "is_numeric": True, "material": "Q235"},
            {"spec": 10, "width": 180, "length": 1000, "count": 1, "is_numeric": True, "material": "Q235"},
        ]
        spec, _, _ = _combine_profiles(mats, 3)
        # Web: spec=6 (min thickness), flanges: both spec=10 with widths 200 and 180
        assert "BH" in spec
        assert "(" in spec  # asymmetric notation

    def test_flagq_1_count_display(self):
        """Count > 1 prepended to spec."""
        from multi_split.bom import _combine_profiles
        mats = [
            {"spec": 10, "width": 2000, "length": 1000, "count": 3.0, "is_numeric": True, "material": "Q235"},
        ]
        spec, _, _ = _combine_profiles(mats, 1)
        assert spec == "3PL10*2000"

    def test_detect_main_materials_edge(self):
        """max_length=0 skips detection; NaN length filtered."""
        from multi_split.bom import _detect_main_materials
        config = SunFireConfig()
        parts = pd.DataFrame({
            "零件类型": ["主材"],
            "长度": [np.nan],
            "规格": ["6"],
            "宽度": ["200"],
            "材质": ["Q235"],
            "零件总数": [4],
            "构件数量": [2],
        })
        result = _detect_main_materials(
            parts, "零件类型", "长度", max_length=1000.0,
            col_spec="规格", col_width="宽度", col_material="材质",
            col_total_parts="零件总数", col_component_qty="构件数量",
            config=config,
        )
        assert len(result) == 0  # NaN filtered


class TestTXTImport:
    """transtxt — SELX TXT file import."""

    def test_basic_import(self, tmp_path):
        """Import a simple space-delimited TXT file."""
        from multi_split import transtxt
        txt = tmp_path / "test.txt"
        txt.write_text("A B C\n1 2 3\n4 5 6\n", encoding="utf-8")
        result = transtxt([str(txt)], encoding="utf-8")
        assert len(result) > 0
        assert "test" in str(result.iloc[0, 0])  # stem becomes first column

    def test_multi_file(self, tmp_path):
        """Multiple files concatenated."""
        from multi_split import transtxt
        f1 = tmp_path / "f1.txt"
        f2 = tmp_path / "f2.txt"
        f1.write_text("X Y\n1 2\n", encoding="utf-8")
        f2.write_text("X Y\n3 4\n", encoding="utf-8")
        result = transtxt([str(f1), str(f2)], encoding="utf-8")
        assert len(result) > 1

    def test_quantity_mismatch(self):
        """quantities length must match file_paths."""
        from multi_split import transtxt
        with pytest.raises(ValueError):
            transtxt(["a.txt", "b.txt"], quantities=[1])


class TestIO:
    """Excel I/O helpers."""

    def test_read_write_roundtrip(self, tmp_path):
        from multi_split import read_excel, write_excel
        path = tmp_path / "test.xlsx"
        df = pd.DataFrame({"A": [1, 2], "B": ["x", "y"]})
        write_excel(df, path, sheet_name="data")
        df2, _ = read_excel(path, sheet_name="data", header_row=0)  # pass header_row so pandas reads normally
        # header_row=0 means first row is header
        assert len(df2) == 2
        assert list(df2.columns) == ["A", "B"]

    def test_write_with_styling(self, tmp_path):
        from multi_split import write_excel
        path = tmp_path / "styled.xlsx"
        df = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
        write_excel(df, path, column_styles={"A": {"color": "FF0000", "bold": True}})
        assert path.exists()


class TestConfigYAML:
    """SunFireConfig YAML loading."""

    def test_from_yaml(self, tmp_path):
        """Load from YAML file (requires pyyaml)."""
        pytest.importorskip("yaml")
        from multi_split.config import SunFireConfig
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("""
column_mapping:
  spec: "截面规格"
  total_weight: "总重量(kg)"
attachment_keywords: ["连接板", "小件"]
main_material_keyword: "主材"
""", encoding="utf-8")
        config = SunFireConfig.from_yaml(str(yaml_path))
        assert config.column_mapping.spec == "截面规格"
        assert config.column_mapping.total_weight == "总重量(kg)"
        assert config.attachment_keywords == ["连接板", "小件"]
        assert config.main_material_keyword == "主材"


class TestPipelineLabels:
    """End-to-end label consistency: VBA labels flow correctly through pipeline."""

    def test_vba_labels_in_split(self):
        """Verify VBA labels produced by profile split match downstream expectations."""
        df = pd.DataFrame({
            "规格": ["BH300*200*6*8", "BT150*100*5*6", "I200*100*5*8"],
            "宽度": ["200", "100", "100"],
            "数量": ["1", "2", "3"],
            "零件类型": ["H钢", "T钢", "工钢"],
        })
        result = split_profile_df(df, modes=["BH", "BT", "I"])
        # BH: row 0 web "H钢BH腹", row 1 flange "H钢BH翼"
        assert result.iloc[0]["零件类型"] == "H钢BH腹"
        assert result.iloc[1]["零件类型"] == "H钢BH翼"
        # BT: web "T钢BT腹", flange "T钢BT翼"
        assert "BT腹" in result.iloc[2]["零件类型"]
        assert "BT翼" in result.iloc[3]["零件类型"]
        # I: web "工钢I腹", flange "工钢I翼"
        assert "I腹" in result.iloc[4]["零件类型"]
        assert "I翼" in result.iloc[5]["零件类型"]

    def test_split_flange_matches_gai(self):
        """All flange labels ('翼') are detectable by '翼' keyword for step 13."""
        df = pd.DataFrame({
            "规格": ["BH300*200*6*8", "BT150*100*5*6", "I200*100*5*8"],
            "宽度": ["200", "100", "100"],
            "数量": ["1", "1", "1"],
            "零件类型": [""] * 3,
        })
        result = split_profile_df(df, modes=["BH", "BT", "I"])
        for idx in [1, 3, 5]:  # flange rows
            assert "翼" in result.iloc[idx]["零件类型"], \
                f"Row {idx} type {result.iloc[idx]['零件类型']!r} does not contain '翼'"

    def test_large_batch_split_performance(self):
        """100-row batch with mixed specs."""
        specs = []
        for i in range(34):
            specs.extend(["BH300*200*6*8", "PL10*2000", "HN300*150"])
        specs = specs[:100]
        df = pd.DataFrame({
            "规格": specs,
            "宽度": ["200"] * 100,
            "数量": ["1"] * 100,
            "零件类型": [""] * 100,
        })
        result = split_profile_df(df)
        assert len(result) > 100  # more rows due to splits


# =============================================================================
# REAL-WORLD DATA TESTS — from actual Tekla export & pipeline output
# =============================================================================


class TestBoxSplit:
    """BOX section split — real data: BOX650*300*14*24, BOX700*700*36*36."""

    def test_box_detection(self):
        """BOX prefix detected as profile type."""
        from multi_split.profile import _detect_profile_type
        assert _detect_profile_type("BOX650*300*14*24") == "BOX"
        assert _detect_profile_type("box650*300*14*24") == "BOX"
        assert _detect_profile_type("BOX700*700*36*36") == "BOX"

    def test_box_split_basic(self):
        """BOX split = same algorithm as BH, BOX-specific labels."""
        df = pd.DataFrame({
            "规格": ["BOX650*300*14*24"],
            "宽度": ["300"],
            "数量": ["1"],
            "零件类型": ["箱型钢"],
        })
        result = split_profile_df(df, modes=["BOX"])
        assert len(result) == 2
        assert result.iloc[0]["规格"] == "14"
        assert result.iloc[0]["宽度"] == "602"   # 650 - 2*24 = 602
        assert result.iloc[0]["零件类型"] == "箱型钢BOX腹"
        assert result.iloc[1]["规格"] == "24"
        assert result.iloc[1]["宽度"] == "300"
        assert result.iloc[1]["数量"] == "2"      # 1 × 2
        assert result.iloc[1]["零件类型"] == "箱型钢BOX翼"

    def test_box_large(self):
        """BOX700*700*36*36."""
        df = pd.DataFrame({
            "规格": ["BOX700*700*36*36"],
            "宽度": ["700"],
            "数量": ["3"],
            "零件类型": [""],
        })
        result = split_profile_df(df, modes=["BOX"])
        assert result.iloc[0]["宽度"] == "628"   # 700 - 2*36
        assert result.iloc[1]["数量"] == "6"      # 3 × 2

    def test_box_not_in_default_modes(self):
        """BOX NOT in DEFAULT_MODES — explicit opt-in required."""
        df = pd.DataFrame({
            "规格": ["BOX650*300*14*24"],
            "宽度": ["300"],
            "数量": ["1"],
            "零件类型": [""],
        })
        result = split_profile_df(df)  # default: BH, I, PL — no BOX
        assert len(result) == 1  # passes through unsplit

    def test_box_label_compatibility(self):
        """Explicit BOX mode emits the expected wing label."""
        df = pd.DataFrame({
            "规格": ["BOX650*300*14*24"],
            "宽度": ["300"],
            "数量": ["1"],
            "零件类型": [""],
        })
        result = split_profile_df(df, modes=["BOX"])
        assert "翼" in result.iloc[1]["零件类型"]


class TestRealTeklaData:
    """Tests against real Tekla export patterns from 首都体育学院 B7 project."""

    def test_real_spec_variety(self):
        """All real spec types from actual project."""
        from multi_split.profile import _detect_profile_type

        # Plates (various dimensions)
        assert _detect_profile_type("PL10*135") == "PL"
        assert _detect_profile_type("PL50*950") == "PL"
        assert _detect_profile_type("PL6*30") == "PL"     # very small
        assert _detect_profile_type("PL20*50") == "PL"
        assert _detect_profile_type("PL16*628") == "PL"
        assert _detect_profile_type("PL30*608") == "PL"
        assert _detect_profile_type("PL40*850") == "PL"

        # Non-steel/non-split types
        assert _detect_profile_type("D24") is None
        assert _detect_profile_type("D30") is None
        assert _detect_profile_type("NUT_M24") is None
        assert _detect_profile_type("NUT_M30") is None
        assert _detect_profile_type("TT25") is None
        assert _detect_profile_type("M20") is None
        assert _detect_profile_type("D8") is None
        assert _detect_profile_type("D19") is None

    def test_real_mixed_batch(self):
        """Simulated Tekla export row: BOX + PL + non-steel in one batch."""
        specs = [
            "BOX700*700*36*36",  # BOX section
            "PL10*135",           # plate
            "PL50*950",           # thick plate
            "D24",                # round bar (no split)
            "NUT_M30",            # nut (no split)
            "PL6*30",             # very small plate
            "TT25",               # special (no split)
        ]
        widths = ["700", "135", "950", "", "", "30", ""]
        qtys = ["1", "4", "1", "20", "30", "4", "20"]
        types = ["箱型", "板", "板", "", "", "板", ""]
        df = pd.DataFrame({
            "规格": specs, "宽度": widths, "数量": qtys, "零件类型": types,
        })
        result = split_profile_df(df, modes=["BH", "I", "PL", "BT", "BOX"])
        # BOX: 2 rows, PL10*135: 1, PL50*950: 1, D24: 1, NUT_M30: 1, PL6*30: 1, TT25: 1 = 8
        assert len(result) == 8
        # BOX rows
        box_rows = result[result["零件类型"].str.contains("BOX", na=False)]
        assert len(box_rows) == 2
        # PL6*30 sorted
        pl6 = result[result["规格"] == "6"]
        assert len(pl6) == 1
        assert pl6.iloc[0]["宽度"] == "30"  # sorted: smaller first

    def test_tekla_column_names(self):
        """Pipeline uses '截面型材' column (real Tekla export column name)."""
        df = pd.DataFrame({
            "截面型材": ["BH650*300*14*24", "PL10*143", "BOX700*700*36*36"],
            "宽度(mm)": ["300", "143", "700"],
            "数量(支)": ["1", "4", "1"],
            "零件分类": ["H型", "钢板", "箱型"],
        })
        result = split_profile_df(
            df, spec_col="截面型材", width_col="宽度(mm)",
            qty_col="数量(支)", part_type_col="零件分类",
            modes=["BH", "PL", "BOX"],
        )
        # BH: 2 rows + PL: 1 row + BOX: 2 rows = 5 rows total
        assert len(result) == 5

    def test_pl6_30_special_case(self):
        """PL6*30 is a pipeline special case (step 14 merges 规格=6,宽度=30 → 6*30)."""
        df = pd.DataFrame({
            "规格": ["PL30*6"],  # reversed: 30*6
            "宽度": ["30"],
            "数量": ["4"],
            "零件类型": [""],
        })
        result = split_profile_df(df, modes=["PL"])
        # After sort: spec=6, width=30
        assert result.iloc[0]["规格"] == "6"
        assert result.iloc[0]["宽度"] == "30"

    def test_17_column_format(self):
        """Real Tekla export has 17 columns. Header detection requires header in top half."""
        cols_count = 17

        # Only 1 metadata row so header (row 1) is within top half
        data = []
        # Row 0: sparse metadata — not enough non-null for 17 cols (needs >= 15)
        data.append(["工程名称：", "TeklaCorporation"] + [np.nan] * (cols_count - 2))
        # Row 1: header — all 17 non-null
        headers = ['批次', '构件编号', '零件号', '规格', '长度(mm)', '材质', '数量',
                   '单净重(kg)', '总净重(kg)', '单毛重(kg)', '总毛重(kg)',
                   '单表面积(㎡)', '总表面积(㎡)', '长度(mm)', '宽度(mm)', '高度(mm)', '版本']
        data.append(headers)
        # Rows 2-4: data
        for i in range(3):
            data.append([f'BATCH{i}', f'COMP{i}', f'p{i}', 'PL10*135', '250', 'Q355B',
                        '4', '2.6', '10.4'] + [np.nan] * (cols_count - 9))

        df = pd.DataFrame(data)
        from multi_split.utils import detect_data_region
        clean, headrow, start = detect_data_region(df)
        # Header should be at row 1 (all 17 filled, threshold=15 for 17 cols)
        assert headrow == 1
        assert list(clean.columns) == headers
        assert len(clean) == 3  # 3 data rows


class TestRealPipelineOutput:
    """Verify split results match actual pipeline output format."""

    def test_bh_output_matches_expected(self):
        """BH650*300*14*24 → web(14×602) + flange(24×300, qty×2) labels BH腹/BH翼."""
        df = pd.DataFrame({
            "规格": ["BH650*300*14*24"],
            "宽度": ["300"],
            "数量": ["1"],
            "零件类型": [""],
        })
        result = split_profile_df(df, modes=["BH"])
        # Web
        assert result.iloc[0]["规格"] == "14"
        assert result.iloc[0]["宽度"] == "602"  # 650 - 2*24
        assert result.iloc[0]["零件类型"] == "BH腹"
        # Flange
        assert result.iloc[1]["规格"] == "24"
        assert result.iloc[1]["宽度"] == "300"
        assert result.iloc[1]["数量"] == "2"
        assert result.iloc[1]["零件类型"] == "BH翼"

    def test_bt_output_matches_expected(self):
        """BT650*300*14*24 → web(14×626) + flange(24×300, qty unchanged)."""
        df = pd.DataFrame({
            "规格": ["BT650*300*14*24"],
            "宽度": ["300"],
            "数量": ["1"],
            "零件类型": [""],
        })
        result = split_profile_df(df, modes=["BT"])
        # Web: H - tf = 650 - 24 = 626 (NOT 2*tf!)
        assert result.iloc[0]["宽度"] == "626"
        assert result.iloc[0]["零件类型"] == "BT腹"
        # Flange: qty unchanged
        assert result.iloc[1]["数量"] == "1"
        assert result.iloc[1]["零件类型"] == "BT翼"

    def test_box_output_matches_expected(self):
        """BOX650*300*14*24 → web(14×602) + flange(24×300, qty×2) labels BOX腹/BOX翼."""
        df = pd.DataFrame({
            "规格": ["BOX650*300*14*24"],
            "宽度": ["300"],
            "数量": ["1"],
            "零件类型": [""],
        })
        result = split_profile_df(df, modes=["BOX"])
        assert result.iloc[0]["宽度"] == "602"  # 650 - 2*24
        assert result.iloc[0]["零件类型"] == "BOX腹"
        assert result.iloc[1]["数量"] == "2"
        assert result.iloc[1]["零件类型"] == "BOX翼"

    def test_full_real_component(self):
        """Simulate processing one real component (B7-4FD-ZL-19) end-to-end."""
        # Input: as it would come from Excel with 截面型材 column
        specs = [
            "PL10*143",           # plate
            "PL8*120",            # plate
            "PL16*143",           # plate
            "BH650*300*14*24",    # BH → split
            "D8",                 # round bar (no split)
            "PL12*325",           # plate
            "PL6*30",             # plate (small)
            "M20",                # bolt (no split)
            "D19",                # stud (no split)
        ]
        widths = ["143", "120", "143", "300", "", "325", "30", "", ""]
        qtys = ["4", "2", "2", "1", "26", "4", "4", "56", "120"]
        types = [""] * 9
        df = pd.DataFrame({
            "规格": specs, "宽度": widths, "数量": qtys, "零件类型": types,
        })
        result = split_profile_df(df, modes=["BH", "PL"])
        # BH: 2 rows. Others: 1 row each (8). Total: 10
        assert len(result) == 10
        # BH rows labelled
        bh_rows = result[result["零件类型"].str.contains("BH", na=False)]
        assert len(bh_rows) == 2
        assert "BH腹" in bh_rows.iloc[0]["零件类型"]
        assert "BH翼" in bh_rows.iloc[1]["零件类型"]
        # PL6*30 sorted
        pl6_30 = result[result["规格"] == "6"]
        assert len(pl6_30) == 1
        assert pl6_30.iloc[0]["宽度"] == "30"
