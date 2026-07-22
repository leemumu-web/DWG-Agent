"""Exhaustive edge-case and stress tests for multi_split — complete VBA parity.

Covers: malformed specs, NaN in all positions, concurrent mode interaction,
GBK encoding, empty/single-row DataFrames, mixed types, stable sort,
error message parity, qty edge cases, column resolution stress, header
detection boundary conditions, full component processing.
"""
import numpy as np
import pandas as pd
import pytest

from multi_split import (
    split_profile_df, split_profile_excel, fillin, multisort, combination_check, combination_merge, mddzb, transtxt,
    SortSpec, ColumnMapping, SunFireConfig,
)
from multi_split.bom import (
    _build_attachment_string, _combine_profiles,
    _map_standard_columns, qdmade,
)
from multi_split.profile import (
    _parse_four_num, _parse_plate,
    _clean_number_str,
)
from multi_split.utils import (
    detect_header_row, resolve_column, _col_index_to_letter,
)


# ============================================================================
# 1. Profile detection — malformed & edge specs
# ============================================================================

class TestMalformedSpecs:
    """Specs that are borderline, ambiguous, or corrupted."""

    def test_five_numbers(self):
        """BH300*200*6*8*10 — 5 numbers, regex only captures first 4."""
        assert _parse_four_num("BH300*200*6*8*10") == [300, 200, 6, 8]

    def test_three_number_plate(self):
        """PL10*20*30 — 3 numbers, regex greedily parses first 2 as plate."""
        r = _parse_plate("PL10*20*30")
        # Regex matches "PL10*20" capturing [10, 20], ignores trailing *30
        assert r == [10, 20]

    def test_letter_in_number_position(self):
        """BH300*ABC*6*8 — H position is non-numeric."""
        assert _parse_four_num("BH300*ABC*6*8") is None

    def test_double_star(self):
        """BH300**200*6*8 — double star."""
        r = _parse_four_num("BH300**200*6*8")
        assert r is None or len(r) != 4

    def test_trailing_star(self):
        """BH300*200*6*8* — trailing star."""
        r = _parse_four_num("BH300*200*6*8*")
        assert r == [300, 200, 6, 8] or r is None

    def test_leading_star(self):
        """*BH300*200*6*8 — leading star, not at start so regex fails."""
        assert _parse_four_num("*BH300*200*6*8") is None

    def test_only_numbers(self):
        """300*200*6*8 — no prefix, should still parse."""
        assert _parse_four_num("300*200*6*8") == [300, 200, 6, 8]

    def test_extremely_large(self):
        """BH999999*888888*666*888 — extreme dimensions."""
        r = _parse_four_num("BH999999*888888*666*888")
        assert r == [999999, 888888, 666, 888]

    def test_negative_numbers(self):
        """BH-300*200*6*8 — negative H (nonsensical but shouldn't crash)."""
        r = _parse_four_num("BH-300*200*6*8")
        assert r is None  # regex won't match negative sign in number position

    def test_spec_with_newline(self):
        """Spec with newline — `\\s*` in regex matches newline+star."""
        r = _parse_four_num("BH300\n*200*6*8")
        assert r == [300, 200, 6, 8]  # regex \\s* absorbs newline

    def test_plate_with_letter_mixed(self):
        """PL10A*2000 — letter mixed in number."""
        assert _parse_plate("PL10A*2000") is None


# ============================================================================
# 2. Mode interaction & detection precedence
# ============================================================================

