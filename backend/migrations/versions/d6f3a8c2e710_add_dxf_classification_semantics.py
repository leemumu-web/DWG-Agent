"""add authoritative DXF classification semantics

Revision ID: d6f3a8c2e710
Revises: c7b2d4e9f601
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6f3a8c2e710"
down_revision: str | None = "c7b2d4e9f601"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dxf_classification_items",
        sa.Column("profile_raw", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "dxf_classification_items",
        sa.Column("profile_normalized", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "dxf_classification_items",
        sa.Column("type_source", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "dxf_classification_items",
        sa.Column(
            "group_key",
            sa.String(length=96),
            nullable=False,
            server_default="status:legacy",
        ),
    )
    op.add_column(
        "dxf_classification_items",
        sa.Column(
            "next_stage_eligible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    bind = op.get_bind()
    items = sa.table(
        "dxf_classification_items",
        sa.column("id", sa.BigInteger()),
        sa.column("disposition", sa.String()),
        sa.column("part_type", sa.String()),
        sa.column("output_file_id", sa.BigInteger()),
        sa.column("type_source", sa.String()),
        sa.column("group_key", sa.String()),
        sa.column("next_stage_eligible", sa.Boolean()),
    )
    rows = bind.execute(
        sa.select(
            items.c.id,
            items.c.disposition,
            items.c.part_type,
            items.c.output_file_id,
        )
    ).all()
    for item_id, disposition, part_type, output_file_id in rows:
        eligible = bool(
            disposition == "classified" and part_type and output_file_id is not None
        )
        group_key = (
            f"type:{part_type}"
            if eligible
            else f"status:{disposition or 'unknown'}"
        )
        bind.execute(
            items.update()
            .where(items.c.id == item_id)
            .values(
                type_source="legacy" if eligible else None,
                group_key=group_key,
                next_stage_eligible=eligible,
            )
        )

    op.alter_column(
        "dxf_classification_items",
        "group_key",
        existing_type=sa.String(length=96),
        server_default=None,
    )
    op.create_index(
        "ix_dxf_classification_items_group",
        "dxf_classification_items",
        ["run_id", "group_key"],
    )
    op.create_index(
        "ix_dxf_classification_items_next_stage",
        "dxf_classification_items",
        ["run_id", "next_stage_eligible"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dxf_classification_items_next_stage",
        table_name="dxf_classification_items",
    )
    op.drop_index(
        "ix_dxf_classification_items_group",
        table_name="dxf_classification_items",
    )
    op.drop_column("dxf_classification_items", "next_stage_eligible")
    op.drop_column("dxf_classification_items", "group_key")
    op.drop_column("dxf_classification_items", "type_source")
    op.drop_column("dxf_classification_items", "profile_normalized")
    op.drop_column("dxf_classification_items", "profile_raw")
