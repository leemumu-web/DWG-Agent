"""persist classifier and splitter type resolution

Revision ID: c8f1d2e3a490
Revises: b4e8c2a7d910
Create Date: 2026-07-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8f1d2e3a490"
down_revision: str | None = "b4e8c2a7d910"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dxf_split_items",
        sa.Column("classification_disposition", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "dxf_split_items",
        sa.Column("classification_part_type", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "dxf_split_items",
        sa.Column("type_resolution", sa.String(length=32), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE dxf_split_items
            SET classification_disposition = 'classified',
                classification_part_type = part_type,
                type_resolution = CASE
                    WHEN automation_route = 'auto_accepted'
                         AND family IN ('BH', 'BOX')
                    THEN 'classifier_confirmed'
                    ELSE 'unresolved'
                END
            """
        )
    )
    op.alter_column(
        "dxf_split_items",
        "classification_disposition",
        existing_type=sa.String(length=32),
        nullable=False,
    )
    op.alter_column(
        "dxf_split_items",
        "type_resolution",
        existing_type=sa.String(length=32),
        nullable=False,
    )
    op.create_index(
        "ix_dxf_split_items_type_resolution",
        "dxf_split_items",
        ["run_id", "type_resolution"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dxf_split_items_type_resolution",
        table_name="dxf_split_items",
    )
    op.drop_column("dxf_split_items", "type_resolution")
    op.drop_column("dxf_split_items", "classification_part_type")
    op.drop_column("dxf_split_items", "classification_disposition")
