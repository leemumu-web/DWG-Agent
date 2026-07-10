"""harden excel_final relations

Revision ID: 7f2a9c4e6b10
Revises: 3480bd86ddc3
Create Date: 2026-07-10 18:25:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f2a9c4e6b10"
down_revision: str | None = "3480bd86ddc3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_foreign_key_for_column(table_name: str, column_name: str) -> None:
    """Drop an old unnamed FK using the name assigned by the target database."""
    inspector = sa.inspect(op.get_bind())
    for foreign_key in inspector.get_foreign_keys(table_name):
        if foreign_key["constrained_columns"] == [column_name]:
            constraint_name = foreign_key.get("name")
            if not constraint_name:
                raise RuntimeError(
                    f"Cannot migrate unnamed foreign key on {table_name}.{column_name}."
                )
            op.drop_constraint(constraint_name, table_name, type_="foreignkey")
            return
    raise RuntimeError(f"Foreign key not found on {table_name}.{column_name}.")


def _alter_identifier(
    table_name: str,
    column_name: str,
    *,
    target_type: sa.types.TypeEngine,
    existing_type: sa.types.TypeEngine,
    nullable: bool,
    autoincrement: bool = False,
) -> None:
    op.alter_column(
        table_name,
        column_name,
        existing_type=existing_type,
        type_=target_type,
        existing_nullable=nullable,
        autoincrement=autoincrement,
    )


def upgrade() -> None:
    _drop_foreign_key_for_column("excel_final_parts", "batch_id")
    _drop_foreign_key_for_column("excel_final_components", "batch_id")
    op.drop_index("ix_excel_final_batches_job_id", table_name="excel_final_batches")

    for table_name, column_name, nullable, autoincrement in (
        ("excel_final_batches", "id", False, True),
        ("excel_final_batches", "job_id", False, False),
        ("excel_final_batches", "file_id", True, False),
        ("excel_final_parts", "id", False, True),
        ("excel_final_parts", "batch_id", False, False),
        ("excel_final_components", "id", False, True),
        ("excel_final_components", "batch_id", False, False),
    ):
        _alter_identifier(
            table_name,
            column_name,
            target_type=sa.BigInteger(),
            existing_type=sa.Integer(),
            nullable=nullable,
            autoincrement=autoincrement,
        )

    op.create_unique_constraint("uq_excel_final_batches_job_id", "excel_final_batches", ["job_id"])
    op.create_index(
        "ix_excel_final_batches_file_id",
        "excel_final_batches",
        ["file_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_excel_final_batches_job_id",
        "excel_final_batches",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_excel_final_batches_file_id",
        "excel_final_batches",
        "files",
        ["file_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_excel_final_parts_batch_id",
        "excel_final_parts",
        "excel_final_batches",
        ["batch_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_excel_final_components_batch_id",
        "excel_final_components",
        "excel_final_batches",
        ["batch_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_excel_final_components_batch_id",
        "excel_final_components",
        type_="foreignkey",
    )
    op.drop_constraint("fk_excel_final_parts_batch_id", "excel_final_parts", type_="foreignkey")
    op.drop_constraint("fk_excel_final_batches_file_id", "excel_final_batches", type_="foreignkey")
    op.drop_constraint("fk_excel_final_batches_job_id", "excel_final_batches", type_="foreignkey")
    op.drop_index("ix_excel_final_batches_file_id", table_name="excel_final_batches")
    op.drop_constraint("uq_excel_final_batches_job_id", "excel_final_batches", type_="unique")

    for table_name, column_name, nullable, autoincrement in (
        ("excel_final_batches", "id", False, True),
        ("excel_final_batches", "job_id", False, False),
        ("excel_final_batches", "file_id", True, False),
        ("excel_final_parts", "id", False, True),
        ("excel_final_parts", "batch_id", False, False),
        ("excel_final_components", "id", False, True),
        ("excel_final_components", "batch_id", False, False),
    ):
        _alter_identifier(
            table_name,
            column_name,
            target_type=sa.Integer(),
            existing_type=sa.BigInteger(),
            nullable=nullable,
            autoincrement=autoincrement,
        )

    op.create_index(
        "ix_excel_final_batches_job_id",
        "excel_final_batches",
        ["job_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_excel_final_parts_batch_id",
        "excel_final_parts",
        "excel_final_batches",
        ["batch_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_excel_final_components_batch_id",
        "excel_final_components",
        "excel_final_batches",
        ["batch_id"],
        ["id"],
        ondelete="CASCADE",
    )
