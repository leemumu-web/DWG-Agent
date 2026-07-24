from __future__ import annotations

import importlib
import inspect
import re
from decimal import Decimal
from typing import Any

import pymysql
import pytest


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.row: tuple[Any, ...] | None = None

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.connection.executions.append((sql, params))
        if "LIMIT 0" in sql:
            if self.connection.schema_error is not None:
                raise self.connection.schema_error
            self.row = None
            return
        if self.connection.lookup_error is not None:
            raise self.connection.lookup_error
        table_match = re.search(r"FROM `([^`]+)`", sql)
        assert table_match is not None, sql
        key = (table_match.group(1), str(params[0]) if params else "")
        self.row = self.connection.rows.get(key)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.row

    def fetchall(self) -> tuple[tuple[Any, ...], ...]:
        if self.row is None:
            return ()
        if self.row and isinstance(self.row[0], tuple):
            return self.row
        return (self.row,)


class FakeConnection:
    def __init__(
        self,
        rows: dict[tuple[str, str], tuple[Any, ...]] | None = None,
        *,
        schema_error: Exception | None = None,
        lookup_error: Exception | None = None,
    ) -> None:
        self.rows = rows or {}
        self.schema_error = schema_error
        self.lookup_error = lookup_error
        self.executions: list[tuple[str, tuple[object, ...] | None]] = []
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def close(self) -> None:
        self.closed = True


def _handbook():
    return importlib.import_module("handbook")


def _repository(connection: FakeConnection):
    handbook = _handbook()
    return handbook.SteelHandbookRepository(
        {"database": "injected-test-database"},
        connector=lambda **_config: connection,
    )


def _lookup_sql(connection: FakeConnection) -> list[tuple[str, tuple[object, ...] | None]]:
    return [(sql, params) for sql, params in connection.executions if "LIMIT 0" not in sql]


def test_lookup_requires_category_spec_and_material_for_d_categories() -> None:
    handbook = _handbook()
    repository = _repository(FakeConnection())

    with pytest.raises(handbook.HandbookRequestError, match="category"):
        repository.lookup("", "24")
    with pytest.raises(handbook.HandbookRequestError, match="spec"):
        repository.lookup("round_bar", "")
    with pytest.raises(handbook.HandbookRequestError, match="material"):
        repository.lookup("round_bar", "24")
    with pytest.raises(handbook.HandbookRequestError, match="conflicts"):
        repository.lookup("round_bar", "24", material="HRB400")
    with pytest.raises(handbook.HandbookRequestError, match="conflicts"):
        repository.lookup("rebar", "24", material="Q355B")


def test_each_category_executes_only_its_owned_table_query() -> None:
    connection = FakeConnection(
        {
            ("flat_steel", "6*30"): (Decimal("1.413"),),
            ("round_square_bar", "24"): (Decimal("3.55"),),
            ("rebar", "24"): (Decimal("2.47"),),
        }
    )
    repository = _repository(connection)

    flat = repository.lookup("flat_steel", "6*30")
    round_bar = repository.lookup("round_bar", "24", material="Q355B")
    rebar = repository.lookup("rebar", "24", material="HRB400")

    assert flat.value_kg_per_m == Decimal("1.413")
    assert round_bar.value_kg_per_m == Decimal("3.55")
    assert rebar.value_kg_per_m == Decimal("2.47")
    lookup_sql = _lookup_sql(connection)
    assert len(lookup_sql) == 3
    assert "FROM `flat_steel`" in lookup_sql[0][0]
    assert "FROM `round_square_bar`" in lookup_sql[1][0]
    assert "round_weight" in lookup_sql[1][0]
    assert "FROM `rebar`" in lookup_sql[2][0]
    assert all("material_lookup" not in sql for sql, _ in lookup_sql)


