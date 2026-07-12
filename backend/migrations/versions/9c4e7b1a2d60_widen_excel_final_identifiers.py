"""widen Excel Final identifiers

Revision ID: 9c4e7b1a2d60
Revises: 6d2f8a9c1b40
Create Date: 2026-07-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c4e7b1a2d60"
down_revision: str | None = "6d2f8a9c1b40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "excel_final_parts",
        "component_no",
        existing_type=sa.String(128),
        type_=sa.String(512),
        existing_nullable=True,
    )
    op.alter_column(
        "excel_final_parts",
        "part_type",
        existing_type=sa.String(64),
        type_=sa.String(128),
        existing_nullable=True,
    )
    op.alter_column(
        "excel_final_parts",
        "part_no",
        existing_type=sa.String(128),
        type_=sa.String(255),
        existing_nullable=True,
    )
    op.alter_column(
        "excel_final_parts",
        "profile_spec",
        existing_type=sa.String(128),
        type_=sa.String(255),
        existing_nullable=True,
    )
    op.alter_column(
        "excel_final_parts",
        "spec",
        existing_type=sa.String(64),
        type_=sa.String(128),
        existing_nullable=True,
    )
    op.alter_column(
        "excel_final_components",
        "component_no",
        existing_type=sa.String(128),
        type_=sa.String(512),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "excel_final_components",
        "component_no",
        existing_type=sa.String(512),
        type_=sa.String(128),
        existing_nullable=True,
    )
    op.alter_column(
        "excel_final_parts",
        "spec",
        existing_type=sa.String(128),
        type_=sa.String(64),
        existing_nullable=True,
    )
    op.alter_column(
        "excel_final_parts",
        "profile_spec",
        existing_type=sa.String(255),
        type_=sa.String(128),
        existing_nullable=True,
    )
    op.alter_column(
        "excel_final_parts",
        "part_no",
        existing_type=sa.String(255),
        type_=sa.String(128),
        existing_nullable=True,
    )
    op.alter_column(
        "excel_final_parts",
        "part_type",
        existing_type=sa.String(128),
        type_=sa.String(64),
        existing_nullable=True,
    )
    op.alter_column(
        "excel_final_parts",
        "component_no",
        existing_type=sa.String(512),
        type_=sa.String(128),
        existing_nullable=True,
    )
