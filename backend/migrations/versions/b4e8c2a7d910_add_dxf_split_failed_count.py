"""add persisted DXF split failure progress

Revision ID: b4e8c2a7d910
Revises: a7d9e4c1b620
Create Date: 2026-07-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4e8c2a7d910"
down_revision: str | None = "a7d9e4c1b620"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dxf_split_runs",
        sa.Column(
            "failed_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("dxf_split_runs", "failed_count")
