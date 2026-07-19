"""add control-plane messaging and worker runtime framework

Revision ID: c1e9a4b7d220
Revises: a9e4c7d2f610
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1e9a4b7d220"
down_revision: str | None = "a9e4c7d2f610"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_runtimes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("worker_name", sa.String(160), nullable=False),
        sa.Column("hostname", sa.String(255)), sa.Column("process_id", sa.Integer()),
        sa.Column("queues_json", sa.JSON()), sa.Column("concurrency", sa.Integer()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True)), sa.Column("metadata_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("worker_name"),
    )
    op.create_index("ix_worker_runtimes_status_last_seen", "worker_runtimes", ["status", "last_seen_at"])
    op.create_table(
        "control_plane_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(32), nullable=False), sa.Column("direction", sa.String(32), nullable=False),
        sa.Column("event_type", sa.String(96), nullable=False), sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("correlation_id", sa.String(128)), sa.Column("target_kind", sa.String(48)),
        sa.Column("target_id", sa.String(160)), sa.Column("payload_json", sa.JSON()), sa.Column("message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_control_plane_events_created", "control_plane_events", ["created_at"])
    op.create_index("ix_control_plane_events_target", "control_plane_events", ["target_kind", "target_id"])
    op.create_index("ix_control_plane_events_correlation", "control_plane_events", ["correlation_id"])
    op.create_table(
        "platform_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("audience", sa.String(32), nullable=False), sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("category", sa.String(48), nullable=False), sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text()), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("action_url", sa.String(512)), sa.Column("related_event_id", sa.BigInteger()),
        sa.Column("read_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["related_event_id"], ["control_plane_events.id"]), sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_platform_messages_status_created", "platform_messages", ["status", "created_at"])
    op.create_index("ix_platform_messages_related_event_id", "platform_messages", ["related_event_id"])


def downgrade() -> None:
    op.drop_table("platform_messages")
    op.drop_table("control_plane_events")
    op.drop_table("worker_runtimes")
