from __future__ import annotations

from app.modules.workflows.models import WorkflowRetentionExport


def test_workflow_retention_export_model_keeps_independent_durable_state():
    table = WorkflowRetentionExport.__table__

    assert table.name == "workflow_retention_exports"
    assert {column.name for column in table.columns} >= {
        "export_uid",
        "workflow_run_id",
        "created_by",
        "status",
        "manifest_json",
        "manifest_sha256",
        "token_digest",
        "token_expires_at",
        "file_count",
        "preview_cache_count",
        "source_size_bytes",
        "reclaimable_size_bytes",
        "downloaded_at",
        "task_id",
        "purge_transfer_uid",
        "purge_started_at",
        "purged_at",
        "purged_file_count",
        "purged_size_bytes",
        "error_code",
        "error_message",
    }
    unique_names = {constraint.name for constraint in table.constraints}
    assert "uq_workflow_retention_exports_uid" in unique_names
    indexed_columns = {
        tuple(column.name for column in index.columns) for index in table.indexes
    }
    assert ("workflow_run_id", "status") in indexed_columns
    assert ("created_by", "created_at") in indexed_columns
