"""add DXF classification stage and ledger

Revision ID: a9e4c7d2f610
Revises: f7a9c2d4e610
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "a9e4c7d2f610"
down_revision: str | None = "f7a9c2d4e610"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dxf_classification_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workflow_run_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("job_attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("classifier_version", sa.String(length=32), nullable=False),
        sa.Column("report_schema", sa.String(length=64), nullable=True),
        sa.Column("cli_schema", sa.String(length=64), nullable=True),
        sa.Column("project_name", sa.String(length=128), nullable=False),
        sa.Column("input_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("input_count", sa.Integer(), nullable=False),
        sa.Column("classified_count", sa.Integer(), nullable=False),
        sa.Column("review_required_count", sa.Integer(), nullable=False),
        sa.Column("unreadable_count", sa.Integer(), nullable=False),
        sa.Column("type_counts_json", sa.JSON(), nullable=True),
        sa.Column("report_file_id", sa.BigInteger(), nullable=True),
        sa.Column("manifest_file_id", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["report_file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["manifest_file_id"], ["files.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "job_attempt", name="uq_dxf_classification_job_attempt"),
    )
    op.create_index("ix_dxf_classification_runs_workflow_run_id", "dxf_classification_runs", ["workflow_run_id"])
    op.create_index("ix_dxf_classification_runs_project_id", "dxf_classification_runs", ["project_id"])
    op.create_index("ix_dxf_classification_runs_job_id", "dxf_classification_runs", ["job_id"])
    op.create_index("ix_dxf_classification_runs_status", "dxf_classification_runs", ["status"])
    op.create_index("ix_dxf_classification_runs_report_file_id", "dxf_classification_runs", ["report_file_id"])
    op.create_index("ix_dxf_classification_runs_manifest_file_id", "dxf_classification_runs", ["manifest_file_id"])
    op.create_index("ix_dxf_classification_workflow_attempt", "dxf_classification_runs", ["workflow_run_id", "job_attempt"])

    op.create_table(
        "dxf_classification_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("drawing_id", sa.BigInteger(), nullable=True),
        sa.Column("source_file_id", sa.BigInteger(), nullable=False),
        sa.Column("output_file_id", sa.BigInteger(), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("output_name", sa.String(length=255), nullable=False),
        sa.Column("output_directory", sa.String(length=255), nullable=False),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column("part_type", sa.String(length=64), nullable=True),
        sa.Column("diagnostics_json", sa.JSON(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["dxf_classification_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["drawing_id"], ["drawings.id"]),
        sa.ForeignKeyConstraint(["source_file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["output_file_id"], ["files.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "source_file_id", name="uq_dxf_classification_run_source"),
    )
    op.create_index("ix_dxf_classification_items_run_id", "dxf_classification_items", ["run_id"])
    op.create_index("ix_dxf_classification_items_drawing_id", "dxf_classification_items", ["drawing_id"])
    op.create_index("ix_dxf_classification_items_source_file_id", "dxf_classification_items", ["source_file_id"])
    op.create_index("ix_dxf_classification_items_output_file_id", "dxf_classification_items", ["output_file_id"])
    op.create_index("ix_dxf_classification_items_disposition", "dxf_classification_items", ["run_id", "disposition"])
    op.create_index("ix_dxf_classification_items_part_type", "dxf_classification_items", ["run_id", "part_type"])

    bind = op.get_bind()
    runs = sa.table(
        "workflow_runs",
        sa.column("id", sa.BigInteger()),
        sa.column("workflow_type", sa.String()),
        sa.column("current_stage", sa.String()),
        sa.column("status", sa.String()),
    )
    stages = sa.table(
        "workflow_stage_runs",
        sa.column("workflow_run_id", sa.BigInteger()),
        sa.column("stage_code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("sequence", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("progress", sa.Integer()),
        sa.column("output_json", sa.JSON()),
        sa.column("started_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    workflow_rows = bind.execute(
        sa.select(runs.c.id).where(runs.c.workflow_type == "linux_production")
    ).all()
    now = datetime.now(UTC)
    for (workflow_id,) in workflow_rows:
        statuses = dict(
            bind.execute(
                sa.select(stages.c.stage_code, stages.c.status).where(
                    stages.c.workflow_run_id == workflow_id
                )
            ).all()
        )
        source_status = statuses.get("source_intake")
        drawing_status = statuses.get("drawing_processing")
        bind.execute(
            stages.update()
            .where(stages.c.workflow_run_id == workflow_id, stages.c.sequence >= 2)
            .values(sequence=stages.c.sequence + 1)
        )
        if source_status == "succeeded" and drawing_status in {"pending", "ready", "waiting_input"}:
            classification_status, progress = "waiting_input", 0
            bind.execute(
                stages.update()
                .where(
                    stages.c.workflow_run_id == workflow_id,
                    stages.c.stage_code == "drawing_processing",
                )
                .values(status="pending", progress=0)
            )
            bind.execute(
                runs.update()
                .where(runs.c.id == workflow_id)
                .values(current_stage="dxf_classification", status="waiting_input")
            )
        elif source_status != "succeeded":
            classification_status, progress = "pending", 0
        else:
            classification_status, progress = "skipped", 100
        bind.execute(
            stages.insert().values(
                workflow_run_id=workflow_id,
                stage_code="dxf_classification",
                name="DXF 分类与分流",
                sequence=2,
                status=classification_status,
                progress=progress,
                output_json={"migration_backfill": True} if classification_status == "skipped" else None,
                started_at=now if classification_status == "waiting_input" else None,
                created_at=now,
                updated_at=now,
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE workflow_runs SET current_stage='drawing_processing', status='waiting_input' "
            "WHERE current_stage='dxf_classification'"
        )
    )
    bind.execute(sa.text("DELETE FROM workflow_stage_runs WHERE stage_code='dxf_classification'"))
    bind.execute(
        sa.text(
            "UPDATE workflow_stage_runs SET sequence=sequence-1 "
            "WHERE workflow_run_id IN (SELECT id FROM workflow_runs WHERE workflow_type='linux_production') "
            "AND sequence>=3"
        )
    )
    op.drop_table("dxf_classification_items")
    op.drop_table("dxf_classification_runs")
