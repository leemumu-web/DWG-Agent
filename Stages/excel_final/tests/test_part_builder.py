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
        "file_value": "RECT",
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
    assert all(row.import_component_no for row in result.rows)
    assert all(row.import_part_no for row in result.rows)


def test_grouping_uses_full_key_and_never_crosses_component_or_team() -> None:
    builder = _builder()
    candidates = [
        _candidate(builder, source_row=8),
        _candidate(builder, source_row=9),
        _candidate(builder, source_row=10, import_component_no="C2"),
        _candidate(builder, source_row=11, team="A"),
        _candidate(builder, source_row=12, import_part_no="p-material", material="Q420B"),
    ]

    result = builder.build_part_rows(candidates)

    assert len(result.rows) == 4
    c1_default = next(
        row for row in result.rows
        if row.import_component_no == "C1" and row.team == "" and row.material == "Q355B"
    )
    assert c1_default.summary == Decimal("12")
    assert {row.import_component_no for row in result.rows} == {"C1", "C2"}
    assert {row.team for row in result.rows} == {"", "A"}


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


def test_same_component_and_part_id_with_conflicting_geometry_is_severe_and_excluded() -> None:
    builder = _builder()
    candidates = [
        _candidate(builder, source_row=8, width=Decimal("100")),
        _candidate(builder, source_row=9, width=Decimal("101")),
        _candidate(builder, source_row=10, import_part_no="safe"),
    ]

    result = builder.build_part_rows(candidates)

    assert [row.import_part_no for row in result.rows] == ["safe"]
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.level.value == "严重"
    assert issue.category == "导入零件身份冲突"
    assert issue.affects_part is True