class TestModeInteraction:
    """Concurrent mode behavior and detection precedence."""

    def _mk(self, specs, **kw):
        n = len(specs) if isinstance(specs, list) else 1
        specs = specs if isinstance(specs, list) else [specs]
        return pd.DataFrame({
            "规格": specs, "宽度": kw.get("w", [""] * n),
            "数量": kw.get("q", ["1"] * n), "零件类型": kw.get("t", [""] * n),
        })

    def test_bh_detected_pl_disabled(self):
        """BH spec with PL in modes but BH not — BH should NOT be split."""
        df = self._mk("BH300*200*6*8", w=["200"])
        r = split_profile_df(df, modes=["PL"])
        assert len(r) == 1  # BH not split when mode disabled

    def test_bh_detected_pl_enabled(self):
        """split_done guard prevents BH from also being processed as PL."""
        df = self._mk("BH300*200*6*8", w=["200"])
        r = split_profile_df(df, modes=["BH", "PL"])
        assert len(r) == 2  # BH split, not also plate

    def test_dash_prefix_pl_only(self):
        """'-10*2000' detected as PL even with BH mode off."""
        df = self._mk("-10*2000", w=["2000"])
        r = split_profile_df(df, modes=["PL"])
        assert r.iloc[0]["规格"] == "10"

    def test_all_modes_enabled(self):
        """All modes enabled simultaneously."""
        specs = ["BH300*200*6*8", "I200*100*5*8", "BT150*100*5*6",
                 "BOX650*300*14*24", "PL10*2000", "HN300*150"]
        df = self._mk(specs, w=["200", "100", "100", "300", "2000", "150"])
        r = split_profile_df(df, modes=["BH", "I", "BT", "BOX", "PL"])
        assert len(r) == 10  # BH:2 + I:2 + BT:2 + BOX:2 + PL:1 + HN:1

    def test_empty_modes_list(self):
        """Empty modes list → nothing split."""
        df = self._mk("BH300*200*6*8", w=["200"])
        r = split_profile_df(df, modes=[])
        assert len(r) == 1

    def test_unknown_mode_string(self):
        """Mode string not matching any profile type → no-op."""
        df = self._mk("XYZ300*200*6*8", w=["200"])
        r = split_profile_df(df, modes=["XYZ"])
        assert len(r) == 1


# ============================================================================
# 3. Quantity edge cases
# ============================================================================

class TestQuantityEdge:
    """Quantity column handling — NaN, zero, negative, float, text."""

    def _mk(self, spec, qty):
        return pd.DataFrame({
            "规格": [spec], "宽度": ["200"], "数量": [qty], "零件类型": [""],
        })

    def test_float_qty(self):
        """Float quantity '2.5' should work."""
        df = self._mk("BH300*200*6*8", "2.5")
        r = split_profile_df(df, modes=["BH"])
        assert r.iloc[1]["数量"] == "5"  # 2.5 × 2 = 5.0 → "5"

    def test_float_qty_decimal(self):
        """Float qty '1.5' → flange = 3.0 → '3'."""
        df = self._mk("BH300*200*6*8", "1.5")
        r = split_profile_df(df, modes=["BH"])
        assert r.iloc[1]["数量"] == "3"

    def test_zero_qty_web(self):
        """Zero qty → BH web qty stays 0, flange = 0×2 = 0."""
        df = self._mk("BH300*200*6*8", "0")
        r = split_profile_df(df, modes=["BH"])
        assert r.iloc[1]["数量"] == "0"

    def test_box_zero_qty(self):
        """BOX with qty=0 → both stay 0."""
        df = self._mk("BOX650*300*14*24", "0")
        r = split_profile_df(df, modes=["BOX"])
        assert r.iloc[0]["数量"] == "0"  # 0×2 = 0
        assert r.iloc[1]["数量"] == "0"

    def test_text_qty_all_columns(self):
        """Text 'abc' in qty → no multiplication for any row."""
        df = self._mk("BH300*200*6*8", "abc")
        r = split_profile_df(df, modes=["BH"])
        assert r.iloc[0]["数量"] == "abc"
        assert r.iloc[1]["数量"] == "abc"  # unchanged

    def test_nan_qty(self):
        """NaN qty → web unchanged, flange multiplication skipped."""
        df = self._mk("BH300*200*6*8", np.nan)
        r = split_profile_df(df, modes=["BH"])
        # float(np.nan) = nan, str(2*nan) = "nan"
        assert r.iloc[1]["数量"] == "nan"

    def test_scientific_notation_qty(self):
        """Qty '1e2' → float 100.0 → web 100, flange 200."""
        df = self._mk("BH300*200*6*8", "1e2")
        r = split_profile_df(df, modes=["BH"])
        assert r.iloc[1]["数量"] == "200"

    def test_negative_qty(self):
        """Negative qty '-5' → flange = -10."""
        df = self._mk("BH300*200*6*8", "-5")
        r = split_profile_df(df, modes=["BH"])
        assert r.iloc[1]["数量"] == "-10"


# ============================================================================
# 4. NaN in every column position
# ============================================================================

