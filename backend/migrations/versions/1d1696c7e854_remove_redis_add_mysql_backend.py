"""remove_redis_add_mysql_backend

Revision ID: 1d1696c7e854
Revises: 53cd59adf848
Create Date: 2026-07-10 13:41:43.105405
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1d1696c7e854"
down_revision: str | None = "53cd59adf848"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_memory",
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("messages", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_table(
        "token_blacklist",
        sa.Column("jti", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("jti"),
    )
    op.create_index(
        op.f("ix_token_blacklist_expires_at"),
        "token_blacklist",
        ["expires_at"],
        unique=False,
    )
    op.add_column("jobs", sa.Column("progress_data", sa.JSON(), nullable=True))
    op.add_column(
        "sys_users",
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sys_users", "password_changed_at")
    op.drop_column("jobs", "progress_data")
    op.drop_index(op.f("ix_token_blacklist_expires_at"), table_name="token_blacklist")
    op.drop_table("token_blacklist")
    op.drop_table("agent_memory")
