"""add workflow framework

Revision ID: e4a1c7f2b930
Revises: a74c2e9f1d30
Create Date: 2026-07-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4a1c7f2b930"
down_revision: str | None = "a74c2e9f1d30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("workflow_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_stage", sa.String(length=64), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["sys_users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_runs_project_id", "workflow_runs", ["project_id"])
    op.create_index("ix_workflow_runs_created_by", "workflow_runs", ["created_by"])
    op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"])
    op.create_index("ix_workflow_runs_project_status", "workflow_runs", ["project_id", "status"])
    op.create_index("ix_workflow_runs_created_by_status", "workflow_runs", ["created_by", "status"])

    op.create_table(
        "workflow_stage_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workflow_run_id", sa.BigInteger(), nullable=False),
        sa.Column("stage_code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("job_attempt", sa.Integer(), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_run_id", "stage_code", name="uq_workflow_stage_code"),
    )
    op.create_index("ix_workflow_stage_runs_workflow_run_id", "workflow_stage_runs", ["workflow_run_id"])
    op.create_index("ix_workflow_stage_runs_status", "workflow_stage_runs", ["status"])
    op.create_index("ix_workflow_stage_runs_job_id", "workflow_stage_runs", ["job_id"])
    op.create_index("ix_workflow_stage_runs_workflow_sequence", "workflow_stage_runs", ["workflow_run_id", "sequence"])
    op.create_index("ix_workflow_stage_runs_job_attempt", "workflow_stage_runs", ["job_id", "job_attempt"])

    op.create_table(
        "workflow_artifacts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workflow_run_id", sa.BigInteger(), nullable=False),
        sa.Column("stage_run_id", sa.BigInteger(), nullable=True),
        sa.Column("artifact_type", sa.String(length=64), nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=True),
        sa.Column("result_id", sa.BigInteger(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["result_id"], ["analysis_results.id"]),
        sa.ForeignKeyConstraint(["stage_run_id"], ["workflow_stage_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_artifacts_workflow_run_id", "workflow_artifacts", ["workflow_run_id"])
    op.create_index("ix_workflow_artifacts_stage_run_id", "workflow_artifacts", ["stage_run_id"])
    op.create_index("ix_workflow_artifacts_file_id", "workflow_artifacts", ["file_id"])
    op.create_index("ix_workflow_artifacts_result_id", "workflow_artifacts", ["result_id"])
    op.create_index("ix_workflow_artifacts_workflow_type", "workflow_artifacts", ["workflow_run_id", "artifact_type"])


def downgrade() -> None:
    op.drop_table("workflow_artifacts")
    op.drop_table("workflow_stage_runs")
    op.drop_table("workflow_runs")