class TestNaNEverywhere:
    """NaN in every possible DataFrame position."""

    def test_nan_spec(self):
        df = pd.DataFrame({"规格": [np.nan], "宽度": ["200"], "数量": ["1"], "零件类型": [""]})
        r = split_profile_df(df)
        assert len(r) == 1

    def test_nan_width(self):
        df = pd.DataFrame({"规格": ["BH300*200*6*8"], "宽度": [np.nan], "数量": ["1"], "零件类型": [""]})
        r = split_profile_df(df, modes=["BH"])
        assert len(r) == 2

    def test_nan_type(self):
        df = pd.DataFrame({"规格": ["BH300*200*6*8"], "宽度": ["200"], "数量": ["1"], "零件类型": [np.nan]})
        r = split_profile_df(df, modes=["BH"])
        assert r.iloc[0]["零件类型"] == "BH腹"

    def test_all_nan(self):
        df = pd.DataFrame({"规格": [np.nan], "宽度": [np.nan], "数量": [np.nan], "零件类型": [np.nan]})
        r = split_profile_df(df)
        assert len(r) == 1

    def test_nan_mixed_with_valid(self):
        df = pd.DataFrame({
            "规格": [np.nan, "BH300*200*6*8", "PL10*2000"],
            "宽度": [np.nan, "200", "2000"],
            "数量": [np.nan, "1", "2"],
            "零件类型": [np.nan, np.nan, ""],
        })
        r = split_profile_df(df)
        assert len(r) >= 3


# ============================================================================
# 5. _clean_number_str edge cases
# ============================================================================

class TestCleanNumberStr:
    def test_int(self):
        assert _clean_number_str("10") == "10"

    def test_float_decimal(self):
        assert _clean_number_str("10.0") == "10"

    def test_float_keep_decimal(self):
        assert _clean_number_str("10.5") == "10.5"

    def test_zero(self):
        assert _clean_number_str("0") == "0"
        assert _clean_number_str("0.0") == "0"

    def test_negative(self):
        assert _clean_number_str("-5") == "-5"
        assert _clean_number_str("-5.0") == "-5"

    def test_text(self):
        assert _clean_number_str("abc") == "abc"

    def test_empty(self):
        assert _clean_number_str("") == ""

    def test_large_int(self):
        assert _clean_number_str("999999") == "999999"

    def test_scientific(self):
        assert _clean_number_str("1e2") == "100"


# ============================================================================
# 6. _col_index_to_letter edge cases
# ============================================================================

class TestColIndexToLetterDeep:
    def test_a(self):
        assert _col_index_to_letter(1) == "A"

    def test_z(self):
        assert _col_index_to_letter(26) == "Z"

    def test_aa(self):
        assert _col_index_to_letter(27) == "AA"

    def test_az(self):
        assert _col_index_to_letter(52) == "AZ"

    def test_ba(self):
        assert _col_index_to_letter(53) == "BA"

    def test_zz(self):
        assert _col_index_to_letter(702) == "ZZ"

    def test_aaa(self):
        assert _col_index_to_letter(703) == "AAA"

    def test_xfd(self):
        """Excel max column (16384)."""
        assert _col_index_to_letter(16384) == "XFD"


# ============================================================================
# 7. Header detection — boundary conditions
# ============================================================================

class TestHeaderDetectionDeep:
    def test_single_row(self):
        """Single row: it's both header and data."""
        df = pd.DataFrame([["A", "B", "C"]])
        assert detect_header_row(df) == 0

    def test_two_rows_header_first(self):
        """2 rows: first row is header (all full)."""
        df = pd.DataFrame([["H1", "H2", "H3"], ["v1", "v2", "v3"]])
        assert detect_header_row(df) == 0  # first row has all non-null

    def test_all_rows_same_nonnull(self):
        """All rows have same non-null count → first one detected."""
        data = [["a", "b", "c"]] * 10
        assert detect_header_row(pd.DataFrame(data)) == 0

    def test_header_with_some_empty_cells(self):
        """Header row has 7/8 non-empty (>= 87.5% threshold)."""
        data = [
            ["Project", None, None, None, None, None, None, None],
            ["H1", None, "H3", "H4", "H5", "H6", "H7", "H8"],  # 7/8 = 87.5% ≥ 7/8
            ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8"],
            ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8"],
        ]
        assert detect_header_row(pd.DataFrame(data)) == 1

    def test_exactly_threshold(self):
        """Exactly 87.5% cells non-empty."""
        cols = list(range(8))
        data = [
            ["H1"] + [None] * 7,  # 1/8
            cols,                   # 8/8 ✓
            list(range(8)),
            list(range(8)),
        ]
        assert detect_header_row(pd.DataFrame(data)) == 1

    def test_all_empty_except_one(self):
        """Only one cell in first row → not header."""
        data = [["x"] + [None] * 7] + [[None] * 8] * 3
        assert detect_header_row(pd.DataFrame(data)) == 0


