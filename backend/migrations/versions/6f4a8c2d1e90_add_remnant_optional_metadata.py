"""add optional remnant project and storage metadata

Revision ID: 6f4a8c2d1e90
Revises: 9d6e4a1b2c70
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6f4a8c2d1e90"
down_revision: str | None = "9d6e4a1b2c70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table, prefix in (
        ("remnant_import_items", "corrected_"),
        ("remnants", ""),
    ):
        op.add_column(table, sa.Column(f"{prefix}project_no_secondary", sa.String(128)))
        op.add_column(table, sa.Column(f"{prefix}storage_location", sa.String(128)))
        op.add_column(table, sa.Column(f"{prefix}remark_1", sa.String(500)))
        op.add_column(table, sa.Column(f"{prefix}remark_2", sa.String(500)))


def downgrade() -> None:
    for table, prefix in (
        ("remnants", ""),
        ("remnant_import_items", "corrected_"),
    ):
        op.drop_column(table, f"{prefix}remark_2")
        op.drop_column(table, f"{prefix}remark_1")
        op.drop_column(table, f"{prefix}storage_location")
        op.drop_column(table, f"{prefix}project_no_secondary")
