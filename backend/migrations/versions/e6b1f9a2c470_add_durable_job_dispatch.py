"""add operation idempotency and durable Job dispatch intents

Revision ID: e6b1f9a2c470
Revises: a4c8e1f2b730
Create Date: 2026-08-01
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op

revision: str = "e6b1f9a2c470"
down_revision: str | None = "a4c8e1f2b730"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _backfill_queued_jobs() -> None:
    connection = op.get_bind()
    jobs = sa.table(
        "jobs",
        sa.column("id", sa.BigInteger()),
        sa.column("attempt", sa.Integer()),
        sa.column("task_type", sa.String(length=64)),
        sa.column("pipeline", sa.String(length=64)),
        sa.column("status", sa.String(length=32)),
    )
    dispatches = sa.table(
        "job_dispatches",
        sa.column("id", sa.BigInteger()),
        sa.column("dispatch_uid", sa.String(length=36)),
        sa.column("job_id", sa.BigInteger()),
        sa.column("job_attempt", sa.Integer()),
        sa.column("task_type", sa.String(length=64)),
        sa.column("pipeline", sa.String(length=64)),
        sa.column("dispatch_mode", sa.String(length=32)),
        sa.column("status", sa.String(length=16)),
        sa.column("delivery_attempts", sa.Integer()),
        sa.column("available_at", sa.DateTime()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    rows = connection.execute(
        sa.select(
            jobs.c.id,
            jobs.c.attempt,
            jobs.c.task_type,
            jobs.c.pipeline,
        )
        .where(jobs.c.status == "queued")
        .order_by(jobs.c.id)
    ).mappings()
    now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    payload = [
        {
            "id": index,
            "dispatch_uid": str(uuid4()),
            "job_id": row["id"],
            "job_attempt": row["attempt"],
            "task_type": row["task_type"],
            "pipeline": row["pipeline"] or "stub",
            "dispatch_mode": "single",
            "status": "pending",
            "delivery_attempts": 0,
            "available_at": now,
            "created_at": now,
            "updated_at": now,
        }
        for index, row in enumerate(rows, start=1)
    ]
    if payload:
        op.bulk_insert(dispatches, payload)


def upgrade() -> None:
    op.add_column("jobs", sa.Column("operation_key", sa.String(length=191)))
    op.create_unique_constraint(
        "uq_jobs_task_operation_key",
        "jobs",
        ["task_type", "operation_key"],
    )
    op.create_table(
        "job_dispatches",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("dispatch_uid", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("job_attempt", sa.Integer(), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("pipeline", sa.String(length=64), nullable=False),
        sa.Column("dispatch_mode", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "delivery_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(length=36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("celery_task_id", sa.String(length=64)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(length=64)),
        sa.Column("last_error_message", sa.String(length=500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "job_attempt",
            name="uq_job_dispatch_attempt",
        ),
    )
    op.create_index(
        "ix_job_dispatch_pending",
        "job_dispatches",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_job_dispatch_lease",
        "job_dispatches",
        ["lease_expires_at"],
    )
    op.create_index(
        "ix_job_dispatch_uid",
        "job_dispatches",
        ["dispatch_uid"],
    )
    _backfill_queued_jobs()


def downgrade() -> None:
    op.drop_table("job_dispatches")
    op.drop_constraint("uq_jobs_task_operation_key", "jobs", type_="unique")
    op.drop_column("jobs", "operation_key")