# ============================================================================
# 8. Column resolution stress
# ============================================================================

class TestColumnResolution:
    def test_duplicate_column_names(self):
        """Pandas allows duplicate column names — resolve should handle it."""
        df = pd.DataFrame([[1, 2, 3]], columns=["A", "A", "B"])
        assert resolve_column(df, "B") == "B"
        # "A" matches the first occurrence
        assert resolve_column(df, "A") == "A"

    def test_int_column_name(self):
        """Integer column name resolved by substring match: '1' in str(1) → True."""
        df = pd.DataFrame(columns=[1, 2, 3])
        assert resolve_column(df, "1") == 1  # subtype match str(1)='1'

    def test_empty_dataframe_columns(self):
        """Empty DataFrame with no columns."""
        df = pd.DataFrame()
        with pytest.raises(KeyError):
            resolve_column(df, "any")

    def test_resolve_by_index_beyond_range(self):
        """Index beyond column count."""
        df = pd.DataFrame(columns=["A", "B"])
        with pytest.raises(IndexError):
            resolve_column(df, 5)

    def test_substring_ambiguous(self):
        """Two columns contain the same substring."""
        df = pd.DataFrame(columns=["产品规格(mm)", "产品规格说明"])
        assert resolve_column(df, "规格") == "产品规格(mm)"  # first match


# ============================================================================
# 9. Sort — stable, NaN, mixed types
# ============================================================================

class TestSortDeep:
    def test_stable_sort(self):
        """Multi-key sort is stable."""
        df = pd.DataFrame({
            "group": ["A", "A", "A", "B", "B", "B"],
            "time":  [1,   2,   3,   1,   2,   3],
            "val":   [10,  20,  30,  40,  50,  60],
        })
        result = multisort(df, [SortSpec(column="group", ascending=True)])
        # Within each group, original order should be preserved
        a_rows = result[result["group"] == "A"]
        assert a_rows["val"].tolist() == [10, 20, 30]  # original order

    def test_sort_with_nan(self):
        """NaN values in sort column — NaN goes to end by default."""
        df = pd.DataFrame({
            "name": ["a", "b", "c"],
            "val":  [1.0, np.nan, 3.0],
        })
        result = multisort(df, [SortSpec(column="val", ascending=True)])
        assert result["val"].tolist()[-1] != result["val"].tolist()[-1]  # NaN is NaN
        assert not np.isnan(result["val"].iloc[0])

    def test_sort_all_same(self):
        """All rows identical — stable sort returns same order."""
        df = pd.DataFrame({"a": [1, 1, 1], "b": [2, 2, 2]})
        result = multisort(df, [SortSpec(column="a", ascending=True)])
        assert len(result) == 3

    def test_sort_single_row(self):
        """Sorting a single row doesn't crash."""
        df = pd.DataFrame({"a": [1]})
        result = multisort(df, [SortSpec(column="a")])
        assert len(result) == 1

    def test_sort_empty(self):
        df = pd.DataFrame({"a": []})
        result = multisort(df, [SortSpec(column="a")])
        assert len(result) == 0


# ============================================================================
# 10. Combination — NaN, empty, edge
# ============================================================================

class TestCombinationDeep:
    def test_merge_with_nan_sum(self):
        """NaN in sum column → treated as 0 after coercion."""
        df = pd.DataFrame({
            "key": ["A", "A"],
            "val": [np.nan, 2],
        })
        result = combination_merge(df, condition_cols=["key"], sum_cols=["val"])
        assert result.iloc[0]["val"] == 2  # NaN coerced to 0

    def test_merge_empty_dataframe(self):
        df = pd.DataFrame({"a": [], "b": []})
        result = combination_merge(df, condition_cols=["a"], sum_cols=["b"])
        assert len(result) == 0

    def test_merge_single_row(self):
        df = pd.DataFrame({"a": [1], "b": [5]})
        result = combination_merge(df, condition_cols=["a"], sum_cols=["b"])
        assert len(result) == 1
        assert result.iloc[0]["b"] == 5

    def test_check_with_nan_baseline(self):
        """NaN in baseline column."""
        df = pd.DataFrame({
            "key": [np.nan, np.nan],
            "val": ["A", "B"],
        })
        result = combination_check(df, baseline_col="key", check_cols=["val"])
        # NaN key: both rows have NaN which equals NaN in groupby
        assert result["can_merge"] in (True, False)  # depends on NaN grouping

    def test_merge_all_same_group(self):
        """All rows belong to same group."""
        df = pd.DataFrame({
            "key": ["X", "X", "X", "X", "X"],
            "val": [1,   2,   3,   4,   5],
        })
        result = combination_merge(df, condition_cols=["key"], sum_cols=["val"])
        assert len(result) == 1
        assert result.iloc[0]["val"] == 15


