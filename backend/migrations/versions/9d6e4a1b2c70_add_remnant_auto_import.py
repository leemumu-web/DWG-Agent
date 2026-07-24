"""add remnant auto import metadata

Revision ID: 9d6e4a1b2c70
Revises: 7c4d9e2a1b60
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9d6e4a1b2c70"
down_revision: str | None = "7c4d9e2a1b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "remnant_import_batches",
        sa.Column(
            "import_mode",
            sa.String(16),
            server_default="manual",
            nullable=False,
        ),
    )
    op.add_column(
        "remnant_import_batches",
        sa.Column("default_project_no", sa.String(128)),
    )
    op.add_column(
        "remnant_import_batches",
        sa.Column("source_folder_name", sa.String(255)),
    )
    op.create_check_constraint(
        "ck_remnant_import_batch_mode",
        "remnant_import_batches",
        "import_mode IN ('manual', 'auto')",
    )
    op.add_column(
        "remnant_import_items",
        sa.Column("source_relative_path", sa.String(1024)),
    )
    op.add_column(
        "remnant_import_items",
        sa.Column("standard_parse_json", sa.JSON()),
    )
    op.drop_constraint("uq_remnant_import_item_batch_source", "remnant_import_items", type_="unique")


def downgrade() -> None:
    duplicate = op.get_bind().execute(
        sa.text(
            """
            SELECT batch_id, source_sha256
            FROM remnant_import_items
            GROUP BY batch_id, source_sha256
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot downgrade remnant auto import while duplicate ledger rows exist; "
            "archive or reconcile those rows explicitly before retrying."
        )
    op.create_unique_constraint(
        "uq_remnant_import_item_batch_source",
        "remnant_import_items",
        ["batch_id", "source_sha256"],
    )
    op.drop_column("remnant_import_items", "standard_parse_json")
    op.drop_column("remnant_import_items", "source_relative_path")
    op.drop_constraint(
        "ck_remnant_import_batch_mode",
        "remnant_import_batches",
        type_="check",
    )
    op.drop_column("remnant_import_batches", "source_folder_name")
    op.drop_column("remnant_import_batches", "default_project_no")
    op.drop_column("remnant_import_batches", "import_mode")
