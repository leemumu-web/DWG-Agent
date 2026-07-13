"""add job request key

Revision ID: d5e8a1c4b720
Revises: 9c4e7b1a2d60
Create Date: 2026-07-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e8a1c4b720"
down_revision: str | None = "9c4e7b1a2d60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("request_key", sa.String(length=128), nullable=True))
    op.create_unique_constraint(
        "uq_jobs_actor_task_request_key",
        "jobs",
        ["created_by", "task_type", "request_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_jobs_actor_task_request_key", "jobs", type_="unique")
    op.drop_column("jobs", "request_key")
