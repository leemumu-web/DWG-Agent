"""add daily archive runs

Revision ID: e2f4b8c6a130
Revises: c1e9a4b7d220
Create Date: 2026-07-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2f4b8c6a130"
down_revision: str | None = "c1e9a4b7d220"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_archive_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("archive_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("scope_bucket", sa.String(128)),
        sa.Column("scope_key", sa.String(160), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_file_ids_json", sa.JSON(), nullable=False),
        sa.Column("source_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("bucket_counts_json", sa.JSON(), nullable=False),
        sa.Column("format_counts_json", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("task_id", sa.String(64)),
        sa.Column("archive_file_id", sa.BigInteger()),
        sa.Column("manifest_file_id", sa.BigInteger()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["sys_users.id"]),
        sa.ForeignKeyConstraint(["archive_file_id"], ["files.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["manifest_file_id"], ["files.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_user_id",
            "idempotency_key",
            name="uq_daily_archive_actor_idempotency",
        ),
    )
    op.create_index("ix_daily_archive_runs_actor_user_id", "daily_archive_runs", ["actor_user_id"])
    op.create_index("ix_daily_archive_runs_archive_file_id", "daily_archive_runs", ["archive_file_id"])
    op.create_index("ix_daily_archive_runs_manifest_file_id", "daily_archive_runs", ["manifest_file_id"])
    op.create_index("ix_daily_archive_runs_task_id", "daily_archive_runs", ["task_id"])
    op.create_index(
        "ix_daily_archive_scope_status",
        "daily_archive_runs",
        ["archive_date", "scope_key", "status"],
    )
    op.create_index(
        "ix_daily_archive_manifest",
        "daily_archive_runs",
        ["source_manifest_sha256"],
    )
    op.create_index("ix_daily_archive_created", "daily_archive_runs", ["created_at"])


def downgrade() -> None:
    op.drop_table("daily_archive_runs")