@pytest.mark.parametrize(
    ("category", "source_spec", "database_spec", "table", "weight"),
    [
        ("i_beam", "HI14", "I14", "i_beam", "16.89"),
        ("h_beam", "HN450*200*9*14", "H450*200*9*14", "h_beam", "74.9"),
        ("h_beam", "HW200*200*8*12", "H200*200*8*12", "h_beam", "49.9"),
        ("h_beam", "HT300*150*6.5*9", "H300*150*6.5*9", "h_beam", "36.7"),
        ("t_beam", "TN50*100*6*8", "T50*100*6*8", "t_beam", "8.47"),
        ("channel", "C14A", "[14A", "channel", "14.535"),
        ("steel_pipe", "PIP60*14", "φ60*14", "steel_pipe", "15.884"),
        ("square_tube", "方管100*100*5", "□100*5", "square_tube", "14.915"),
        ("square_tube", "矩形管100*50*4", "□100*50*4", "square_tube", "8.9176"),
        ("square_bar", "方钢20", "20", "round_square_bar", "3.14"),
        ("hfw_pipe", "HFW100*50*2.3*3.2", "LH100*50*2.3*3.2", "hfw_pipe", "4.2"),
        ("hfw_pipe", "LH100*50*2.3*3.2", "LH100*50*2.3*3.2", "hfw_pipe", "4.2"),
        ("w_beam", "W4*13", "W4*13", "w_beam", "19.157454"),
    ],
)
def test_profile_aliases_query_the_existing_database_key(
    category: str,
    source_spec: str,
    database_spec: str,
    table: str,
    weight: str,
) -> None:
    connection = FakeConnection({(table, database_spec): (Decimal(weight),)})
    repository = _repository(connection)

    result = repository.lookup(category, source_spec)

    assert result.status is _handbook().LookupStatus.HIT
    assert result.normalized_spec == source_spec
    assert result.value_kg_per_m == Decimal(weight)
    expected_params = (
        (database_spec, database_spec)
        if category == "w_beam"
        else (database_spec,)
    )
    assert [params for _sql, params in _lookup_sql(connection)] == [expected_params]


def test_q235b_is_an_allowed_round_bar_material_class() -> None:
    connection = FakeConnection(
        {
            ("round_square_bar", "8"): (Decimal("0.395"),),
        }
    )
    repository = _repository(connection)

    result = repository.lookup("round_bar", "8", material="Q235B")

    assert result.status is _handbook().LookupStatus.HIT
    assert result.value_kg_per_m == Decimal("0.395")


def test_cache_key_includes_category_spec_and_d_material_class() -> None:
    connection = FakeConnection(
        {
            ("round_square_bar", "24"): (Decimal("3.55"),),
            ("rebar", "24"): (Decimal("2.47"),),
        }
    )
    repository = _repository(connection)

    repository.lookup("round_bar", "24", material="Q355B")
    repository.lookup("round_bar", "24", material="Q355B")
    repository.lookup("round_bar", "24", material="HPB300")
    repository.lookup("rebar", "24", material="HRB400")

    assert len(_lookup_sql(connection)) == 3


def test_successful_select_without_row_is_not_found_without_formula_fallback() -> None:
    connection = FakeConnection()
    repository = _repository(connection)

    result = repository.lookup("flat_steel", "999*999")

    assert result.status is _handbook().LookupStatus.NOT_FOUND
    assert result.value_kg_per_m is None
    assert result.source == "flat_steel:not_found"
    assert len(_lookup_sql(connection)) == 1


def test_duplicate_source_rows_with_the_same_weight_are_one_lookup_hit() -> None:
    connection = FakeConnection(
        {
            ("flat_steel", "6*60"): (
                (Decimal("2.826"), "扁钢", 11),
                (Decimal("2.826"), "扁钢", 17),
            ),
        }
    )
    repository = _repository(connection)

    result = repository.lookup("flat_steel", "6*60")

    assert result.status is _handbook().LookupStatus.HIT
    assert result.value_kg_per_m == Decimal("2.826")
    assert result.source_refs == ("扁钢!11", "扁钢!17")


def test_duplicate_source_rows_with_conflicting_weights_are_not_arbitrarily_selected() -> None:
    connection = FakeConnection(
        {
            ("hfw_pipe", "LH200*100*3.2*6"): (
                (Decimal("11.86"), "高频焊", 26),
                (Decimal("14.15"), "高频焊", 27),
            ),
        }
    )
    repository = _repository(connection)

    result = repository.lookup("hfw_pipe", "LH200*100*3.2*6")

    assert result.status is _handbook().LookupStatus.CONFLICT
    assert result.value_kg_per_m is None
    assert result.source == "hfw_pipe:conflict"
    assert result.source_refs == ("高频焊!26", "高频焊!27")


