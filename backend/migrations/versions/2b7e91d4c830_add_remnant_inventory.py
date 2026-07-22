"""add remnant inventory

Revision ID: 2b7e91d4c830
Revises: e2f4b8c6a130
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2b7e91d4c830"
down_revision: str | None = "e2f4b8c6a130"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "remnant_materials",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("family_code", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_by", sa.BigInteger()),
        sa.Column("updated_by", sa.BigInteger()),
        *_timestamps(),
        sa.ForeignKeyConstraint(["created_by"], ["sys_users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["sys_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_remnant_material_code"),
    )
    op.create_index("ix_remnant_material_family_enabled", "remnant_materials", ["family_code", "enabled"])

    op.create_table(
        "remnant_material_aliases",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("material_id", sa.BigInteger(), nullable=False),
        sa.Column("alias", sa.String(128), nullable=False),
        sa.Column("normalized_alias", sa.String(128), nullable=False),
        sa.Column("created_by", sa.BigInteger()),
        *_timestamps(),
        sa.ForeignKeyConstraint(["material_id"], ["remnant_materials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["sys_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_alias", name="uq_remnant_material_alias_normalized"),
    )
    op.create_index("ix_remnant_material_alias_material", "remnant_material_aliases", ["material_id"])

    op.create_table(
        "remnant_import_batches",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(32), server_default="uploaded", nullable=False),
        sa.Column("total_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("converting_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("parsing_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("pending_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("confirmed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cancelled_count", sa.Integer(), server_default="0", nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["created_by"], ["sys_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_remnant_import_batch_creator_status", "remnant_import_batches", ["created_by", "status"])

    op.create_table(
        "remnant_import_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("source_file_id", sa.BigInteger(), nullable=False),
        sa.Column("dxf_file_id", sa.BigInteger()),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("source_ext", sa.String(8), nullable=False),
        sa.Column("conversion_job_id", sa.BigInteger()),
        sa.Column("parse_job_id", sa.BigInteger()),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(32), server_default="uploaded", nullable=False),
        sa.Column("parser_version", sa.String(32)),
        sa.Column("schema_version", sa.String(16)),
        sa.Column("material_candidates_json", sa.JSON()),
        sa.Column("project_candidates_json", sa.JSON()),
        sa.Column("part_candidates_json", sa.JSON()),
        sa.Column("warnings_json", sa.JSON()),
        sa.Column("corrected_thickness_mm", sa.Numeric(10, 3)),
        sa.Column("corrected_material_id", sa.BigInteger()),
        sa.Column("corrected_project_no", sa.String(128)),
        sa.Column("corrected_parts_json", sa.JSON()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        *_timestamps(),
        sa.ForeignKeyConstraint(["batch_id"], ["remnant_import_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["dxf_file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["conversion_job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["parse_job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["corrected_material_id"], ["remnant_materials.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "source_sha256", name="uq_remnant_import_item_batch_source"),
    )
    op.create_index("ix_remnant_import_item_batch_status", "remnant_import_items", ["batch_id", "status"])
    op.create_index("ix_remnant_import_item_source_sha", "remnant_import_items", ["source_sha256"])

    op.create_table(
        "remnants",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("import_item_id", sa.BigInteger(), nullable=False),
        sa.Column("source_file_id", sa.BigInteger(), nullable=False),
        sa.Column("dxf_file_id", sa.BigInteger(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("thickness_mm", sa.Numeric(10, 3), nullable=False),
        sa.Column("material_id", sa.BigInteger(), nullable=False),
        sa.Column("project_no", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), server_default="available", nullable=False),
        sa.Column("imported_by", sa.BigInteger(), nullable=False),
        sa.Column("confirmed_by", sa.BigInteger(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_by", sa.BigInteger()),
        sa.Column("reserved_at", sa.DateTime(timezone=True)),
        sa.Column("used_by", sa.BigInteger()),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("archived_by", sa.BigInteger()),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["import_item_id"], ["remnant_import_items.id"]),
        sa.ForeignKeyConstraint(["source_file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["dxf_file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["material_id"], ["remnant_materials.id"]),
        sa.ForeignKeyConstraint(["imported_by"], ["sys_users.id"]),
        sa.ForeignKeyConstraint(["confirmed_by"], ["sys_users.id"]),
        sa.ForeignKeyConstraint(["reserved_by"], ["sys_users.id"]),
        sa.ForeignKeyConstraint(["used_by"], ["sys_users.id"]),
        sa.ForeignKeyConstraint(["archived_by"], ["sys_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_sha256", name="uq_remnant_source_sha256"),
        sa.UniqueConstraint("import_item_id", name="uq_remnant_import_item_confirmation"),
    )
    op.create_index("ix_remnant_search", "remnants", ["material_id", "thickness_mm", "status"])
    op.create_index("ix_remnant_reserved_by_status", "remnants", ["reserved_by", "status"])

    op.create_table(
        "remnant_parts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("remnant_id", sa.BigInteger(), nullable=False),
        sa.Column("part_no", sa.String(128), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["remnant_id"], ["remnants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("remnant_id", "part_no", name="uq_remnant_part_number"),
    )
    op.create_index("ix_remnant_part_number", "remnant_parts", ["part_no"])


def downgrade() -> None:
    op.drop_table("remnant_parts")
    op.drop_table("remnants")
    op.drop_table("remnant_import_items")
    op.drop_table("remnant_import_batches")
    op.drop_table("remnant_material_aliases")
    op.drop_table("remnant_materials")
