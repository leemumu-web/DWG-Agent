"""add user password reset requirement

Revision ID: c9a1d4e7f620
Revises: b7e2c9a4d610
Create Date: 2026-07-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9a1d4e7f620"
down_revision: str | None = "b7e2c9a4d610"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sys_users",
        sa.Column(
            "password_reset_required",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("sys_users", "password_reset_required")
