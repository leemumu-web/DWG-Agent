"""Use fixed-point storage for Excel Final physical values.

Revision ID: 2f6b8c1d4e90
Revises: f3a7c9d2e6b1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "2f6b8c1d4e90"
down_revision: str | None = "f3a7c9d2e6b1"
branch_labels: str | None = None
depends_on: str | None = None

_BATCH_COLUMNS = ("total_net_weight", "total_gross_weight")
_PART_COLUMNS = (
    "original_qty",
    "width",
    "length",
    "left_inset",
    "right_inset",
    "cut_length",
    "qty",
    "total_qty",
    "total_length",
    "density",
    "theo_unit_weight",
    "theo_total_weight",
    "material_utilization",
    "net_unit_weight",
    "net_total_weight",
    "table_net_weight",
    "gross_unit_weight",
    "gross_total_weight",
    "table_gross_weight",
    "surface_area",
    "total_surface_area",
)
_COMPONENT_COLUMNS = ("total_weight",)


def _alter_columns(
    table_name: str,
    columns: tuple[str, ...],
    *,
    source_type: sa.types.TypeEngine,
    target_type: sa.types.TypeEngine,
) -> None:
    with op.batch_alter_table(table_name) as batch_op:
        for column_name in columns:
            batch_op.alter_column(
                column_name,
                existing_type=source_type,
                type_=target_type,
                existing_nullable=True,
            )


def upgrade() -> None:
    source_type = sa.Float()
    target_type = sa.DECIMAL(precision=24, scale=9)
    _alter_columns(
        "excel_final_batches",
        _BATCH_COLUMNS,
        source_type=source_type,
        target_type=target_type,
    )
    _alter_columns(
        "excel_final_parts",
        _PART_COLUMNS,
        source_type=source_type,
        target_type=target_type,
    )
    _alter_columns(
        "excel_final_components",
        _COMPONENT_COLUMNS,
        source_type=source_type,
        target_type=target_type,
    )
    op.alter_column(
        "excel_final_batches",
        "source_type",
        existing_type=sa.String(length=32),
        existing_nullable=False,
        comment="init / canonical / tsv",
        existing_comment="init_table / tekla_tsv",
    )


def downgrade() -> None:
    op.alter_column(
        "excel_final_batches",
        "source_type",
        existing_type=sa.String(length=32),
        existing_nullable=False,
        comment="init_table / tekla_tsv",
        existing_comment="init / canonical / tsv",
    )
    source_type = sa.DECIMAL(precision=24, scale=9)
    target_type = sa.Float()
    _alter_columns(
        "excel_final_components",
        _COMPONENT_COLUMNS,
        source_type=source_type,
        target_type=target_type,
    )
    _alter_columns(
        "excel_final_parts",
        _PART_COLUMNS,
        source_type=source_type,
        target_type=target_type,
    )
    _alter_columns(
        "excel_final_batches",
        _BATCH_COLUMNS,
        source_type=source_type,
        target_type=target_type,
    )
