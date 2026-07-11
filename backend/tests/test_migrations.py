from __future__ import annotations

from pathlib import Path

from app.models.job import JobStep

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VERSIONS_DIR = PROJECT_ROOT / "backend" / "migrations" / "versions"
ALEMBIC_ENV = PROJECT_ROOT / "backend" / "migrations" / "env.py"
DB_SCRIPT = PROJECT_ROOT / "scripts" / "db.sh"
INITIAL_REVISION = VERSIONS_DIR / "40452ddd24e7_initial.py"
MYSQL_BACKEND_REVISION = VERSIONS_DIR / "1d1696c7e854_remove_redis_add_mysql_backend.py"
EXCEL_FINAL_REVISION = VERSIONS_DIR / "3480bd86ddc3_add_excel_final_tables.py"
EXCEL_FINAL_RELATIONS_REVISION = VERSIONS_DIR / "7f2a9c4e6b10_harden_excel_final_relations.py"
JOB_ATTEMPT_REVISION = VERSIONS_DIR / "8c61f4d2a9e7_add_job_attempt_generation.py"
JOB_STEP_ATTEMPT_REVISION = VERSIONS_DIR / "a74c2e9f1d30_add_job_step_attempt.py"
MODEL_TABLES = (
    "agent_run_steps",
    "agent_runs",
    "analysis_results",
    "audit_logs",
    "drawing_versions",
    "drawings",
    "files",
    "job_steps",
    "jobs",
    "project_members",
    "projects",
    "review_records",
    "sys_permissions",
    "sys_role_permissions",
    "sys_roles",
    "sys_user_roles",
    "sys_users",
)


def _migration_sources() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in VERSIONS_DIR.glob("*.py"))


def test_alembic_repairs_timestamp_mixin_columns_for_existing_mysql_tables():
    source = _migration_sources()

    assert "40452ddd24e7" in source
    for table in (
        "project_members",
        "drawing_versions",
        "review_records",
        "agent_run_steps",
    ):
        assert table in source
    assert "created_at" in source
    assert "updated_at" in source
    assert "op.add_column" in source


def test_initial_revision_creates_all_model_tables():
    source = INITIAL_REVISION.read_text(encoding="utf-8")

    assert "op.create_table" in source
    assert "\n    pass\n" not in source
    for table in MODEL_TABLES:
        assert f'"{table}"' in source or f"'{table}'" in source


def test_mysql_backend_revision_creates_durable_runtime_state():
    source = MYSQL_BACKEND_REVISION.read_text(encoding="utf-8")

    assert 'down_revision: str | None = "53cd59adf848"' in source
    for table in ("agent_memory", "token_blacklist"):
        assert f'"{table}"' in source
        assert f'op.drop_table("{table}")' in source
    assert 'op.add_column("jobs"' in source
    assert '"progress_data"' in source
    assert 'op.add_column(\n        "sys_users"' in source
    assert '"password_changed_at"' in source
    assert "ix_token_blacklist_expires_at" in source


def test_business_migration_does_not_manage_celery_owned_tables():
    source = EXCEL_FINAL_REVISION.read_text(encoding="utf-8")

    for table in (
        "kombu_queue",
        "kombu_message",
        "celery_taskmeta",
        "celery_tasksetmeta",
        "message_id_sequence",
        "queue_id_sequence",
        "task_id_sequence",
        "taskset_id_sequence",
    ):
        assert f"op.drop_table('{table}')" not in source
        assert f"op.create_table('{table}'" not in source


def test_excel_final_followup_migration_aligns_ids_and_relations():
    source = EXCEL_FINAL_RELATIONS_REVISION.read_text(encoding="utf-8")

    assert 'down_revision: str | None = "3480bd86ddc3"' in source
    assert "target_type=sa.BigInteger()" in source
    assert '"fk_excel_final_batches_job_id"' in source
    assert '"fk_excel_final_batches_file_id"' in source
    assert '"uq_excel_final_batches_job_id"' in source
    assert 'ondelete="CASCADE"' in source
    assert 'ondelete="SET NULL"' in source


def test_job_attempt_migration_adds_worker_generation_boundary():
    source = JOB_ATTEMPT_REVISION.read_text(encoding="utf-8")

    assert 'down_revision: str | None = "7f2a9c4e6b10"' in source
    assert '"jobs"' in source
    assert '"attempt"' in source
    assert "server_default=sa.text(\"1\")" in source


def test_job_step_attempt_migration_preserves_retry_history():
    source = JOB_STEP_ATTEMPT_REVISION.read_text(encoding="utf-8")

    assert 'down_revision: str | None = "8c61f4d2a9e7"' in source
    assert '"job_steps"' in source
    assert '"attempt"' in source
    assert '"ix_job_steps_job_id_attempt"' in source
    assert "server_default=sa.text(\"1\")" in source


def test_job_step_model_keeps_attempt_lookup_index_in_metadata():
    indexed_columns = {
        tuple(column.name for column in index.columns) for index in JobStep.__table__.indexes
    }

    assert ("job_id", "attempt") in indexed_columns


def test_alembic_autogenerate_excludes_celery_owned_tables():
    source = ALEMBIC_ENV.read_text(encoding="utf-8")

    for table in (
        "kombu_queue",
        "kombu_message",
        "celery_taskmeta",
        "celery_tasksetmeta",
        "message_id_sequence",
        "queue_id_sequence",
        "task_id_sequence",
        "taskset_id_sequence",
    ):
        assert f'"{table}"' in source
    assert source.count("include_object=include_object") == 2


def test_mysql_migration_smoke_script_checks_current_business_tables():
    source = DB_SCRIPT.read_text(encoding="utf-8")

    for table in (
        "agent_memory",
        "token_blacklist",
        "excel_final_batches",
        "excel_final_components",
        "excel_final_parts",
    ):
        assert f'"{table}"' in source
    assert "create_engine(settings.sqlalchemy_database_url)" in source
    assert 'version != "a74c2e9f1d30"' in source
    assert '"jobs": {"progress_data", "attempt"}' in source
    assert '"job_steps": {"attempt"}' in source
    assert "identifier types are not BIGINT" in source
    assert "excel_final_batches.job_id is not unique" in source
