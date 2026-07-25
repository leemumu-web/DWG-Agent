"""add workflow batch exports

Revision ID: e9a1b2c3d4f5
Revises: c8f1d2e3a490
Create Date: 2026-07-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9a1b2c3d4f5"
down_revision: str | None = "c8f1d2e3a490"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_files_purged_at", "files", ["purged_at"])

    op.create_table(
        "workflow_batch_exports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("export_uid", sa.String(length=36), nullable=False),
        sa.Column("workflow_run_id", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("categories_json", sa.JSON(), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("source_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
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
            name="fk_workflow_batch_exports_created_by",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.id"],
            name="fk_workflow_batch_exports_workflow",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("export_uid", name="uq_workflow_batch_exports_uid"),
    )
    op.create_index(
        "ix_workflow_batch_exports_workflow_run_id",
        "workflow_batch_exports",
        ["workflow_run_id"],
    )
    op.create_index(
        "ix_workflow_batch_exports_created_by",
        "workflow_batch_exports",
        ["created_by"],
    )
    op.create_index(
        "ix_workflow_batch_exports_status",
        "workflow_batch_exports",
        ["status"],
    )
    op.create_index(
        "ix_workflow_batch_exports_workflow_status",
        "workflow_batch_exports",
        ["workflow_run_id", "status"],
    )
    op.create_index(
        "ix_workflow_batch_exports_creator_created",
        "workflow_batch_exports",
        ["created_by", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_batch_exports_creator_created",
        table_name="workflow_batch_exports",
    )
    op.drop_index(
        "ix_workflow_batch_exports_workflow_status",
        table_name="workflow_batch_exports",
    )
    op.drop_index("ix_workflow_batch_exports_status", table_name="workflow_batch_exports")
    op.drop_index(
        "ix_workflow_batch_exports_created_by",
        table_name="workflow_batch_exports",
    )
    op.drop_index(
        "ix_workflow_batch_exports_workflow_run_id",
        table_name="workflow_batch_exports",
    )
    op.drop_table("workflow_batch_exports")

    op.drop_index("ix_files_purged_at", table_name="files")
    op.drop_column("files", "purged_at")
