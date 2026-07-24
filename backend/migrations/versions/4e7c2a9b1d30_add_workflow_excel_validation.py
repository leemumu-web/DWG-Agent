"""add workflow Excel validation snapshots

Revision ID: 4e7c2a9b1d30
Revises: 7c4d9e2a1b60
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4e7c2a9b1d30"
down_revision: str | None = "7c4d9e2a1b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_input_items",
        sa.Column("validation_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "workflow_input_items",
        sa.Column("validation_contract_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "workflow_input_items",
        sa.Column("validated_sha256", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflow_input_items", "validated_sha256")
    op.drop_column("workflow_input_items", "validation_contract_version")
    op.drop_column("workflow_input_items", "validation_json")
