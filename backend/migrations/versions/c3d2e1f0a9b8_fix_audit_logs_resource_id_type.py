"""fix audit_logs.resource_id type to BIGINT for consistency with all other ID columns

Revision ID: c3d2e1f0a9b8
Revises: b8f9e7d6c5a4
Create Date: 2026-07-04 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d2e1f0a9b8"
down_revision: str | None = "b8f9e7d6c5a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _pk_type() -> sa.BigInteger:
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.alter_column(
            "resource_id",
            existing_type=sa.Integer(),
            type_=_pk_type(),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.alter_column(
            "resource_id",
            existing_type=_pk_type(),
            type_=sa.Integer(),
            existing_nullable=True,
        )
