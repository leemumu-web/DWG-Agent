from __future__ import annotations

import pytest

from migrations.versions import a4c8e1f2b730_convert_datetime_to_beijing as migration


def test_beijing_time_migration_extends_current_head():
    assert migration.revision == "a4c8e1f2b730"
    assert migration.down_revision == "d1e7f3a9c520"
    assert migration.EXPECTED_BUSINESS_DATETIME_COLUMN_COUNT == 126
    assert migration.CELERY_UTC_DATETIME_COLUMNS == frozenset(
        {
            ("celery_taskmeta", "date_done"),
            ("celery_tasksetmeta", "date_done"),
            ("kombu_message", "timestamp"),
        }
    )


def test_datetime_columns_are_grouped_in_stable_table_order():
    grouped = migration._group_datetime_columns(
        [
            ("projects", "created_at"),
            ("projects", "updated_at"),
            ("jobs", "finished_at"),
        ]
    )

    assert grouped == {
        "jobs": ("finished_at",),
        "projects": ("created_at", "updated_at"),
    }


@pytest.mark.parametrize(
    ("table_name", "column_name"),
    [
        ("projects`; DROP TABLE projects; --", "created_at"),
        ("projects", "created-at"),
        ("项目", "created_at"),
    ],
)
def test_datetime_column_discovery_rejects_unsafe_identifiers(table_name, column_name):
    with pytest.raises(RuntimeError, match="unsafe MySQL identifier"):
        migration._group_datetime_columns([(table_name, column_name)])


def test_table_update_combines_all_datetime_columns_once():
    upgrade_sql = migration._build_update_sql(
        "projects",
        ("created_at", "updated_at"),
        operation="ADD",
    )
    downgrade_sql = migration._build_update_sql(
        "projects",
        ("created_at", "updated_at"),
        operation="SUB",
    )

    assert upgrade_sql == (
        "UPDATE `projects` SET "
        "`created_at` = DATE_ADD(`created_at`, INTERVAL 8 HOUR), "
        "`updated_at` = DATE_ADD(`updated_at`, INTERVAL 8 HOUR) "
        "WHERE `created_at` IS NOT NULL OR `updated_at` IS NOT NULL"
    )
    assert "DATE_SUB(`created_at`, INTERVAL 8 HOUR)" in downgrade_sql
    assert downgrade_sql.count("UPDATE `projects`") == 1


def test_column_count_drift_refuses_automatic_conversion():
    with pytest.raises(RuntimeError, match="expected 126, found 1"):
        migration._validate_business_datetime_column_count(
            {"projects": ("created_at",)}
        )


def test_celery_protocol_datetimes_are_excluded_from_wall_time_conversion():
    grouped = {
        "celery_taskmeta": ("date_done",),
        "celery_tasksetmeta": ("date_done",),
        "kombu_message": ("timestamp",),
        "projects": ("created_at", "updated_at"),
    }

    assert migration._business_datetime_columns(grouped) == {
        "projects": ("created_at", "updated_at")
    }