def test_connection_failure_is_fatal_infrastructure_error() -> None:
    handbook = _handbook()

    def fail_connection(**_config: object) -> None:
        raise pymysql.OperationalError(2003, "connection refused")

    with pytest.raises(handbook.HandbookInfrastructureError, match="connect"):
        handbook.SteelHandbookRepository({}, connector=fail_connection)


@pytest.mark.parametrize(
    "error",
    [
        pymysql.ProgrammingError(1146, "table missing"),
        pymysql.OperationalError(1054, "column missing"),
    ],
)
def test_missing_table_or_column_during_schema_validation_is_fatal(error: Exception) -> None:
    handbook = _handbook()

    with pytest.raises(handbook.HandbookInfrastructureError, match="schema"):
        _repository(FakeConnection(schema_error=error))


def test_sql_execution_failure_is_fatal_not_not_found() -> None:
    handbook = _handbook()
    repository = _repository(
        FakeConnection(lookup_error=pymysql.OperationalError(2013, "lost connection"))
    )

    with pytest.raises(handbook.HandbookInfrastructureError, match="query"):
        repository.lookup("flat_steel", "6*30")


def test_plate_constant_and_explicit_skip_do_not_query_mysql() -> None:
    connection = FakeConnection()
    repository = _repository(connection)
    before = len(connection.executions)

    plate = repository.lookup("plate", "PL10*200")
    skipped = repository.lookup("skip", "NUT_M24")

    assert plate.status is _handbook().LookupStatus.HIT
    assert plate.value_kg_per_m == Decimal("7.85")
    assert plate.source == "plate_constant:7.85"
    assert skipped.status is _handbook().LookupStatus.SKIPPED
    assert skipped.value_kg_per_m is None
    assert skipped.source == "explicit_skip"
    assert len(connection.executions) == before


def test_bare_dimensions_query_flat_once_then_caller_can_fall_back_to_plate() -> None:
    connection = FakeConnection()
    repository = _repository(connection)

    flat = repository.lookup("flat_steel", "10*143")
    plate = repository.lookup("plate", "10*143")

    assert flat.status is _handbook().LookupStatus.NOT_FOUND
    assert plate.status is _handbook().LookupStatus.HIT
    assert plate.value_kg_per_m == Decimal("7.85")
    assert len(_lookup_sql(connection)) == 1


def test_stage_config_uses_only_explicit_environment_database_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = importlib.import_module("config")
    source = inspect.getsource(config)

    assert config.DB_CONFIG == {}
    assert not hasattr(config, "MISC_WEIGHTS")
    assert "adminer123" not in source
    assert "04120617" not in source
    for name in (
        "DWG_HANDBOOK_MYSQL_HOST",
        "DWG_HANDBOOK_MYSQL_PORT",
        "DWG_HANDBOOK_MYSQL_DATABASE",
        "DWG_HANDBOOK_MYSQL_USER",
        "DWG_HANDBOOK_MYSQL_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    assert config._read_db_config() == {}

    monkeypatch.setenv("DWG_HANDBOOK_MYSQL_HOST", "handbook-db")
    assert config._read_db_config()["database"] == "hardware_handbook"
    monkeypatch.setenv("DWG_HANDBOOK_MYSQL_PORT", "3307")
    monkeypatch.setenv("DWG_HANDBOOK_MYSQL_DATABASE", "golden_handbook")
    monkeypatch.setenv("DWG_HANDBOOK_MYSQL_USER", "readonly")
    monkeypatch.setenv("DWG_HANDBOOK_MYSQL_PASSWORD", "runtime-only")
    assert config._read_db_config() == {
        "host": "handbook-db",
        "port": 3307,
        "database": "golden_handbook",
        "user": "readonly",
        "password": "runtime-only",
        "charset": "utf8mb4",
        "connect_timeout": 5,
    }


def test_pipeline_does_not_swallow_handbook_initialization_failure() -> None:
    pipeline = importlib.import_module("pipeline")
    source = inspect.getsource(pipeline.run_auto_pipeline)

    assert "比重查找将仅使用公式" not in source
    assert "五金手册数据库初始化失败" not in source
