"""convert persisted MySQL DATETIME values to Beijing wall time

Revision ID: a4c8e1f2b730
Revises: d1e7f3a9c520
Create Date: 2026-08-01
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4c8e1f2b730"
down_revision: str | None = "d1e7f3a9c520"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXPECTED_BUSINESS_DATETIME_COLUMN_COUNT = 126
CELERY_UTC_DATETIME_COLUMNS = frozenset(
    {
        ("celery_taskmeta", "date_done"),
        ("celery_tasksetmeta", "date_done"),
        ("kombu_message", "timestamp"),
    }
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


def _validate_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise RuntimeError(f"unsafe MySQL identifier discovered: {value!r}")
    return value


def _group_datetime_columns(
    rows: Iterable[tuple[str, str]],
) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for raw_table, raw_column in rows:
        table = _validate_identifier(raw_table)
        column = _validate_identifier(raw_column)
        grouped[table].append(column)
    return {table: tuple(columns) for table, columns in sorted(grouped.items())}


def _business_datetime_columns(
    grouped: dict[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    business: dict[str, tuple[str, ...]] = {}
    for table, columns in grouped.items():
        selected = tuple(
            column
            for column in columns
            if (table, column) not in CELERY_UTC_DATETIME_COLUMNS
        )
        if selected:
            business[table] = selected
    return business


def _validate_business_datetime_column_count(
    grouped: dict[str, tuple[str, ...]],
) -> None:
    actual = sum(len(columns) for columns in grouped.values())
    if actual != EXPECTED_BUSINESS_DATETIME_COLUMN_COUNT:
        raise RuntimeError(
            "MySQL business DATETIME column audit drifted: "
            f"expected {EXPECTED_BUSINESS_DATETIME_COLUMN_COUNT}, found {actual}."
        )


def _build_update_sql(
    table: str,
    columns: tuple[str, ...],
    *,
    operation: str,
) -> str:
    table = _validate_identifier(table)
    if operation not in {"ADD", "SUB"}:
        raise ValueError(f"unsupported datetime migration operation: {operation!r}")
    if not columns:
        raise ValueError("at least one DATETIME column is required")
    safe_columns = tuple(_validate_identifier(column) for column in columns)
    assignments = ", ".join(
        f"`{column}` = DATE_{operation}(`{column}`, INTERVAL 8 HOUR)"
        for column in safe_columns
    )
    predicate = " OR ".join(
        f"`{column}` IS NOT NULL" for column in safe_columns
    )
    return f"UPDATE `{table}` SET {assignments} WHERE {predicate}"


def _discover_datetime_columns(connection) -> dict[str, tuple[str, ...]]:
    schema = connection.execute(sa.text("SELECT DATABASE()")).scalar_one()
    if not isinstance(schema, str) or not schema:
        raise RuntimeError("cannot discover the active MySQL schema")
    rows = connection.execute(
        sa.text(
            "SELECT TABLE_NAME, COLUMN_NAME "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = :schema AND DATA_TYPE = 'datetime' "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION"
        ),
        {"schema": schema},
    ).tuples()
    return _group_datetime_columns(rows)


def _convert_mysql_datetimes(operation: str) -> None:
    connection = op.get_bind()
    if connection.dialect.name != "mysql":
        return
    discovered = _discover_datetime_columns(connection)
    grouped = _business_datetime_columns(discovered)
    _validate_business_datetime_column_count(grouped)
    for table, columns in grouped.items():
        connection.execute(
            sa.text(_build_update_sql(table, columns, operation=operation))
        )


def upgrade() -> None:
    _convert_mysql_datetimes("ADD")


def downgrade() -> None:
    _convert_mysql_datetimes("SUB")
