"""merge remnant inventory and Excel workflow heads

Revision ID: 8a6c1f4e2b90
Revises: 6f4a8c2d1e90, 5f8d3b0c2e41
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "8a6c1f4e2b90"
down_revision: tuple[str, str] = ("6f4a8c2d1e90", "5f8d3b0c2e41")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