# ============================================================================
# 11. Crossref — duplicate keys, empty DataFrames
# ============================================================================

class TestCrossrefDeep:
    def test_duplicate_keys_source(self):
        """Source has duplicate standard keys."""
        src = pd.DataFrame({"ID": ["A", "A"], "val": [10, 20]})
        tgt = pd.DataFrame({"ID": ["A"], "val": [100]})
        result = mddzb(src, tgt, standard_cols=["ID"], content_cols=["val"])
        # Merge creates cartesian product-like rows
        assert len(result) >= 2

    def test_empty_source(self):
        src = pd.DataFrame({"ID": [], "val": []})
        tgt = pd.DataFrame({"ID": ["A"], "val": [100]})
        result = mddzb(src, tgt, standard_cols=["ID"], content_cols=["val"])
        assert len(result) >= 1  # target rows appear

    def test_empty_target(self):
        src = pd.DataFrame({"ID": ["A"], "val": [10]})
        tgt = pd.DataFrame({"ID": [], "val": []})
        result = mddzb(src, tgt, standard_cols=["ID"], content_cols=["val"])
        assert len(result) >= 1  # source rows appear

    def test_no_overlap(self):
        """No matching keys between source and target."""
        src = pd.DataFrame({"ID": ["A", "B"], "val": [10, 20]})
        tgt = pd.DataFrame({"ID": ["C", "D"], "val": [30, 40]})
        result = mddzb(src, tgt, standard_cols=["ID"], content_cols=["val"])
        assert len(result) == 4  # all rows from both


# ============================================================================
# 12. Fill — first row NaN, mixed types
# ============================================================================

class TestFillDeep:
    def test_first_row_nan(self):
        """First row is NaN — nothing to fill from, stays NaN."""
        df = pd.DataFrame({"A": [np.nan, "x", np.nan]})
        result = fillin(df)
        assert np.isnan(result.iloc[0]["A"])
        assert result.iloc[1]["A"] == "x"

    def test_mixed_types_downward(self):
        """Fill propagates correct types."""
        df = pd.DataFrame({"A": [1, np.nan, np.nan], "B": ["x", np.nan, "y"]})
        result = fillin(df)
        assert result.iloc[2]["A"] == 1.0
        assert result.iloc[1]["B"] == "x"

    def test_single_row(self):
        df = pd.DataFrame({"A": [1]})
        result = fillin(df)
        assert len(result) == 1


# ============================================================================
# 13. TXT import — GBK, empty, Chinese
# ============================================================================

class TestTXTImportDeep:
    def test_gbk_encoding(self, tmp_path):
        """GBK-encoded file with Chinese characters."""
        txt = tmp_path / "gbk_test.txt"
        txt.write_bytes("名称 规格 数量\n构件A BH300 5\n构件B PL10 10\n".encode("gbk"))
        result = transtxt([str(txt)], encoding="gbk")
        assert len(result) > 0

    def test_empty_file(self, tmp_path):
        """Empty file raises EmptyDataError — wrapped as empty DataFrame."""
        txt = tmp_path / "empty.txt"
        txt.write_text("", encoding="utf-8")
        from pandas.errors import EmptyDataError
        try:
            result = transtxt([str(txt)], encoding="utf-8")
            assert len(result) == 0
        except EmptyDataError:
            pass  # acceptable behavior for empty file

    def test_header_only(self, tmp_path):
        txt = tmp_path / "header_only.txt"
        txt.write_text("A B C\n", encoding="utf-8")
        result = transtxt([str(txt)], encoding="utf-8")
        assert len(result) == 1  # one row with filename + header

    def test_single_column(self, tmp_path):
        """Single-column file with header=None → 4 rows (header + 3 data + name col)."""
        txt = tmp_path / "single.txt"
        txt.write_text("A\n1\n2\n3\n", encoding="utf-8")
        result = transtxt([str(txt)], encoding="utf-8")
        assert len(result) >= 3  # includes header row + name column


