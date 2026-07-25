from __future__ import annotations

from app.modules.jobs.interface import JobStep
from tests.support.paths import REPO_ROOT as PROJECT_ROOT

VERSIONS_DIR = PROJECT_ROOT / "backend" / "migrations" / "versions"
ALEMBIC_ENV = PROJECT_ROOT / "backend" / "migrations" / "env.py"
DB_SCRIPT = PROJECT_ROOT / "scripts" / "db.sh"
DB_IMPLEMENTATION = PROJECT_ROOT / "scripts" / "lib" / "database.sh"
INITIAL_REVISION = VERSIONS_DIR / "40452ddd24e7_initial.py"
MYSQL_BACKEND_REVISION = VERSIONS_DIR / "1d1696c7e854_remove_redis_add_mysql_backend.py"
EXCEL_FINAL_REVISION = VERSIONS_DIR / "3480bd86ddc3_add_excel_final_tables.py"
EXCEL_FINAL_RELATIONS_REVISION = VERSIONS_DIR / "7f2a9c4e6b10_harden_excel_final_relations.py"
JOB_ATTEMPT_REVISION = VERSIONS_DIR / "8c61f4d2a9e7_add_job_attempt_generation.py"
JOB_STEP_ATTEMPT_REVISION = VERSIONS_DIR / "a74c2e9f1d30_add_job_step_attempt.py"
DATA_CONSOLE_REVISION = VERSIONS_DIR / "6d2f8a9c1b40_add_data_console_ledger.py"
EXCEL_FINAL_WIDTH_REVISION = VERSIONS_DIR / "9c4e7b1a2d60_widen_excel_final_identifiers.py"
JOB_REQUEST_KEY_REVISION = VERSIONS_DIR / "d5e8a1c4b720_add_job_request_key.py"
WORKFLOW_INPUT_REVISION = VERSIONS_DIR / "f7a9c2d4e610_add_workflow_input_batches.py"
DXF_CLASSIFICATION_REVISION = VERSIONS_DIR / "a9e4c7d2f610_add_dxf_classification_stage.py"
CONTROL_PLANE_REVISION = VERSIONS_DIR / "c1e9a4b7d220_add_control_plane_framework.py"
DAILY_ARCHIVE_REVISION = VERSIONS_DIR / "e2f4b8c6a130_add_daily_archive_runs.py"
REMNANT_INVENTORY_REVISION = VERSIONS_DIR / "2b7e91d4c830_add_remnant_inventory.py"
EXCEL_FINAL_QUALITY_REVISION = VERSIONS_DIR / "f3a7c9d2e6b1_add_excel_final_quality_fields.py"
EXCEL_FINAL_DECIMAL_REVISION = (
    VERSIONS_DIR / "2f6b8c1d4e90_use_decimal_for_excel_final_physical_values.py"
)
MERGE_REVISION = VERSIONS_DIR / "7c4d9e2a1b60_merge_excel_final_and_remnant_heads.py"
REMNANT_AUTO_IMPORT_REVISION = (
    VERSIONS_DIR / "9d6e4a1b2c70_add_remnant_auto_import.py"
)
WORKFLOW_EXCEL_VALIDATION_REVISION = (
    VERSIONS_DIR / "4e7c2a9b1d30_add_workflow_excel_validation.py"
)
LINUX_EXCEL_STAGE_REVISION = (
    VERSIONS_DIR / "5f8d3b0c2e41_normalize_linux_excel_stage.py"
)
REMNANT_EXCEL_WORKFLOW_MERGE_REVISION = (
    VERSIONS_DIR / "8a6c1f4e2b90_merge_remnant_and_excel_workflow_heads.py"
)
WORKFLOW_DXF_CANONICAL_REVISION = (
    VERSIONS_DIR / "c7b2d4e9f601_canonicalize_workflow_dxf_flow.py"
)
DXF_CLASSIFICATION_SEMANTICS_REVISION = (
    VERSIONS_DIR / "d6f3a8c2e710_add_dxf_classification_semantics.py"
)
DXF_SPLIT_REVISION = VERSIONS_DIR / "f9c4b7e2a610_add_dxf_split_pipeline.py"
WORKFLOW_BATCH_EXPORT_REVISION = (
    VERSIONS_DIR / "e9a1b2c3d4f5_add_workflow_batch_exports.py"
)
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


