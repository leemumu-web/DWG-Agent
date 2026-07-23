"""add excel final provenance and quality fields

Revision ID: f3a7c9d2e6b1
Revises: e2f4b8c6a130
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a7c9d2e6b1"
down_revision: str | None = "e2f4b8c6a130"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "excel_final_batches",
        sa.Column("quality_status", sa.String(32), server_default="ok", nullable=False),
    )
    op.add_column(
        "excel_final_batches",
        sa.Column("warning_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "excel_final_batches",
        sa.Column("severe_warning_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("excel_final_batches", sa.Column("report_summary", sa.JSON(), nullable=True))

    op.add_column(
        "excel_final_parts", sa.Column("import_component_no", sa.String(512), nullable=True)
    )
    op.add_column("excel_final_parts", sa.Column("import_part_no", sa.String(255), nullable=True))
    op.add_column("excel_final_parts", sa.Column("source_batch", sa.String(255), nullable=True))
    op.add_column("excel_final_parts", sa.Column("team", sa.String(128), nullable=True))
    op.add_column("excel_final_parts", sa.Column("original_qty", sa.Float(), nullable=True))
    op.add_column("excel_final_parts", sa.Column("density_source", sa.String(255), nullable=True))
    op.add_column("excel_final_parts", sa.Column("material_utilization", sa.Float(), nullable=True))
    op.add_column("excel_final_parts", sa.Column("weight_validation", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("excel_final_parts", "weight_validation")
    op.drop_column("excel_final_parts", "material_utilization")
    op.drop_column("excel_final_parts", "density_source")
    op.drop_column("excel_final_parts", "original_qty")
    op.drop_column("excel_final_parts", "team")
    op.drop_column("excel_final_parts", "source_batch")
    op.drop_column("excel_final_parts", "import_part_no")
    op.drop_column("excel_final_parts", "import_component_no")

    op.drop_column("excel_final_batches", "report_summary")
    op.drop_column("excel_final_batches", "severe_warning_count")
    op.drop_column("excel_final_batches", "warning_count")
    op.drop_column("excel_final_batches", "quality_status")