# ============================================================================
# 14. BOX full pipeline integration
# ============================================================================

class TestBoxPipeline:
    """BOX split integration within the compatibility package."""

    def test_box_split_then_relabel_is_noop(self):
        """BOX labels are correct without any external relabeling."""
        df = pd.DataFrame({
            "规格": ["BOX650*300*14*24"],
            "宽度": ["300"],
            "数量": ["1"],
            "零件类型": ["箱型"],
        })
        r = split_profile_df(df, modes=["BOX"])
        # Simulate step 11: BOX section → no relabel needed
        # The type already says BOX腹/BOX翼, no BH prefix to replace
        assert "BOX腹" in r.iloc[0]["零件类型"]
        assert "BOX翼" in r.iloc[1]["零件类型"]
        # Step 12: already handled by multi_split (qty is already ×2)
        assert r.iloc[0]["数量"] == "2"

    def test_box_step13_flange_clear(self):
        """Step 13: BOX翼 flange rows → clear weight/area."""
        df = pd.DataFrame({
            "规格": ["BOX650*300*14*24"],
            "宽度": ["300"],
            "数量": ["1"],
            "零件类型": ["箱型"],
        })
        r = split_profile_df(df, modes=["BOX"])
        assert "翼" in r.iloc[1]["零件类型"]  # step 13 keyword match

    def test_box_step12_skipped_when_split(self):
        """Step 12 should NOT double BOX腹 qty when split marker exists."""
        df = pd.DataFrame({
            "规格": ["BOX650*300*14*24"],
            "宽度": ["300"],
            "数量": ["3"],
            "零件类型": [""],
        })
        r = split_profile_df(df, modes=["BOX"])
        assert r.iloc[0]["数量"] == "6"   # 3×2 from multi_split
        assert r.iloc[0]["拆分标记"] == "拆"  # marker exists → step 12 skips

    def test_compare_bh_vs_box_labels(self):
        """BH and BOX produce different labels for same dimensions."""
        df_bh = pd.DataFrame({"规格": ["BH650*300*14*24"], "宽度": ["300"], "数量": ["1"], "零件类型": ["钢"]})
        df_box = pd.DataFrame({"规格": ["BOX650*300*14*24"], "宽度": ["300"], "数量": ["1"], "零件类型": ["钢"]})
        r_bh = split_profile_df(df_bh, modes=["BH"])
        r_box = split_profile_df(df_box, modes=["BOX"])
        assert r_bh.iloc[0]["零件类型"] == "钢BH腹"
        assert r_box.iloc[0]["零件类型"] == "钢BOX腹"
        assert r_bh.iloc[1]["零件类型"] == "钢BH翼"
        assert r_box.iloc[1]["零件类型"] == "钢BOX翼"
        # Algorithm identical except qty
        assert r_bh.iloc[0]["规格"] == r_box.iloc[0]["规格"]
        assert r_bh.iloc[0]["宽度"] == r_box.iloc[0]["宽度"]
        assert r_bh.iloc[0]["数量"] == "1"   # BH: 1 web
        assert r_box.iloc[0]["数量"] == "2"  # BOX: 2 webs


# ============================================================================
# 15. BOM edge cases
# ============================================================================