def test_data_console_migration_adds_ledger_and_file_retention_fields():
    source = DATA_CONSOLE_REVISION.read_text(encoding="utf-8")

    assert 'down_revision: str | None = "e4a1c7f2b930"' in source
    for table in ("file_transfers", "storage_scan_runs", "storage_scan_findings"):
        assert f'"{table}"' in source
        assert f'op.drop_table("{table}")' in source
    assert 'op.add_column("files", sa.Column("deleted_at"' in source
    assert "op.create_unique_constraint(" in source
    assert '"uq_files_bucket_storage_key"' in source
    assert "op.create_index(" in source
    assert '"ix_files_status_deleted_at"' in source
    assert 'op.drop_constraint("uq_files_bucket_storage_key"' in source
    assert 'op.drop_column("files", "deleted_at")' in source


def test_excel_final_width_migration_extends_current_head_without_rewriting_history():
    source = EXCEL_FINAL_WIDTH_REVISION.read_text(encoding="utf-8")

    assert 'down_revision: str | None = "6d2f8a9c1b40"' in source
    assert source.count("op.alter_column(") == 12
    assert '"component_no"' in source
    assert "sa.String(512)" in source
    assert "sa.String(255)" in source
    assert "sa.String(128)" in source


def test_job_request_key_migration_extends_head_with_unique_idempotency_boundary():
    source = JOB_REQUEST_KEY_REVISION.read_text(encoding="utf-8")

    assert 'down_revision: str | None = "9c4e7b1a2d60"' in source
    assert 'op.add_column("jobs"' in source
    assert '"request_key"' in source
    assert '"uq_jobs_actor_task_request_key"' in source
    assert '["created_by", "task_type", "request_key"]' in source


def test_workflow_input_migration_extends_head_and_is_reversible():
    source = WORKFLOW_INPUT_REVISION.read_text(encoding="utf-8")

    assert 'down_revision: str | None = "d5e8a1c4b720"' in source
    for table in ("workflow_input_batches", "workflow_input_items"):
        assert f'"{table}"' in source
        assert f'op.drop_table("{table}")' in source
    assert '"uq_workflow_input_batch_workflow"' in source
    assert '"uq_workflow_input_item_file"' in source


def test_dxf_classification_migration_adds_ledger_and_stage_backfill():
    source = DXF_CLASSIFICATION_REVISION.read_text(encoding="utf-8")

    assert 'down_revision: str | None = "f7a9c2d4e610"' in source
    for table in ("dxf_classification_runs", "dxf_classification_items"):
        assert f'"{table}"' in source
        assert f'op.drop_table("{table}")' in source
    assert '"uq_dxf_classification_job_attempt"' in source
    assert '"uq_dxf_classification_run_source"' in source
    assert 'stage_code="dxf_classification"' in source


def test_dxf_classification_semantics_migration_adds_authoritative_fields():
    source = DXF_CLASSIFICATION_SEMANTICS_REVISION.read_text(encoding="utf-8")

    assert 'down_revision: str | None = "c7b2d4e9f601"' in source
    for column in (
        "profile_raw",
        "profile_normalized",
        "type_source",
        "group_key",
        "next_stage_eligible",
    ):
        assert f'"{column}"' in source
    assert '"ix_dxf_classification_items_group"' in source
    assert '"ix_dxf_classification_items_next_stage"' in source
    assert '"legacy"' in source
    for column in (
        "profile_raw",
        "profile_normalized",
        "type_source",
        "group_key",
        "next_stage_eligible",
    ):
        assert f'op.drop_column("dxf_classification_items", "{column}")' in source


def test_dxf_split_migration_adds_attempt_aware_runs_and_items():
    source = DXF_SPLIT_REVISION.read_text(encoding="utf-8")

    assert 'revision: str = "f9c4b7e2a610"' in source
    assert 'down_revision: str | None = "d6f3a8c2e710"' in source
    for table in ("dxf_split_runs", "dxf_split_items"):
        assert f'"{table}"' in source
        assert f'op.drop_table("{table}")' in source
    for constraint in (
        "uq_dxf_split_job_attempt",
        "uq_dxf_split_run_classification_item",
    ):
        assert f'"{constraint}"' in source
    for index in (
        "ix_dxf_split_workflow_attempt",
        "ix_dxf_split_items_route",
        "ix_dxf_split_items_part_type",
    ):
        assert f'"{index}"' in source
    for column in (
        "normal_dxf_file_id",
        "weld_allowance_dxf_file_id",
        "bh_split_ledger_file_id",
        "input_manifest_sha256",
    ):
        assert f'"{column}"' in source


