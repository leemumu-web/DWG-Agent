from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from app.modules.excel_processing.handbook_catalog_source import (
    AUTHORITATIVE_SOURCE_SHA256,
    DatabaseContent,
    compare_database_content,
    compare_database_manifest,
    expected_database_content,
    expected_database_manifest,
    load_handbook_workbook,
    render_database_sql,
)
from tests.support.paths import REPO_ROOT

SOURCE_WORKBOOK = REPO_ROOT.parent / "五金手册.xls"
SYNC_SCRIPT = REPO_ROOT / "scripts/database/sync_hardware_handbook.py"

EXPECTED_TABLE_COUNTS = {
    "angle": 296,
    "angle_us": 18,
    "channel": 81,
    "checkered_plate": 11,
    "flat_steel": 17,
    "h_beam": 399,
    "h_beam_us": 26,
    "hfw_pipe": 274,
    "high_rise_steel_thickness": 60,
    "i_beam": 90,
    "pipe_convert": 14,
    "rebar": 15,
    "reducer": 4,
    "round_square_bar": 164,
    "square_tube": 101,
    "stainless_steel": 4,
    "steel_pipe": 40,
    "t_beam": 80,
    "u_channel_us": 3,
    "w_beam": 270,
}


@pytest.fixture(scope="module")
def handbook_snapshot():
    if not SOURCE_WORKBOOK.is_file():
        pytest.skip("authoritative hardware-handbook workbook is absent")
    return load_handbook_workbook(SOURCE_WORKBOOK)


def test_authoritative_workbook_has_frozen_identity_and_complete_source_rows(
    handbook_snapshot,
) -> None:
    assert handbook_snapshot.sha256 == AUTHORITATIVE_SOURCE_SHA256
    assert handbook_snapshot.sheet_count == 27
    assert len(handbook_snapshot.source_rows) == 2025
    assert {
        (row.sheet_index, row.sheet_name, row.row_number)
        for row in handbook_snapshot.source_rows
    } == {
        (row.sheet_index, row.sheet_name, row.row_number)
        for records in handbook_snapshot.table_records.values()
        for row in records
    } | {
        (row.sheet_index, row.sheet_name, row.row_number)
        for row in handbook_snapshot.source_rows
        if row.record_type in {"title", "header", "note", "unmapped"}
    }


def test_every_semantic_source_row_maps_to_exactly_one_physical_record(
    handbook_snapshot,
) -> None:
    assert {
        table: len(records)
        for table, records in handbook_snapshot.table_records.items()
    } == EXPECTED_TABLE_COUNTS
    assert handbook_snapshot.semantic_record_count == 1967

    source_coordinates = [
        (record.sheet_index, record.sheet_name, record.row_number)
        for records in handbook_snapshot.table_records.values()
        for record in records
    ]
    assert len(source_coordinates) == len(set(source_coordinates))
    assert all(record.source_row_id > 0 for records in handbook_snapshot.table_records.values() for record in records)


def test_duplicate_source_rows_are_preserved_without_overwriting(
    handbook_snapshot,
) -> None:
    flat_rows = [
        record
        for record in handbook_snapshot.table_records["flat_steel"]
        if record.values["lookup_spec"] == "6*60"
    ]
    assert [(record.sheet_name, record.row_number) for record in flat_rows] == [
        ("扁钢", 11),
        ("扁钢", 17),
    ]
    assert {record.values["weight"] for record in flat_rows} == {Decimal("2.826")}


def test_excel_binary_float_tails_do_not_become_business_values(
    handbook_snapshot,
) -> None:
    flat_row = next(
        record
        for record in handbook_snapshot.table_records["flat_steel"]
        if record.row_number == 18
    )
    square_tube_row = next(
        record
        for record in handbook_snapshot.table_records["square_tube"]
        if record.row_number == 4
    )

    assert flat_row.values["weight"] == Decimal("28.260000000")
    assert square_tube_row.values["weight"] == Decimal("0.896784000")


def test_w_beam_source_columns_have_queryable_combined_keys(
    handbook_snapshot,
) -> None:
    first = handbook_snapshot.table_records["w_beam"][0]

    assert first.values["us_spec1"] == "W4"
    assert first.values["us_spec2"] == "X 13"
    assert first.values["cn_spec"] == "W100 X 19.3"
    assert first.values["lookup_us_spec"] == "W4*13"
    assert first.values["lookup_cn_spec"] == "W100*19.3"


def test_conflicting_source_weights_remain_explicitly_ambiguous(
    handbook_snapshot,
) -> None:
    conflicts = handbook_snapshot.lookup_conflicts["hfw_pipe"]
    assert conflicts["LH200*100*3.2*6"] == (
        Decimal("11.86"),
        Decimal("14.15"),
    )
    assert conflicts["LH200*100*6*8"] == (
        Decimal("18.27"),
        Decimal("21.23"),
    )