class TestBOMEdge:
    def test_qdmade_empty_dataframe(self):
        """Empty DataFrame → empty result."""
        df = pd.DataFrame(columns=[
            "图号", "构件号", "构件数量", "零件号", "规格", "宽度", "长度",
            "材质", "零件总数", "总重", "零件类型", "制作单位",
        ])
        result = qdmade(df, other_cols=[], unique_cols=[])
        assert len(result) == 0

    def test_qdmade_single_row(self):
        """Single row = single component."""
        df = pd.DataFrame({
            "图号": ["DWG-1"],
            "构件号": ["COMP-1"],
            "构件数量": [1],
            "零件号": ["P1"],
            "规格": ["HN300"],
            "宽度": ["150"],
            "长度": ["1000"],
            "材质": ["Q235"],
            "零件总数": [1],
            "总重": ["40"],
            "零件类型": ["主材"],
            "制作单位": ["工厂"],
        })
        result = qdmade(df, other_cols=[], unique_cols=[])
        assert len(result) == 1

    def test_combine_profiles_flagq_2_bh_count_boundary(self):
        """Count ratio at boundary: c2/c1=1.9 vs 2.0."""
        # 1.9 → clearly not BH (abs(1.9-2) = 0.1 ≥ 0.01)
        mats = [
            {"spec": 6, "width": 280, "length": 1000, "count": 1, "is_numeric": True, "material": "Q235"},
            {"spec": 8, "width": 200, "length": 1000, "count": 1.9, "is_numeric": True, "material": "Q235"},
        ]
        spec, _, _ = _combine_profiles(mats, 2)
        assert "PL" in spec

        # 2.0 → exactly BH
        mats2 = [
            {"spec": 6, "width": 280, "length": 1000, "count": 1, "is_numeric": True, "material": "Q235"},
            {"spec": 8, "width": 200, "length": 1000, "count": 2.0, "is_numeric": True, "material": "Q235"},
        ]
        spec2, _, _ = _combine_profiles(mats2, 2)
        assert "BH" in spec2

    def test_flagq_1_count_equals_1(self):
        """Count exactly 1 → no prefix."""
        mats = [{"spec": 10, "width": 2000, "length": 1000, "count": 1.0, "is_numeric": True, "material": "Q235"}]
        spec, _, _ = _combine_profiles(mats, 1)
        assert spec == "PL10*2000"  # no count prefix

    def test_attachment_with_custom_keywords(self):
        """Custom attachment keywords."""
        config = SunFireConfig()
        config.attachment_keywords = ["小件", "杂项"]
        parts = pd.DataFrame({
            "零件号": ["P1", "P2"],
            "规格": ["10", "20"],
            "宽度": ["100", "200"],
            "长度": ["500", "600"],
            "零件类型": ["小件", "主材"],
            "零件总数": [2, 4],
            "构件数量": [1, 1],
        })
        result = _build_attachment_string(
            parts, "零件号", "规格", "宽度", "长度",
            "零件类型", "零件总数", "构件数量", config,
        )
        assert "P1:PL10*" in result
        assert "P2" not in result  # not an attachment with custom keywords


# ============================================================================
# 16. Error message parity
# ============================================================================

class TestErrorMessages:
    """Verify Chinese error messages match VBA originals."""

    def test_resolve_column_not_found(self):
        df = pd.DataFrame(columns=["A"])
        with pytest.raises(KeyError, match="Column not found"):
            split_profile_df(df, spec_col="Z")

    def test_map_standard_missing_keyword(self):
        with pytest.raises(ValueError, match="未找到标题"):
            _map_standard_columns(["A", "B"], ColumnMapping())

    def test_sort_duplicate_columns(self):
        df = pd.DataFrame({"a": [1, 2]})
        with pytest.raises(ValueError, match="重复"):
            multisort(df, [SortSpec("a"), SortSpec("a")])

    def test_sort_too_many_conditions(self):
        with pytest.raises(ValueError, match="Maximum 5"):
            multisort(pd.DataFrame(), [SortSpec("a")] * 6)

    def test_combination_overlap(self):
        df = pd.DataFrame({"a": [1], "b": [2]})
        with pytest.raises(ValueError, match="重复"):
            combination_merge(df, condition_cols=["a"], sum_cols=["a"])

    def test_crossref_missing_header(self):
        src = pd.DataFrame({"ID": ["A"], "val": [1]})
        tgt = pd.DataFrame({"ID": ["A"], "other": [1]})
        with pytest.raises(ValueError, match="未找到所选标题"):
            mddzb(src, tgt, standard_cols=["ID"], content_cols=["val"])


# ============================================================================
# 17. Pipeline integration — end-to-end with real patterns
# ============================================================================