def test_control_plane_migration_extends_head_with_persisted_observability():
    source = CONTROL_PLANE_REVISION.read_text(encoding="utf-8")
    assert 'down_revision: str | None = "a9e4c7d2f610"' in source
    for table in ("worker_runtimes", "control_plane_events", "platform_messages"):
        assert f'"{table}"' in source
        assert f'op.drop_table("{table}")' in source


def test_daily_archive_migration_extends_head_with_durable_outputs():
    source = DAILY_ARCHIVE_REVISION.read_text(encoding="utf-8")
    assert 'down_revision: str | None = "c1e9a4b7d220"' in source
    assert '"daily_archive_runs"' in source
    assert '"source_file_ids_json"' in source
    assert '"source_manifest_sha256"' in source
    assert '"archive_file_id"' in source
    assert '"manifest_file_id"' in source
    assert 'op.drop_table("daily_archive_runs")' in source


def test_remnant_inventory_migration_extends_head_and_is_reversible():
    source = REMNANT_INVENTORY_REVISION.read_text(encoding="utf-8")
    assert 'down_revision: str | None = "e2f4b8c6a130"' in source
    for table in (
        "remnant_materials",
        "remnant_material_aliases",
        "remnant_import_batches",
        "remnant_import_items",
        "remnants",
        "remnant_parts",
    ):
        assert f'"{table}"' in source
        assert f'op.drop_table("{table}")' in source
    for constraint in (
        "uq_remnant_material_code",
        "uq_remnant_material_alias_normalized",
        "uq_remnant_import_item_batch_source",
        "uq_remnant_source_sha256",
        "uq_remnant_import_item_confirmation",
        "uq_remnant_part_number",
    ):
        assert f'"{constraint}"' in source


def test_excel_final_quality_migration_extends_current_head_and_is_reversible():
    source = EXCEL_FINAL_QUALITY_REVISION.read_text(encoding="utf-8")

    assert 'revision: str = "f3a7c9d2e6b1"' in source
    assert 'down_revision: str | None = "e2f4b8c6a130"' in source
    part_columns = (
        "import_component_no",
        "import_part_no",
        "source_batch",
        "team",
        "original_qty",
        "density_source",
        "material_utilization",
        "weight_validation",
    )
    batch_columns = (
        "quality_status",
        "warning_count",
        "severe_warning_count",
        "report_summary",
    )
    for column in part_columns:
        assert f'"{column}"' in source
        assert f'op.drop_column("excel_final_parts", "{column}")' in source
    for column in batch_columns:
        assert f'"{column}"' in source
        assert f'op.drop_column("excel_final_batches", "{column}")' in source
    assert "sa.JSON()" in source


def test_excel_final_decimal_migration_extends_quality_revision():
    source = EXCEL_FINAL_DECIMAL_REVISION.read_text(encoding="utf-8")

    assert 'revision: str = "2f6b8c1d4e90"' in source
    assert 'down_revision: str | None = "f3a7c9d2e6b1"' in source
    assert "sa.DECIMAL(precision=24, scale=9)" in source


def test_merge_revision_joins_excel_final_and_remnant_heads_without_ddl():
    source = MERGE_REVISION.read_text(encoding="utf-8")

    assert 'revision: str = "7c4d9e2a1b60"' in source
    assert 'down_revision: tuple[str, str] = ("2f6b8c1d4e90", "2b7e91d4c830")' in source
    assert "def upgrade() -> None:\n    pass" in source
    assert "def downgrade() -> None:\n    pass" in source
    assert "op." not in source


def test_remnant_auto_import_migration_extends_merge_head_and_is_reversible():
    source = REMNANT_AUTO_IMPORT_REVISION.read_text(encoding="utf-8")

    assert 'revision: str = "9d6e4a1b2c70"' in source
    assert 'down_revision: str | None = "7c4d9e2a1b60"' in source
    for column in (
        "import_mode",
        "default_project_no",
        "source_folder_name",
        "source_relative_path",
        "standard_parse_json",
    ):
        assert f'"{column}"' in source
    assert 'server_default="manual"' in source
    assert 'op.drop_constraint("uq_remnant_import_item_batch_source"' in source
    assert "DELETE duplicate_row" not in source
    assert "Cannot downgrade remnant auto import" in source
    assert 'op.create_unique_constraint(' in source
    assert 'op.drop_column("remnant_import_items", "standard_parse_json")' in source
    assert 'op.drop_column("remnant_import_batches", "import_mode")' in source


