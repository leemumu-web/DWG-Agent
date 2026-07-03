from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VERSIONS_DIR = PROJECT_ROOT / "backend" / "migrations" / "versions"
INITIAL_REVISION = VERSIONS_DIR / "40452ddd24e7_initial.py"
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
