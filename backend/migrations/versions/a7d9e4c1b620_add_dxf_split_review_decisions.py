"""add DXF split progress, candidates and review decisions

Revision ID: a7d9e4c1b620
Revises: f9c4b7e2a610
Create Date: 2026-07-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7d9e4c1b620"
down_revision: str | None = "f9c4b7e2a610"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dxf_split_runs",
        sa.Column(
            "processed_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    candidate_columns = (
        "candidate_normal_dxf_file_id",
        "candidate_weld_allowance_dxf_file_id",
        "candidate_split_report_file_id",
        "candidate_weld_allowance_report_file_id",
    )
    for column_name in candidate_columns:
        op.add_column(
            "dxf_split_items",
            sa.Column(column_name, sa.BigInteger(), nullable=True),
        )
        op.create_foreign_key(
            f"fk_dxf_split_items_{column_name}",
            "dxf_split_items",
            "files",
            [column_name],
            ["id"],
        )
        op.create_index(
            f"ix_dxf_split_items_{column_name}",
            "dxf_split_items",
            [column_name],
        )

    op.create_table(
        "dxf_split_review_decisions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("split_item_id", sa.BigInteger(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("final_normal_dxf_file_id", sa.BigInteger(), nullable=True),
        sa.Column("final_weld_allowance_dxf_file_id", sa.BigInteger(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.BigInteger(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
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
        sa.CheckConstraint(
            "decision IN ('accept_candidate', 'manual_processing')",
            name="ck_dxf_split_review_decision",
        ),
        sa.ForeignKeyConstraint(
            ["split_item_id"],
            ["dxf_split_items.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["final_normal_dxf_file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["final_weld_allowance_dxf_file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["decided_by"], ["sys_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("split_item_id", name="uq_dxf_split_review_item"),
    )
    op.create_index(
        "ix_dxf_split_review_decisions_split_item_id",
        "dxf_split_review_decisions",
        ["split_item_id"],
    )
    op.create_index(
        "ix_dxf_split_review_decisions_final_normal_dxf_file_id",
        "dxf_split_review_decisions",
        ["final_normal_dxf_file_id"],
    )
    op.create_index(
        "ix_dxf_split_review_decisions_final_weld_allowance_dxf_file_id",
        "dxf_split_review_decisions",
        ["final_weld_allowance_dxf_file_id"],
    )
    op.create_index(
        "ix_dxf_split_review_decider",
        "dxf_split_review_decisions",
        ["decided_by", "decided_at"],
    )


def downgrade() -> None:
    op.drop_table("dxf_split_review_decisions")
    candidate_columns = (
        "candidate_weld_allowance_report_file_id",
        "candidate_split_report_file_id",
        "candidate_weld_allowance_dxf_file_id",
        "candidate_normal_dxf_file_id",
    )
    for column_name in candidate_columns:
        op.drop_index(
            f"ix_dxf_split_items_{column_name}",
            table_name="dxf_split_items",
        )
        op.drop_constraint(
            f"fk_dxf_split_items_{column_name}",
            "dxf_split_items",
            type_="foreignkey",
        )
        op.drop_column("dxf_split_items", column_name)
    op.drop_column("dxf_split_runs", "processed_count")
