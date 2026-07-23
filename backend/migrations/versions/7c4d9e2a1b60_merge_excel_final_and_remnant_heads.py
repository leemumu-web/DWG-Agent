"""merge Excel Final and remnant inventory heads

Revision ID: 7c4d9e2a1b60
Revises: 2f6b8c1d4e90, 2b7e91d4c830
Create Date: 2026-07-23
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "7c4d9e2a1b60"
down_revision: tuple[str, str] = ("2f6b8c1d4e90", "2b7e91d4c830")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
