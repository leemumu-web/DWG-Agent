"""add data console ledger

Revision ID: 6d2f8a9c1b40
Revises: e4a1c7f2b930
Create Date: 2026-07-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6d2f8a9c1b40"
down_revision: str | None = "e4a1c7f2b930"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    duplicate = op.get_bind().execute(
        sa.text(
            "SELECT bucket, storage_key FROM files "
            "GROUP BY bucket, storage_key HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot add files storage-location uniqueness while duplicate "
            f"rows exist for bucket={duplicate.bucket!r}."
        )

    op.add_column("files", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        sa.text(
            "UPDATE files SET deleted_at = updated_at "
            "WHERE status = 'deleted' AND deleted_at IS NULL"
        )
    )
    op.create_unique_constraint(
        "uq_files_bucket_storage_key",
        "files",
        ["bucket", "storage_key"],
    )
    op.create_index(
        "ix_files_status_deleted_at",
        "files",
        ["status", "deleted_at"],
    )

    op.create_table(
        "file_transfers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("transfer_uid", sa.String(length=36), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=True),
        sa.Column("batch_ref", sa.String(length=64), nullable=True),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("bucket", sa.String(length=128), nullable=True),
        sa.Column("storage_key", sa.String(length=512), nullable=True),
        sa.Column("original_name", sa.String(length=255), nullable=True),
        sa.Column("expected_bytes", sa.BigInteger(), nullable=True),
        sa.Column("transferred_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["sys_users.id"]),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transfer_uid", name="uq_file_transfers_uid"),
        sa.UniqueConstraint(
            "actor_user_id",
            "operation",
            "idempotency_key",
            name="uq_file_transfers_idempotency",
        ),
    )
    op.create_index("ix_file_transfers_file_id", "file_transfers", ["file_id"])
    op.create_index("ix_file_transfers_batch_ref", "file_transfers", ["batch_ref"])
    op.create_index(
        "ix_file_transfers_actor_user_id", "file_transfers", ["actor_user_id"]
    )
    op.create_index("ix_file_transfers_request_id", "file_transfers", ["request_id"])
    op.create_index(
        "ix_file_transfers_direction_created",
        "file_transfers",
        ["direction", "created_at"],
    )
    op.create_index(
        "ix_file_transfers_status_created",
        "file_transfers",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_file_transfers_operation_created",
        "file_transfers",
        ["operation", "created_at"],
    )

    op.create_table(
        "storage_scan_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("backend", sa.String(length=16), nullable=False),
        sa.Column("scope_bucket", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("scanned_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scanned_objects", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consistent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "retained_deleted_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("missing_object_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("untracked_object_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("size_mismatch_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["sys_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_storage_scan_runs_actor_user_id", "storage_scan_runs", ["actor_user_id"]
    )
    op.create_index(
        "ix_storage_scan_runs_status_created",
        "storage_scan_runs",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_storage_scan_runs_scope_status",
        "storage_scan_runs",
        ["scope_bucket", "status"],
    )

    op.create_table(
        "storage_scan_findings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("finding_type", sa.String(length=32), nullable=False),
        sa.Column("bucket", sa.String(length=128), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=True),
        sa.Column("file_status", sa.String(length=32), nullable=True),
        sa.Column("database_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("object_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("object_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolution_status",
            sa.String(length=32),
            nullable=False,
            server_default="open",
        ),
        sa.Column("resolution_action", sa.String(length=32), nullable=True),
        sa.Column("resolved_by", sa.BigInteger(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by"], ["sys_users.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["storage_scan_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "finding_type",
            "bucket",
            "storage_key",
            name="uq_storage_scan_finding_location",
        ),
    )
    op.create_index(
        "ix_storage_scan_findings_run_id", "storage_scan_findings", ["run_id"]
    )
    op.create_index(
        "ix_storage_scan_findings_file_id", "storage_scan_findings", ["file_id"]
    )
    op.create_index(
        "ix_storage_scan_findings_run_type",
        "storage_scan_findings",
        ["run_id", "finding_type"],
    )
    op.create_index(
        "ix_storage_scan_findings_run_resolution",
        "storage_scan_findings",
        ["run_id", "resolution_status"],
    )


def downgrade() -> None:
    op.drop_table("storage_scan_findings")
    op.drop_table("storage_scan_runs")
    op.drop_table("file_transfers")
    op.drop_index("ix_files_status_deleted_at", table_name="files")
    op.drop_constraint("uq_files_bucket_storage_key", "files", type_="unique")
    op.drop_column("files", "deleted_at")
