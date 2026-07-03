"""add missing timestamp columns

Revision ID: b8f9e7d6c5a4
Revises: 40452ddd24e7
Create Date: 2026-07-03 16:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8f9e7d6c5a4"
down_revision: str | None = "40452ddd24e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TIMESTAMP_TABLES = (
    "project_members",
    "drawing_versions",
    "review_records",
    "agent_run_steps",
)
TIMESTAMP_COLUMNS = ("created_at", "updated_at")


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    return table_name in sa.inspect(bind).get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    columns = sa.inspect(bind).get_columns(table_name)
    return any(column["name"] == column_name for column in columns)


def _quote_identifier(identifier: str) -> str:
    bind = op.get_bind()
    return bind.dialect.identifier_preparer.quote(identifier)


def _backfill_timestamp(table_name: str, column_name: str) -> None:
    table_sql = _quote_identifier(table_name)
    column_sql = _quote_identifier(column_name)
    op.execute(
        sa.text(
            f"UPDATE {table_sql} "
            f"SET {column_sql} = CURRENT_TIMESTAMP "
            f"WHERE {column_sql} IS NULL"
        )
    )


def _add_timestamp_column(table_name: str, column_name: str) -> None:
    if not _table_exists(table_name) or _column_exists(table_name, column_name):
        return

    column_type = sa.DateTime(timezone=True)
    op.add_column(table_name, sa.Column(column_name, column_type, nullable=True))
    _backfill_timestamp(table_name, column_name)
    op.alter_column(
        table_name,
        column_name,
        existing_type=column_type,
        nullable=False,
    )


def upgrade() -> None:
    for table_name in TIMESTAMP_TABLES:
        for column_name in TIMESTAMP_COLUMNS:
            _add_timestamp_column(table_name, column_name)


def downgrade() -> None:
    for table_name in TIMESTAMP_TABLES:
        if not _table_exists(table_name):
            continue
        for column_name in reversed(TIMESTAMP_COLUMNS):
            if _column_exists(table_name, column_name):
                op.drop_column(table_name, column_name)
