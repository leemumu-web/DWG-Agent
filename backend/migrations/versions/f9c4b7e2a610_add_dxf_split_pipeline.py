"""add DXF split run and item ledgers

Revision ID: f9c4b7e2a610
Revises: d6f3a8c2e710
Create Date: 2026-07-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9c4b7e2a610"
down_revision: str | None = "d6f3a8c2e710"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dxf_split_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workflow_run_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("classification_run_id", sa.BigInteger(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("job_attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("splitter_version", sa.String(length=32), nullable=False),
        sa.Column("cli_schema", sa.String(length=64), nullable=True),
        sa.Column("validation_schema", sa.String(length=64), nullable=True),
        sa.Column("input_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("input_count", sa.Integer(), nullable=False),
        sa.Column("auto_accepted_count", sa.Integer(), nullable=False),
        sa.Column("manual_review_count", sa.Integer(), nullable=False),
        sa.Column("source_contracts_json", sa.JSON(), nullable=True),
        sa.Column("bh_split_ledger_file_id", sa.BigInteger(), nullable=True),
        sa.Column("split_manifest_file_id", sa.BigInteger(), nullable=True),
        sa.Column("validation_report_file_id", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(
            ["classification_run_id"],
            ["dxf_classification_runs.id"],
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["bh_split_ledger_file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["split_manifest_file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["validation_report_file_id"], ["files.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "job_attempt",
            name="uq_dxf_split_job_attempt",
        ),
    )
    op.create_index(
        "ix_dxf_split_runs_workflow_run_id",
        "dxf_split_runs",
        ["workflow_run_id"],
    )
    op.create_index("ix_dxf_split_runs_project_id", "dxf_split_runs", ["project_id"])
    op.create_index(
        "ix_dxf_split_runs_classification_run_id",
        "dxf_split_runs",
        ["classification_run_id"],
    )
    op.create_index("ix_dxf_split_runs_job_id", "dxf_split_runs", ["job_id"])
    op.create_index("ix_dxf_split_runs_status", "dxf_split_runs", ["status"])
    op.create_index(
        "ix_dxf_split_runs_bh_split_ledger_file_id",
        "dxf_split_runs",
        ["bh_split_ledger_file_id"],
    )
    op.create_index(
        "ix_dxf_split_runs_split_manifest_file_id",
        "dxf_split_runs",
        ["split_manifest_file_id"],
    )
    op.create_index(
        "ix_dxf_split_runs_validation_report_file_id",
        "dxf_split_runs",
        ["validation_report_file_id"],
    )
    op.create_index(
        "ix_dxf_split_workflow_attempt",
        "dxf_split_runs",
        ["workflow_run_id", "job_attempt"],
    )

    op.create_table(
        "dxf_split_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("classification_item_id", sa.BigInteger(), nullable=False),
        sa.Column("drawing_id", sa.BigInteger(), nullable=True),
        sa.Column("source_file_id", sa.BigInteger(), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("part_type", sa.String(length=64), nullable=False),
        sa.Column("profile_normalized", sa.String(length=255), nullable=True),
        sa.Column("family", sa.String(length=16), nullable=True),
        sa.Column("source_contract_id", sa.String(length=64), nullable=True),
        sa.Column("automation_route", sa.String(length=32), nullable=False),
        sa.Column("disposition", sa.String(length=64), nullable=False),
        sa.Column("normal_dxf_file_id", sa.BigInteger(), nullable=True),
        sa.Column("weld_allowance_dxf_file_id", sa.BigInteger(), nullable=True),
        sa.Column("split_report_file_id", sa.BigInteger(), nullable=True),
        sa.Column("weld_allowance_report_file_id", sa.BigInteger(), nullable=True),
        sa.Column("diagnostics_json", sa.JSON(), nullable=True),
        sa.Column("validation_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["dxf_split_runs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["classification_item_id"],
            ["dxf_classification_items.id"],
        ),
        sa.ForeignKeyConstraint(["drawing_id"], ["drawings.id"]),
        sa.ForeignKeyConstraint(["source_file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["normal_dxf_file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["weld_allowance_dxf_file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["split_report_file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(
            ["weld_allowance_report_file_id"],
            ["files.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "classification_item_id",
            name="uq_dxf_split_run_classification_item",
        ),
    )
    op.create_index("ix_dxf_split_items_run_id", "dxf_split_items", ["run_id"])
    op.create_index(
        "ix_dxf_split_items_classification_item_id",
        "dxf_split_items",
        ["classification_item_id"],
    )
    op.create_index(
        "ix_dxf_split_items_drawing_id",
        "dxf_split_items",
        ["drawing_id"],
    )
    op.create_index(
        "ix_dxf_split_items_source_file_id",
        "dxf_split_items",
        ["source_file_id"],
    )
    op.create_index(
        "ix_dxf_split_items_normal_dxf_file_id",
        "dxf_split_items",
        ["normal_dxf_file_id"],
    )
    op.create_index(
        "ix_dxf_split_items_weld_allowance_dxf_file_id",
        "dxf_split_items",
        ["weld_allowance_dxf_file_id"],
    )
    op.create_index(
        "ix_dxf_split_items_split_report_file_id",
        "dxf_split_items",
        ["split_report_file_id"],
    )
    op.create_index(
        "ix_dxf_split_items_weld_allowance_report_file_id",
        "dxf_split_items",
        ["weld_allowance_report_file_id"],
    )
    op.create_index(
        "ix_dxf_split_items_route",
        "dxf_split_items",
        ["run_id", "automation_route"],
    )
    op.create_index(
        "ix_dxf_split_items_part_type",
        "dxf_split_items",
        ["run_id", "part_type"],
    )


def downgrade() -> None:
    op.drop_table("dxf_split_items")
    op.drop_table("dxf_split_runs")