def test_generated_database_sql_contains_only_source_backed_tables(
    handbook_snapshot,
) -> None:
    sql = render_database_sql(handbook_snapshot)

    assert "DROP DATABASE IF EXISTS `hardware_handbook`" in sql
    assert "CREATE TABLE `source_workbook`" in sql
    assert "CREATE TABLE `source_row`" in sql
    assert "`source_row_id` bigint NOT NULL" in sql
    assert "FOREIGN KEY (`source_row_id`) REFERENCES `source_row` (`id`)" in sql
    assert "`material_lookup`" not in sql
    assert AUTHORITATIVE_SOURCE_SHA256 in sql
    assert "LH200*100*3.2*6" in sql
    assert "KEY `idx_lookup_enabled`" not in sql
    assert "KEY `idx_lookup_us_spec` (`lookup_us_spec`,`lookup_enabled`)" in sql
    assert "KEY `idx_lookup_cn_spec` (`lookup_cn_spec`,`lookup_enabled`)" in sql


def test_database_manifest_rejects_extra_or_missing_source_records(
    handbook_snapshot,
) -> None:
    expected = expected_database_manifest(handbook_snapshot)
    assert expected["source_workbook"] == 1
    assert expected["source_row"] == 2025
    assert expected["hfw_pipe"] == 274
    assert "material_lookup" not in expected

    assert compare_database_manifest(handbook_snapshot, expected) == ()

    contaminated = dict(expected)
    contaminated["material_lookup"] = 1403
    contaminated["flat_steel"] -= 1
    assert compare_database_manifest(handbook_snapshot, contaminated) == (
        "unexpected table material_lookup has 1403 rows",
        "table flat_steel expected 17 rows but found 16",
    )


def test_exact_database_content_detects_one_changed_source_value(
    handbook_snapshot,
) -> None:
    expected = expected_database_content(handbook_snapshot)
    assert compare_database_content(handbook_snapshot, expected) == ()

    changed_rows = dict(expected.table_rows)
    source_rows = list(changed_rows["source_row"])
    changed = list(source_rows[0])
    changed[6] = '["被改写"]'
    source_rows[0] = tuple(changed)
    changed_rows["source_row"] = tuple(source_rows)

    actual = DatabaseContent(
        manifest=expected.manifest,
        table_rows=changed_rows,
    )
    assert compare_database_content(handbook_snapshot, actual) == (
        "table source_row row 1 column raw_values "
        "expected '[\"\",\"\",\"公称口径\",\"普通钢管\",\"理论重量\",\"螺纹钢筋重量\"]' "
        "but found '[\"被改写\"]'",
    )


def test_exact_database_content_detects_one_changed_physical_weight(
    handbook_snapshot,
) -> None:
    expected = expected_database_content(handbook_snapshot)
    changed_rows = dict(expected.table_rows)
    flat_rows = list(changed_rows["flat_steel"])
    changed = list(flat_rows[0])
    changed[3] = Decimal("999")
    flat_rows[0] = tuple(changed)
    changed_rows["flat_steel"] = tuple(flat_rows)

    actual = DatabaseContent(
        manifest=expected.manifest,
        table_rows=changed_rows,
    )
    problems = compare_database_content(handbook_snapshot, actual)

    assert len(problems) == 1
    assert "table flat_steel" in problems[0]
    assert "column weight" in problems[0]
    assert "Decimal('999')" in problems[0]


def test_sql_can_target_an_isolated_validation_database(handbook_snapshot) -> None:
    sql = render_database_sql(
        handbook_snapshot,
        database_name="hardware_handbook_validation",
    )

    assert "DROP DATABASE IF EXISTS `hardware_handbook_validation`" in sql
    assert "CREATE DATABASE `hardware_handbook_validation`" in sql
    assert "USE `hardware_handbook_validation`" in sql


def test_sync_command_generates_sql_with_machine_readable_source_summary(
    tmp_path: Path,
) -> None:
    if not SOURCE_WORKBOOK.is_file():
        pytest.skip("authoritative hardware-handbook workbook is absent")
    output = tmp_path / "hardware-handbook.sql"

    completed = subprocess.run(
        [
            sys.executable,
            str(SYNC_SCRIPT),
            str(SOURCE_WORKBOOK),
            "--output-sql",
            str(output),
            "--database-name",
            "hardware_handbook_validation",
        ],
        cwd=REPO_ROOT / "backend",
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary == {
        "database_name": "hardware_handbook_validation",
        "lookup_conflict_count": 2,
        "output_sql": str(output.resolve()),
        "semantic_record_count": 1967,
        "source_row_count": 2025,
        "source_sha256": AUTHORITATIVE_SOURCE_SHA256,
    }
    assert output.is_file()
    assert "CREATE DATABASE `hardware_handbook_validation`" in output.read_text()
