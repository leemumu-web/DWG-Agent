from __future__ import annotations

from tools.compare_supervised_sample import (
    _match_rows,
    compare_shared_fields,
    normalized_part_no,
    part_key,
    values_equal,
)


def test_shared_nonempty_comparison_ignores_gt_and_output_only_values() -> None:
    result = compare_shared_fields(
        {"长度(mm)": 1000, "下料长度(mm)": None, "材质": "Q355B"},
        {"长度(mm)": 1000, "下料长度(mm)": 1010, "材质": None},
        fields=("长度(mm)", "下料长度(mm)", "材质"),
    )

    assert result.compared == {"长度(mm)": (1000, 1000)}
    assert result.differences == {}


def test_numeric_comparison_uses_display_precision_without_hiding_real_difference() -> None:
    assert values_equal(1.2344, 1.234, field="理单重(kg)")
    assert not values_equal(1.236, 1.234, field="理单重(kg)")
    assert values_equal(0.338, 0.34, field="总表面积(㎡)")
    assert not values_equal(1001, 1000, field="长度(mm)")


def test_part_complete_key_keeps_different_dimensions_separate() -> None:
    assert part_key({
        "导入构件编号": None,
        "导入零件号": "P1",
        "规格": 10,
        "宽度": 100,
        "下料长度": 1000,
        "材质": "Q355B",
    }) != part_key({
        "导入构件编号": None,
        "导入零件号": "P1",
        "规格": 10,
        "宽度": 120,
        "下料长度": 1000,
        "材质": "Q355B",
    })


def test_box_cover_identity_normalizes_to_box_flange() -> None:
    assert normalized_part_no("P1-BOX盖") == "P1-BOX翼"
    assert normalized_part_no("P1BOX盖") == "P1BOX翼"


def test_part_complete_key_normalizes_integer_like_numbers() -> None:
    left = {
        "导入构件编号": "C1",
        "导入零件号": "P1-BOX盖",
        "规格": 10,
        "宽度": 100.0,
        "下料长度": 1000,
        "材质": "Q355B",
    }
    right = {
        "导入构件编号": "C1",
        "导入零件号": "P1-BOX翼",
        "规格": 10.0,
        "宽度": 100,
        "下料长度": 1000.0,
        "材质": "Q355B",
    }

    assert part_key(left) == part_key(right)


def test_organized_matching_uses_shared_part_identity_when_gt_component_is_blank() -> None:
    program = [{
        "__row__": 2,
        "导入构件编号": "C1",
        "导入零件号": "P1",
        "规格": 10,
        "宽度": 100,
        "下料长度(mm)": 1000,
        "材质": "Q355B",
    }]
    ground_truth = [{
        "__row__": 2,
        "导入构件编号": None,
        "导入零件号": "P1",
        "规格": 10,
        "宽度": 100,
        "下料长度(mm)": 1000,
        "材质": "Q355B",
    }]

    matches, unmatched_program, unmatched_gt = _match_rows(
        "整理表",
        program,
        ground_truth,
    )

    assert len(matches) == 1
    assert matches[0].match_kind == "共享零件身份消歧"
    assert unmatched_program == []
    assert unmatched_gt == []


def test_organized_matching_does_not_cross_conflicting_nonempty_components() -> None:
    program = [{
        "__row__": 2,
        "导入构件编号": "C1",
        "导入零件号": "P1",
        "规格": 10,
        "宽度": 100,
        "下料长度(mm)": 1000,
        "材质": "Q355B",
    }]
    ground_truth = [{
        "__row__": 2,
        "导入构件编号": "C2",
        "导入零件号": "P1",
        "规格": 10,
        "宽度": 100,
        "下料长度(mm)": 1000,
        "材质": "Q355B",
    }]

    matches, unmatched_program, unmatched_gt = _match_rows(
        "整理表",
        program,
        ground_truth,
    )

    assert matches == []
    assert program == unmatched_program
    assert ground_truth == unmatched_gt


def test_organized_shared_identity_uses_cut_length_to_disambiguate_duplicates() -> None:
    program = [
        {
            "__row__": 2,
            "导入构件编号": "C1",
            "导入零件号": "P1",
            "规格": 10,
            "宽度": 100,
            "下料长度(mm)": 1000,
            "材质": "Q355B",
        },
        {
            "__row__": 3,
            "导入构件编号": "C2",
            "导入零件号": "P1",
            "规格": 10,
            "宽度": 100,
            "下料长度(mm)": 1001,
            "材质": "Q355B",
        },
    ]
    ground_truth = [
        {
            "__row__": 2,
            "导入构件编号": None,
            "导入零件号": "P1",
            "规格": 10,
            "宽度": 100,
            "下料长度(mm)": 1001,
            "材质": "Q355B",
        },
        {
            "__row__": 3,
            "导入构件编号": None,
            "导入零件号": "P1",
            "规格": 10,
            "宽度": 100,
            "下料长度(mm)": 1000,
            "材质": "Q355B",
        },
    ]

    matches, unmatched_program, unmatched_gt = _match_rows(
        "整理表",
        program,
        ground_truth,
    )

    assert {
        (
            match.program["下料长度(mm)"],
            match.ground_truth["下料长度(mm)"],
        )
        for match in matches
    } == {(1000, 1000), (1001, 1001)}
    assert not any(match.ambiguous for match in matches)
    assert unmatched_program == []
    assert unmatched_gt == []
