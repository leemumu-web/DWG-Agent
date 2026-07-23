from __future__ import annotations

import importlib
import sys
from decimal import Decimal
from pathlib import Path

import pytest

pytestmark = pytest.mark.handbook_mysql


@pytest.fixture(scope="module")
def live_handbook_repository():
    try:
        from app.platform.config.settings import settings
    except ModuleNotFoundError:
        pytest.skip("platform settings unavailable; run this test from backend environment")

    stage_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(stage_root))
    handbook = importlib.import_module("handbook")
    try:
        repository = handbook.SteelHandbookRepository(settings.handbook_database_config)
    except Exception as exc:
        pytest.fail(f"live handbook repository initialization failed: {type(exc).__name__}")
    try:
        yield handbook, repository
    finally:
        repository.close()


def test_live_flat_steel_6_by_30(live_handbook_repository) -> None:
    handbook, repository = live_handbook_repository

    result = repository.lookup("flat_steel", "6*30")

    assert result.status is handbook.LookupStatus.HIT
    assert result.value_kg_per_m == Decimal("1.413")
    assert result.source == "flat_steel:flat_steel"


@pytest.mark.parametrize(
    ("diameter", "expected"),
    [("24", Decimal("3.55")), ("30", Decimal("5.55"))],
)
def test_live_round_bar_uses_round_column_only(
    live_handbook_repository,
    diameter: str,
    expected: Decimal,
) -> None:
    handbook, repository = live_handbook_repository

    result = repository.lookup("round_bar", diameter, material="Q355B")

    assert result.status is handbook.LookupStatus.HIT
    assert result.value_kg_per_m is not None
    assert abs(result.value_kg_per_m - expected) <= Decimal("0.01")
    assert result.source == "round_square_bar:round_bar"


def test_live_q235b_d8_uses_round_bar(live_handbook_repository) -> None:
    handbook, repository = live_handbook_repository

    result = repository.lookup("round_bar", "8", material="Q235B")

    assert result.status is handbook.LookupStatus.HIT
    assert result.value_kg_per_m == Decimal("0.395")
    assert result.source == "round_square_bar:round_bar"


@pytest.mark.parametrize(
    ("category", "source_spec", "expected"),
    [
        ("h_beam", "HN450*200*9*14", Decimal("74.9")),
        ("h_beam", "HW200*200*8*12", Decimal("49.9")),
        ("channel", "C14A", Decimal("14.535")),
    ],
)
def test_live_drawing_aliases_match_existing_handbook_keys(
    live_handbook_repository,
    category: str,
    source_spec: str,
    expected: Decimal,
) -> None:
    handbook, repository = live_handbook_repository

    result = repository.lookup(category, source_spec)

    assert result.status is handbook.LookupStatus.HIT
    assert result.normalized_spec == source_spec
    assert result.value_kg_per_m == expected


@pytest.mark.parametrize("diameter", ["24", "30"])
def test_live_rebar_does_not_cross_fallback_to_round_bar(
    live_handbook_repository,
    diameter: str,
) -> None:
    handbook, repository = live_handbook_repository

    result = repository.lookup("rebar", diameter, material="HRB400")

    assert result.status is handbook.LookupStatus.NOT_FOUND
    assert result.value_kg_per_m is None
    assert result.source == "rebar:not_found"


@pytest.mark.parametrize("diameter", ["24", "30"])
def test_same_spec_isolated_by_requested_category(
    live_handbook_repository,
    diameter: str,
) -> None:
    handbook, repository = live_handbook_repository

    round_result = repository.lookup("round_bar", diameter, material="Q355B")
    rebar_result = repository.lookup("rebar", diameter, material="HRB400")

    assert round_result.status is handbook.LookupStatus.HIT
    assert rebar_result.status is handbook.LookupStatus.NOT_FOUND


def test_live_conflicting_source_rows_are_reported_for_manual_review(
    live_handbook_repository,
) -> None:
    handbook, repository = live_handbook_repository

    result = repository.lookup("hfw_pipe", "LH200*100*3.2*6")

    assert result.status is handbook.LookupStatus.CONFLICT
    assert result.value_kg_per_m is None
    assert result.source == "hfw_pipe:conflict"
    assert result.source_refs == ("高频焊!26", "高频焊!27")


@pytest.mark.parametrize(
    ("category", "source_spec", "expected"),
    [
        ("i_beam", "HI14", Decimal("16.89")),
        ("h_beam", "HT300*150*6.5*9", Decimal("36.7")),
        ("t_beam", "TN50*100*6*8", Decimal("8.47")),
        ("steel_pipe", "PIP60*14", Decimal("15.884")),
        ("square_tube", "方管100*100*5", Decimal("14.915")),
        ("square_tube", "矩形管100*50*4", Decimal("8.9176")),
        ("square_bar", "方钢20", Decimal("3.14")),
        ("hfw_pipe", "HFW100*50*2.3*3.2", Decimal("4.2")),
        ("hfw_pipe", "LH100*50*2.3*3.2", Decimal("4.2")),
        ("w_beam", "W4*13", Decimal("19.157454")),
        ("w_beam", "W100*19.3", Decimal("19.157454")),
    ],
)
def test_live_drawing_vocabulary_maps_to_source_workbook_keys(
    live_handbook_repository,
    category: str,
    source_spec: str,
    expected: Decimal,
) -> None:
    _handbook, repository = live_handbook_repository

    result = repository.lookup(category, source_spec)

    assert result.status is _handbook.LookupStatus.HIT
    assert result.value_kg_per_m == expected
