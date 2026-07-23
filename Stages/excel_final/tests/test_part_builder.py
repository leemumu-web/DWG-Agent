from __future__ import annotations

import importlib
from decimal import Decimal

import pytest


def _builder():
    try:
        return importlib.import_module("part_builder")
    except ModuleNotFoundError as exc:
        pytest.fail(f"part builder module is missing: {exc}")


def _candidate(builder, **overrides: object):
    values: dict[str, object] = {
        "source_sheet": "原表",
        "source_row": 8,
        "source_seq": 1,
        "import_component_no": "C1",
        "import_part_no": "p1",
        "spec": Decimal("10"),
        "width": Decimal("100"),
        "cut_length": Decimal("1000"),
        "material": "Q355B",
        "child_quantity": Decimal("2"),
        "component_quantity": Decimal("3"),
        "part_type": "板材",
        "team": "",
        "graphic": "",
        "excluded": False,
    }
    values.update(overrides)
    return builder.PartCandidate(**values)


def test_part_projection_keeps_only_cuttable_types_and_populates_ids() -> None:
    builder = _builder()
    candidates = [
        _candidate(builder, import_part_no="p-board", part_type="板材"),
        _candidate(builder, import_part_no="p-flat", part_type="扁钢", width=None, spec="6*30"),
        _candidate(builder, import_part_no="p-box-web", part_type="BOX腹"),
        _candidate(builder, import_part_no="p-i", part_type="工字钢"),
        _candidate(builder, import_part_no="p-d", part_type="圆钢"),
    ]

    result = builder.build_part_rows(candidates)

    assert {row.import_part_no for row in result.rows} == {"p-board", "p-flat", "p-box-web"}
    component_scoped = [row for row in result.rows if row.part_type == "BOX腹"]
    global_scoped = [row for row in result.rows if row.part_type in {"板材", "扁钢"}]
    assert all(row.import_component_no for row in component_scoped)
    assert all(row.import_component_no == "" for row in global_scoped)
    assert all(row.import_part_no for row in result.rows)


def test_component_scoped_types_keep_component_and_never_cross_components() -> None:
    builder = _builder()
    result = builder.build_part_rows([
        _candidate(builder, import_component_no="C1", part_type="BOX腹"),
        _candidate(builder, import_component_no="C2", part_type="BOX腹"),
    ])

    assert len(result.rows) == 2
    assert {row.import_component_no for row in result.rows} == {"C1", "C2"}
    assert {row.summary for row in result.rows} == {Decimal("6")}


def test_global_types_clear_component_and_merge_across_components() -> None:
    builder = _builder()
    result = builder.build_part_rows([
        _candidate(builder, source_row=8, import_component_no="C1"),
        _candidate(builder, source_row=9, import_component_no="C2"),
    ])

    assert result.issues == ()
    assert len(result.rows) == 1
    assert result.rows[0].import_component_no == ""
    assert result.rows[0].summary == Decimal("12")


def test_global_same_part_number_with_different_attributes_stays_separate() -> None:
    builder = _builder()
    result = builder.build_part_rows([
        _candidate(builder, import_component_no="C1", width=Decimal("100")),
        _candidate(builder, import_component_no="C2", width=Decimal("101")),
    ])

    assert result.issues == ()
    assert len(result.rows) == 2
    assert {row.width for row in result.rows} == {Decimal("100"), Decimal("101")}
    assert {row.import_component_no for row in result.rows} == {""}


def test_global_grouping_keeps_team_boundary() -> None:
    builder = _builder()
    result = builder.build_part_rows([
        _candidate(builder, import_component_no="C1", team="A"),
        _candidate(builder, import_component_no="C2", team="B"),
    ])

    assert len(result.rows) == 2
    assert {row.team for row in result.rows} == {"A", "B"}


def test_summary_preserves_zero_instead_of_defaulting_to_one() -> None:
    builder = _builder()

    result = builder.build_part_rows([
        _candidate(builder, child_quantity=Decimal("0"), component_quantity=Decimal("7"))
    ])

    assert result.rows[0].summary == Decimal("0")


def test_type_priority_then_part_id_and_dimensions_is_stable() -> None:
    builder = _builder()
    priorities = ["板材", "BT翼", "BH翼", "BOX腹", "扁钢", "BT腹", "BH腹", "BOX翼"]
    candidates = [
        _candidate(builder, source_row=index + 1, import_part_no=f"p-{part_type}", part_type=part_type)
        for index, part_type in enumerate(priorities)
    ]

    result = builder.build_part_rows(candidates)

    assert [row.part_type for row in result.rows] == [
        "BH腹", "BH翼", "BOX腹", "BOX翼", "BT腹", "BT翼", "扁钢", "板材"
    ]


def test_dimension_sort_key_is_numeric_with_stable_text_fallback() -> None:
    builder = _builder()
    values = ["100", Decimal("2"), "profile-X", None, Decimal("10")]

    assert sorted(values, key=builder._sort_value) == [
        None,
        Decimal("2"),
        Decimal("10"),
        "100",
        "profile-X",
    ]


def test_same_component_and_part_id_with_conflicting_geometry_is_severe_and_excluded() -> None:
    builder = _builder()
    candidates = [
        _candidate(
            builder,
            source_row=8,
            width=Decimal("100"),
            part_type="BOX腹",
        ),
        _candidate(
            builder,
            source_row=9,
            width=Decimal("101"),
            part_type="BOX腹",
        ),
        _candidate(
            builder,
            source_row=10,
            import_part_no="safe",
            part_type="BOX腹",
        ),
    ]

    result = builder.build_part_rows(candidates)

    assert [row.import_part_no for row in result.rows] == ["safe"]
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.level.value == "严重"
    assert issue.category == "导入零件身份冲突"
    assert issue.affects_part is True
