"""add job step attempt generation

Revision ID: a74c2e9f1d30
Revises: 8c61f4d2a9e7
Create Date: 2026-07-11 00:45:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a74c2e9f1d30"
down_revision: str | None = "8c61f4d2a9e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_steps",
        sa.Column("attempt", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.create_index(
        "ix_job_steps_job_id_attempt",
        "job_steps",
        ["job_id", "attempt"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_job_steps_job_id_attempt", table_name="job_steps")
    op.drop_column("job_steps", "attempt")
