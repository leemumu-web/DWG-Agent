"""widen excel_final string columns

Revision ID: a1b2c3d4e5f6
Revises: 3480bd86ddc3
Create Date: 2026-07-10
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: str | None = '3480bd86ddc3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Widen columns to accommodate real-world steel part data."""
    op.alter_column('excel_final_parts', 'component_no',
                    existing_type=sa.String(128), type_=sa.String(512))
    op.alter_column('excel_final_parts', 'part_type',
                    existing_type=sa.String(64), type_=sa.String(128))
    op.alter_column('excel_final_parts', 'part_no',
                    existing_type=sa.String(128), type_=sa.String(255))
    op.alter_column('excel_final_parts', 'profile_spec',
                    existing_type=sa.String(128), type_=sa.String(255))
    op.alter_column('excel_final_parts', 'spec',
                    existing_type=sa.String(64), type_=sa.String(128))
    op.alter_column('excel_final_components', 'component_no',
                    existing_type=sa.String(128), type_=sa.String(512))


def downgrade() -> None:
    op.alter_column('excel_final_parts', 'spec',
                    existing_type=sa.String(128), type_=sa.String(64))
    op.alter_column('excel_final_parts', 'profile_spec',
                    existing_type=sa.String(255), type_=sa.String(128))
    op.alter_column('excel_final_parts', 'part_no',
                    existing_type=sa.String(255), type_=sa.String(128))
    op.alter_column('excel_final_parts', 'part_type',
                    existing_type=sa.String(128), type_=sa.String(64))
    op.alter_column('excel_final_parts', 'component_no',
                    existing_type=sa.String(512), type_=sa.String(128))
    op.alter_column('excel_final_components', 'component_no',
                    existing_type=sa.String(512), type_=sa.String(128))