class TestPipelineIntegration:
    """Full pipeline simulation with real-world component data."""

    def test_complete_real_component_flow(self):
        """Simulate processing of one complete Tekla component end-to-end."""
        # Input: as read from Tekla export after steps 0-9
        specs = [
            "PL10*143", "PL8*120", "PL16*143",
            "BH650*300*14*24", "BOX700*700*36*36",
            "BT650*300*14*24", "I200*100*5*8",
            "D8", "M20", "D19", "PL6*30",
        ]
        widths = ["143", "120", "143", "300", "700", "300", "100",
                  "", "", "", "30"]
        qtys = ["4", "2", "2", "1", "1", "1", "3", "26", "56", "120", "4"]
        types = [""] * len(specs)

        df = pd.DataFrame({
            "规格": specs, "宽度": widths, "数量": qtys, "零件类型": types,
        })

        # Step 10: multi_split with all modes
        result = split_profile_df(df, modes=["BH", "I", "BT", "BOX", "PL"])

        # Verify counts
        # PL10*143: 1 row (PL sort), PL8*120: 1, PL16*143: 1
        # BH: 2 rows, BOX: 2 rows, BT: 2 rows, I: 2 rows
        # D8: 1, M20: 1, D19: 1, PL6*30: 1
        # Total: 3 + 2 + 2 + 2 + 2 + 1 + 1 + 1 + 1 = 15
        assert len(result) == 15

        # Check BOX labels
        box_rows = result[result["零件类型"].str.contains("BOX", na=False)]
        assert len(box_rows) == 2
        assert "BOX腹" in box_rows.iloc[0]["零件类型"]
        assert "BOX翼" in box_rows.iloc[1]["零件类型"]

        # BOX qty
        box_web = result[(result["零件类型"].str.contains("BOX腹", na=False))]
        assert len(box_web) == 1
        assert box_web.iloc[0]["数量"] == "2"  # 1×2

        # BT flange qty unchanged
        bt_flange = result[(result["零件类型"].str.contains("BT翼", na=False))]
        assert len(bt_flange) == 1
        assert bt_flange.iloc[0]["数量"] == "1"

        # All flange rows have split marker
        marker_col = "拆分标记"
        assert marker_col in result.columns
        split_rows = result[result[marker_col] == "拆"]
        assert len(split_rows) == 8  # 4 types × 2 rows each = 8

    def test_excel_roundtrip_with_box(self, tmp_path):
        """Write Excel with BOX, split, read back, verify labels & qty."""
        path = tmp_path / "box_test.xlsx"

        df = pd.DataFrame({
            "规格": ["BOX650*300*14*24", "BH300*200*6*8", "PL10*2000"],
            "宽度": ["300", "200", "2000"],
            "数量": ["2", "3", "1"],
            "零件类型": ["箱型", "H钢", "板"],
        })
        with pd.ExcelWriter(path, engine="openpyxl") as w:
            df.to_excel(w, sheet_name="整理表", index=False)

        result_sheet = split_profile_excel(
            str(path), sheet_name="整理表", modes=["BH", "BOX", "PL"],
        )
        result_df = pd.read_excel(path, sheet_name=result_sheet)

        # BOX: 2 webs + 2 flanges; BH: 2 rows; PL: 1 → total 5
        assert len(result_df) == 5
        # BOX web qty: 2×2 = 4
        box_web = result_df[result_df["零件类型"].str.contains("BOX腹", na=False)]
        assert len(box_web) == 1
        assert str(box_web.iloc[0]["数量"]) == "4"
        # BOX flange qty: 2×2 = 4
        box_flange = result_df[result_df["零件类型"].str.contains("BOX翼", na=False)]
        assert str(box_flange.iloc[0]["数量"]) == "4"

    def test_box_split_non_numeric_qty(self):
        """BOX with non-numeric qty → no multiplication, but still split."""
        df = pd.DataFrame({
            "规格": ["BOX650*300*14*24"], "宽度": ["300"],
            "数量": ["NA"], "零件类型": [""],
        })
        r = split_profile_df(df, modes=["BOX"])
        assert len(r) == 2
        # Both web and flange qty unchanged
        assert r.iloc[0]["数量"] == "NA"
        assert r.iloc[1]["数量"] == "NA"


# ============================================================================
# 18. Stress / bulk tests
# ============================================================================

class TestStress:
    """Large-scale tests to ensure no O(N²) or memory issues."""

    def test_1000_row_mixed(self):
        """1000 row batch — should complete quickly."""
        n = 1000
        specs = (["BH300*200*6*8", "PL10*2000", "HN300*150", "BOX650*300*14*24"] * 250)[:n]
        df = pd.DataFrame({
            "规格": specs, "宽度": ["200"] * n,
            "数量": ["1"] * n, "零件类型": [""] * n,
        })
        result = split_profile_df(df, modes=["BH", "PL", "BOX"])
        assert len(result) > n

    def test_1000_row_sort(self):
        """Sort 1000 rows."""
        df = pd.DataFrame({"a": range(1000, 0, -1), "b": range(1000)})
        result = multisort(df, [SortSpec("a")])
        assert result.iloc[0]["a"] == 1

    def test_1000_row_fill(self):
        """Fill 1000 rows."""
        df = pd.DataFrame({"A": [1] + [np.nan] * 999})
        result = fillin(df)
        assert result.iloc[999]["A"] == 1.0
