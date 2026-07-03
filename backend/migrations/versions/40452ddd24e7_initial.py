"""initial

Revision ID: 40452ddd24e7
Revises:
Create Date: 2026-07-03 13:59:32.205758
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "40452ddd24e7"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _pk_type() -> sa.BigInteger:
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _id_column() -> sa.Column:
    return sa.Column("id", _pk_type(), autoincrement=True, nullable=False)


def _fk_id_column(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, _pk_type(), nullable=nullable)


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "sys_permissions",
        _id_column(),
        sa.Column("code", sa.String(length=128), nullable=False),
        sa.Column("resource", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sys_permissions_code"), "sys_permissions", ["code"], unique=True)

    op.create_table(
        "sys_roles",
        _id_column(),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sys_roles_code"), "sys_roles", ["code"], unique=True)

    op.create_table(
        "sys_users",
        _id_column(),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("employee_no", sa.String(length=64), nullable=True),
        sa.Column("real_name", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=128), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("password_algo", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sys_users_deleted_at"), "sys_users", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_sys_users_status"), "sys_users", ["status"], unique=False)
    op.create_index(op.f("ix_sys_users_username"), "sys_users", ["username"], unique=True)

    op.create_table(
        "files",
        _id_column(),
        sa.Column("bucket", sa.String(length=128), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("file_ext", sa.String(length=32), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("md5", sa.String(length=32), nullable=True),
        _fk_id_column("uploaded_by"),
        sa.Column("status", sa.String(length=32), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["uploaded_by"], ["sys_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_files_sha256"), "files", ["sha256"], unique=False)
    op.create_index(op.f("ix_files_storage_key"), "files", ["storage_key"], unique=False)

    op.create_table(
        "projects",
        _id_column(),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        _fk_id_column("owner_id"),
        sa.Column("status", sa.String(length=32), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["owner_id"], ["sys_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_projects_code"), "projects", ["code"], unique=True)

    op.create_table(
        "drawings",
        _id_column(),
        _fk_id_column("project_id", nullable=False),
        sa.Column("drawing_no", sa.String(length=128), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("discipline", sa.String(length=64), nullable=True),
        _fk_id_column("current_version_id"),
        sa.Column("status", sa.String(length=32), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_drawings_project_id"), "drawings", ["project_id"], unique=False)

    op.create_table(
        "drawing_versions",
        _id_column(),
        _fk_id_column("drawing_id", nullable=False),
        _fk_id_column("file_id", nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=True),
        _fk_id_column("created_by"),
        *_timestamps(),
        sa.ForeignKeyConstraint(["created_by"], ["sys_users.id"]),
        sa.ForeignKeyConstraint(["drawing_id"], ["drawings.id"]),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_foreign_key(
        "fk_drawings_current_version_id_drawing_versions",
        "drawings",
        "drawing_versions",
        ["current_version_id"],
        ["id"],
    )

    op.create_table(
        "sys_role_permissions",
        _fk_id_column("role_id", nullable=False),
        _fk_id_column("permission_id", nullable=False),
        sa.ForeignKeyConstraint(["permission_id"], ["sys_permissions.id"]),
        sa.ForeignKeyConstraint(["role_id"], ["sys_roles.id"]),
        sa.PrimaryKeyConstraint("role_id", "permission_id"),
    )

    op.create_table(
        "sys_user_roles",
        _fk_id_column("user_id", nullable=False),
        _fk_id_column("role_id", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["sys_roles.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["sys_users.id"]),
        sa.PrimaryKeyConstraint("user_id", "role_id"),
    )

    op.create_table(
        "audit_logs",
        _id_column(),
        _fk_id_column("actor_user_id"),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["actor_user_id"], ["sys_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False)
    op.create_index(op.f("ix_audit_logs_resource_id"), "audit_logs", ["resource_id"], unique=False)

    op.create_table(
        "agent_runs",
        _id_column(),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        _fk_id_column("user_id", nullable=False),
        _fk_id_column("project_id"),
        _fk_id_column("drawing_id"),
        _fk_id_column("file_id"),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        _fk_id_column("output_file_id"),
        sa.Column("history_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["drawing_id"], ["drawings.id"]),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["output_file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["sys_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_runs_session_id"), "agent_runs", ["session_id"], unique=False)

    op.create_table(
        "jobs",
        _id_column(),
        _fk_id_column("project_id"),
        _fk_id_column("drawing_id"),
        _fk_id_column("created_by"),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("precision_level", sa.String(length=32), nullable=False),
        sa.Column("pipeline", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("params_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["created_by"], ["sys_users.id"]),
        sa.ForeignKeyConstraint(["drawing_id"], ["drawings.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_jobs_drawing_id"), "jobs", ["drawing_id"], unique=False)
    op.create_index(op.f("ix_jobs_project_id"), "jobs", ["project_id"], unique=False)
    op.create_index(op.f("ix_jobs_status"), "jobs", ["status"], unique=False)

    op.create_table(
        "project_members",
        _id_column(),
        _fk_id_column("project_id", nullable=False),
        _fk_id_column("user_id", nullable=False),
        sa.Column("project_role", sa.String(length=64), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["sys_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_member"),
    )

    op.create_table(
        "agent_run_steps",
        _id_column(),
        _fk_id_column("agent_run_id", nullable=False),
        sa.Column("step_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("tool_name", sa.String(length=128), nullable=True),
        sa.Column("arguments_json", sa.JSON(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_run_steps_agent_run_id"), "agent_run_steps", ["agent_run_id"], unique=False)

    op.create_table(
        "analysis_results",
        _id_column(),
        _fk_id_column("job_id", nullable=False),
        _fk_id_column("drawing_id"),
        sa.Column("result_type", sa.String(length=64), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.DECIMAL(precision=5, scale=4), nullable=True),
        _fk_id_column("result_file_id"),
        sa.Column("algorithm_version", sa.String(length=64), nullable=True),
        sa.Column("tool_version", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["drawing_id"], ["drawings.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["result_file_id"], ["files.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_analysis_results_drawing_id"), "analysis_results", ["drawing_id"], unique=False)
    op.create_index(op.f("ix_analysis_results_job_id"), "analysis_results", ["job_id"], unique=False)

    op.create_table(
        "job_steps",
        _id_column(),
        _fk_id_column("job_id", nullable=False),
        sa.Column("step_name", sa.String(length=128), nullable=False),
        sa.Column("worker_name", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_job_steps_job_id"), "job_steps", ["job_id"], unique=False)

    op.create_table(
        "review_records",
        _id_column(),
        _fk_id_column("result_id", nullable=False),
        _fk_id_column("reviewer_id"),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["result_id"], ["analysis_results.id"]),
        sa.ForeignKeyConstraint(["reviewer_id"], ["sys_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_review_records_result_id"), "review_records", ["result_id"], unique=False)


def downgrade() -> None:
    op.drop_table("review_records")
    op.drop_table("job_steps")
    op.drop_table("analysis_results")
    op.drop_table("agent_run_steps")
    op.drop_table("project_members")
    op.drop_table("jobs")
    op.drop_table("agent_runs")
    op.drop_table("audit_logs")
    op.drop_table("sys_user_roles")
    op.drop_table("sys_role_permissions")
    op.drop_constraint("fk_drawings_current_version_id_drawing_versions", "drawings", type_="foreignkey")
    op.drop_table("drawing_versions")
    op.drop_table("drawings")
    op.drop_table("projects")
    op.drop_table("files")
    op.drop_table("sys_users")
    op.drop_table("sys_roles")
    op.drop_table("sys_permissions")
