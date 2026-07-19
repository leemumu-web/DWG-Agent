"""add workflow input batches

Revision ID: f7a9c2d4e610
Revises: d5e8a1c4b720
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7a9c2d4e610"
down_revision: str | None = "d5e8a1c4b720"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_input_batches",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workflow_run_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["sys_users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_run_id", name="uq_workflow_input_batch_workflow"),
    )
    op.create_index("ix_workflow_input_batches_workflow_run_id", "workflow_input_batches", ["workflow_run_id"])
    op.create_index("ix_workflow_input_batches_project_id", "workflow_input_batches", ["project_id"])
    op.create_index("ix_workflow_input_batches_status", "workflow_input_batches", ["status"])
    op.create_index("ix_workflow_input_batches_project_status", "workflow_input_batches", ["project_id", "status"])

    op.create_table(
        "workflow_input_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("input_batch_id", sa.BigInteger(), nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_stem", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("conversion_job_id", sa.BigInteger(), nullable=True),
        sa.Column("conversion_job_attempt", sa.Integer(), nullable=True),
        sa.Column("derived_dxf_file_id", sa.BigInteger(), nullable=True),
        sa.Column("drawing_id", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["conversion_job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["derived_dxf_file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["drawing_id"], ["drawings.id"]),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["input_batch_id"], ["workflow_input_batches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("input_batch_id", "file_id", name="uq_workflow_input_item_file"),
    )
    op.create_index("ix_workflow_input_items_input_batch_id", "workflow_input_items", ["input_batch_id"])
    op.create_index("ix_workflow_input_items_file_id", "workflow_input_items", ["file_id"])
    op.create_index("ix_workflow_input_items_status", "workflow_input_items", ["status"])
    op.create_index("ix_workflow_input_items_conversion_job_id", "workflow_input_items", ["conversion_job_id"])
    op.create_index("ix_workflow_input_items_derived_dxf_file_id", "workflow_input_items", ["derived_dxf_file_id"])
    op.create_index("ix_workflow_input_items_drawing_id", "workflow_input_items", ["drawing_id"])
    op.create_index("ix_workflow_input_items_batch_role", "workflow_input_items", ["input_batch_id", "role"])


def downgrade() -> None:
    op.drop_table("workflow_input_items")
    op.drop_table("workflow_input_batches")
