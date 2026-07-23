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
