"""add workflow retention exports

Revision ID: b7e2c9a4d610
Revises: e9a1b2c3d4f5
Create Date: 2026-07-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e2c9a4d610"
down_revision: str | None = "e9a1b2c3d4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_retention_exports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("export_uid", sa.String(length=36), nullable=False),
        sa.Column("workflow_run_id", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("preview_cache_count", sa.Integer(), nullable=False),
        sa.Column("source_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("reclaimable_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("purge_transfer_uid", sa.String(length=36), nullable=True),
        sa.Column("purge_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_file_count", sa.Integer(), nullable=False),
        sa.Column("purged_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["sys_users.id"],
            name="fk_workflow_retention_exports_created_by",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.id"],
            name="fk_workflow_retention_exports_workflow",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("export_uid", name="uq_workflow_retention_exports_uid"),
    )
    op.create_index(
        "ix_workflow_retention_exports_workflow_run_id",
        "workflow_retention_exports",
        ["workflow_run_id"],
    )
    op.create_index(
        "ix_workflow_retention_exports_created_by",
        "workflow_retention_exports",
        ["created_by"],
    )
    op.create_index(
        "ix_workflow_retention_exports_status",
        "workflow_retention_exports",
        ["status"],
    )
    op.create_index(
        "ix_workflow_retention_exports_task_id",
        "workflow_retention_exports",
        ["task_id"],
    )
    op.create_index(
        "ix_workflow_retention_exports_purge_transfer_uid",
        "workflow_retention_exports",
        ["purge_transfer_uid"],
    )
    op.create_index(
        "ix_workflow_retention_exports_workflow_status",
        "workflow_retention_exports",
        ["workflow_run_id", "status"],
    )
    op.create_index(
        "ix_workflow_retention_exports_creator_created",
        "workflow_retention_exports",
        ["created_by", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("workflow_retention_exports")