def test_workflow_excel_validation_migration_extends_head_and_is_reversible():
    source = WORKFLOW_EXCEL_VALIDATION_REVISION.read_text(encoding="utf-8")

    assert 'revision: str = "4e7c2a9b1d30"' in source
    assert 'down_revision: str | None = "7c4d9e2a1b60"' in source
    for column in (
        "validation_json",
        "validation_contract_version",
        "validated_sha256",
    ):
        assert f'"{column}"' in source
        assert f'op.drop_column("workflow_input_items", "{column}")' in source
    assert "sa.JSON()" in source
    assert "sa.Integer()" in source
    assert "sa.String(length=64)" in source


def test_linux_excel_stage_migration_is_fail_closed_and_reversible():
    source = LINUX_EXCEL_STAGE_REVISION.read_text(encoding="utf-8")

    assert 'revision: str = "5f8d3b0c2e41"' in source
    assert 'down_revision: str | None = "4e7c2a9b1d30"' in source
    assert "def _normalize_linux_workflows(" in source
    assert "def _restore_legacy_linux_workflows(" in source
    assert "has legacy Excel execution evidence" in source
    assert "is currently at legacy excel_final" in source
    assert "DELETE FROM workflow_stage_runs" in source
    assert "definition_revision" in source


def test_remnant_excel_workflow_merge_revision_joins_heads_without_ddl():
    source = REMNANT_EXCEL_WORKFLOW_MERGE_REVISION.read_text(encoding="utf-8")

    assert 'revision: str = "8a6c1f4e2b90"' in source
    assert (
        'down_revision: tuple[str, str] = ("6f4a8c2d1e90", "5f8d3b0c2e41")'
        in source
    )
    assert "def upgrade() -> None:\n    pass" in source
    assert "def downgrade() -> None:\n    pass" in source
    assert "op." not in source


def test_workflow_dxf_canonical_revision_is_fail_closed_and_reversible():
    source = WORKFLOW_DXF_CANONICAL_REVISION.read_text(encoding="utf-8")

    assert 'revision: str = "c7b2d4e9f601"' in source
    assert 'down_revision: str | None = "8a6c1f4e2b90"' in source
    assert "def _upgrade_linux_workflows(" in source
    assert "def _downgrade_linux_workflows(" in source
    assert "cannot infer revision 3 artifact" in source
    assert "cannot be represented by revision 2" in source
    assert "workflow_input_dwg" in source
    assert "workflow_input_dxf" in source
    assert "definition_revision" in source


def test_workflow_batch_export_migration_extends_head_and_is_reversible():
    source = WORKFLOW_BATCH_EXPORT_REVISION.read_text(encoding="utf-8")

    assert 'revision: str = "e9a1b2c3d4f5"' in source
    assert 'down_revision: str | None = "c8f1d2e3a490"' in source
    assert '"workflow_batch_exports"' in source
    assert '"purged_at"' in source
    assert 'op.drop_table("workflow_batch_exports")' in source
    assert 'op.drop_column("files", "purged_at")' in source


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
    source = DB_IMPLEMENTATION.read_text(encoding="utf-8")

    for table in (
        "agent_memory",
        "token_blacklist",
        "excel_final_batches",
        "excel_final_components",
        "excel_final_parts",
        "file_transfers",
        "storage_scan_findings",
        "storage_scan_runs",
        "workflow_artifacts",
        "workflow_batch_exports",
        "dxf_classification_runs",
        "dxf_classification_items",
        "dxf_split_runs",
        "dxf_split_items",
        "daily_archive_runs",
        "workflow_input_batches",
        "workflow_input_items",
        "workflow_runs",
        "workflow_stage_runs",
        "remnant_import_batches",
        "remnant_import_items",
        "remnant_material_aliases",
        "remnant_materials",
        "remnant_parts",
        "remnants",
    ):
        assert f'"{table}"' in source
    assert "create_engine(settings.sqlalchemy_database_url)" in source
    assert "ScriptDirectory.from_config" in source
    assert "repository must have exactly one Alembic head" in source
    assert "version != expected_head" in source
    assert 'version != "e2f4b8c6a130"' not in source
    assert '"files": {"deleted_at", "purged_at"}' in source
    assert '"jobs": {"progress_data", "attempt", "request_key"}' in source
    assert '"uq_jobs_actor_task_request_key"' in source
    assert '"job_steps": {"attempt"}' in source
    assert "identifier types are not BIGINT" in source
    assert "excel_final_batches.job_id is not unique" in source
    assert "files storage location is not unique" in source
