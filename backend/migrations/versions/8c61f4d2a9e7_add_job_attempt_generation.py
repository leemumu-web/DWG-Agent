"""add job attempt generation

Revision ID: 8c61f4d2a9e7
Revises: 7f2a9c4e6b10
Create Date: 2026-07-11 00:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8c61f4d2a9e7"
down_revision: str | None = "7f2a9c4e6b10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("attempt", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("jobs", "attempt")
