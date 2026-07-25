from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from app.platform.http.exceptions import AppHTTPException
from tests.support.paths import REPO_ROOT

APP_ROOT = REPO_ROOT / "backend" / "app"

WORKFLOW_TABLES = {
    "workflow_artifacts",
    "workflow_batch_exports",
    "workflow_input_batches",
    "workflow_input_items",
    "workflow_runs",
    "workflow_stage_runs",
}

WORKFLOW_PUBLIC_CONTRACT = {
    "FrozenInputReference",
    "WorkflowArtifact",
    "WorkflowBatchExport",
    "WorkflowInputBatch",
    "WorkflowInputItem",
    "WorkflowRun",
    "WorkflowStageRun",
    "attach_artifact",
    "bind_stage_job",
    "cancel_workflow",
    "complete_manual_stage",
    "create_workflow",
    "find_frozen_input_reference",
    "find_production_file_workflow_id",
    "production_file_reference_exists",
    "get_workflow_or_404",
    "list_workflow_templates",
    "read_verified_input_object",
    "recompute_workflow",
    "start_workflow",
    "sync_workflow_from_jobs",
}

EXPECTED_ROUTES = [
    (("GET",), "/templates", "get_workflow_templates"),
    (("GET",), "", "list_workflows"),
    (("POST",), "", "create_workflow_api"),
    (("POST",), "/production-projects", "create_production_project_api"),
    (("POST",), "/{workflow_id}/artifacts", "create_workflow_artifact"),
    (
        ("GET",),
        "/{workflow_id}/batch-exports/preview",
        "preview_workflow_batch_export",
    ),
    (
        ("POST",),
        "/{workflow_id}/batch-exports",
        "create_workflow_batch_export",
    ),
    (
        ("GET",),
        "/{workflow_id}/batch-exports/{export_uid}",
        "get_workflow_batch_export",
    ),
    (
        ("GET",),
        "/{workflow_id}/batch-exports/{export_uid}/download",
        "download_workflow_batch_export",
    ),
    (
        ("POST",),
        "/{workflow_id}/batch-exports/{export_uid}/purge",
        "purge_workflow_batch_export",
    ),
    (("GET",), "/{workflow_id}/download-archive", "download_workflow_archive"),
    (
        ("GET",),
        "/{workflow_id}/stages/{stage_code}/download-archive",
        "download_workflow_stage_archive",
    ),
    (
        ("GET",),
        "/{workflow_id}/stages/excel_stage1/download-result",
        "download_excel_stage1_result",
    ),
    (
        ("GET",),
        "/{workflow_id}/stages/excel_stage1/preflight",
        "preflight_excel_stage1_api",
    ),
    (
        ("POST",),
        "/{workflow_id}/stages/{stage_code}/executions",
        "execute_workflow_stage",
    ),
    (("GET",), "/{workflow_id}/dxf-classification", "get_dxf_classification"),
    (
        ("GET",),
        "/{workflow_id}/dxf-classification/groups/{group_key}",
        "get_dxf_classification_group",
    ),
    (
        ("GET",),
        "/{workflow_id}/dxf-classification/groups/{group_key}/download-archive",
        "download_dxf_classification_group_archive",
    ),
    (
        ("GET",),
        "/{workflow_id}/dxf-classification/download-archive",
        "download_all_dxf_classification_archive",
    ),
    (
        ("GET",),
        "/{workflow_id}/drawing-processing",
        "get_drawing_processing",
    ),
    (
        ("GET",),
        "/{workflow_id}/drawing-processing/runs/{run_id}/manual-review-archive",
        "download_manual_review_archive",
    ),
    (
        ("GET",),
        "/{workflow_id}/drawing-processing/runs/{run_id}/review-items",
        "get_split_review_items",
    ),
    (
        ("PUT",),
        "/{workflow_id}/drawing-processing/runs/{run_id}/review-items/{item_id}/decision",
        "put_split_review_decision",
    ),
    (
        ("POST",),
        "/{workflow_id}/drawing-processing/runs/{run_id}/review-completion",
        "complete_split_review_api",
    ),
    (
        ("GET",),
        "/{workflow_id}/drawing-processing/runs/{run_id}/review-candidates-archive",
        "download_split_review_candidates_archive",
    ),
    (
        ("GET",),
        "/{workflow_id}/drawing-processing/runs/{run_id}/results-archive",
        "download_split_results_archive",
    ),
    (("GET",), "/{workflow_id}", "get_workflow"),
    (("POST",), "/{workflow_id}/start", "start_workflow_api"),
    (
        ("POST",),
        "/{workflow_id}/stages/{stage_code}/completion",
        "complete_stage_api",
    ),
    (
        ("POST",),
        "/{workflow_id}/cancellation-requests",
        "cancel_workflow_api",
    ),
    (("POST",), "/{workflow_id}/input-batch", "create_batch_api"),
    (("GET",), "/{workflow_id}/input-batch", "get_batch_api"),
    (
        ("POST",),
        "/{workflow_id}/input-excel",
        "import_input_excel_api",
    ),
    (
        ("POST",),
        "/{workflow_id}/input-dwg-folder",
        "import_input_dwg_folder_api",
    ),
    (
        ("DELETE",),
        "/{workflow_id}/input-folder",
        "clear_input_folder_api",
    ),
    (
        ("POST",),
        "/{workflow_id}/input-batch/conversion-requests",
        "convert_batch_api",
    ),
    (("POST",), "/{workflow_id}/input-batch/freeze", "freeze_batch_api"),
]

EXPECTED_PRODUCTION_STAGES = [
    (
        "source_intake",
        "manual",
        "implemented",
        None,
        ("dwg_files", "excel_file"),
        ("source_dwg", "source_excel", "canonical_dxf"),
        ("source_dwg", "source_excel", "canonical_dxf"),
    ),
    (
        "dxf_classification",
        "automated",
        "implemented",
        "steel_dxf_classification",
        ("canonical_dxf",),
        ("classified_dxf", "classification_report", "classification_manifest"),
        ("classified_dxf", "classification_report", "classification_manifest"),
    ),
    (
        "drawing_processing",
        "automated",
        "implemented",
        "drawing_processing",
        ("classified_dxf",),
        (
            "processed_dxf",
            "weld_allowance_dxf",
            "split_report",
            "weld_allowance_report",
            "validation_report",
            "bh_split_ledger",
            "split_manifest",
        ),
        (
            "processed_dxf",
            "weld_allowance_dxf",
            "split_report",
            "weld_allowance_report",
            "validation_report",
            "bh_split_ledger",
            "split_manifest",
        ),
    ),
    (
        "excel_stage1",
        "automated",
        "implemented",
        "excel_stage1",
        ("source_excel", "processed_dxf", "bh_split_ledger"),
        ("stage1_excel",),
        ("stage1_excel",),
    ),
    (
        "excel_stage2",
        "placeholder",
        "placeholder",
        "excel_stage2",
        ("stage1_excel", "processed_dxf"),
        ("stage2_excel",),
        ("stage2_excel",),
    ),
    (
        "design_barrier",
        "manual",
        "implemented",
        None,
        ("processed_dxf", "stage2_excel"),
        ("review_record",),
        ("review_record",),
    ),
    (
        "cam_packaging",
        "placeholder",
        "placeholder",
        "cam_packaging",
        ("processed_dxf", "stage2_excel", "review_record"),
        ("cam_input_dxf", "cam_package_manifest"),
        ("cam_input_dxf", "cam_package_manifest"),
    ),
    (
        "windows_cam",
        "external",
        "external",
        "windows_cam",
        ("cam_input_dxf", "cam_package_manifest"),
        ("cam_output_dxf", "runner_diagnostics"),
        ("cam_output_dxf",),
    ),
    (
        "result_acceptance",
        "placeholder",
        "placeholder",
        "result_acceptance",
        ("cam_output_dxf",),
        ("accepted_dxf", "acceptance_report"),
        ("accepted_dxf", "acceptance_report"),
    ),
    (
        "delivery_archive",
        "manual",
        "implemented",
        None,
        ("accepted_dxf", "stage2_excel", "acceptance_report"),
        ("delivery_dxf", "delivery_excel", "archive_manifest"),
        ("delivery_dxf", "delivery_excel", "archive_manifest"),
    ),
]

EXPECTED_INTERNAL_LAYERS = {
    "modules/workflows": {
        "access.py",
        "artifacts.py",
        "batch_exports.py",
        "contracts.py",
        "interface.py",
        "job_sync.py",
        "lifecycle.py",
        "stage_execution.py",
        "templates.py",
    },
    "modules/workflows/models": {"exports.py", "intake.py", "orchestration.py"},
    "modules/workflows/schemas": {"exports.py", "intake.py", "orchestration.py"},
    "modules/workflows/intake": {
        "conversion.py",
        "freeze.py",
        "presentation.py",
        "registration.py",
    },
    "modules/workflows/routes": {
        "artifacts.py",
        "batch_exports.py",
        "classification.py",
        "commands.py",
        "execution.py",
        "intake.py",
        "queries.py",
        "router.py",
        "splitting.py",
        "templates.py",
    },
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _routes(router) -> list[tuple[tuple[str, ...], str, str]]:
    return [(tuple(sorted(route.methods or ())), route.path, route.name) for route in router.routes]


def test_workflow_interface_is_exact_and_owns_six_tables() -> None:
    workflows = importlib.import_module("app.modules.workflows.interface")

    assert set(workflows.__all__) == WORKFLOW_PUBLIC_CONTRACT
    assert {
        workflows.WorkflowArtifact.__table__.name,
        workflows.WorkflowBatchExport.__table__.name,
        workflows.WorkflowInputBatch.__table__.name,
        workflows.WorkflowInputItem.__table__.name,
        workflows.WorkflowRun.__table__.name,
        workflows.WorkflowStageRun.__table__.name,
    } == WORKFLOW_TABLES


def test_workflow_router_preserves_all_operations_order_and_tags() -> None:
    module = importlib.import_module("app.modules.workflows.routes.router")

    routes = _routes(module.router)
    assert routes == EXPECTED_ROUTES
    assert routes.index((("GET",), "/templates", "get_workflow_templates")) < routes.index(
        (("GET",), "/{workflow_id}", "get_workflow")
    )
    assert all(
        route.tags
        == (
            ["workflow-inputs"]
            if "/input-" in route.path
            else ["workflows"]
        )
        for route in module.router.routes
    )


def test_linux_production_contract_keeps_server_derived_dxf_and_honest_gaps() -> None:
    registration = importlib.import_module("app.modules.workflows.intake.registration")
    templates = importlib.import_module("app.modules.workflows.templates")

    assert registration.classify_human_input_extension(".dwg") == "source_dwg"
    assert registration.classify_human_input_extension(".xls") == "source_excel"
    assert registration.classify_human_input_extension(".xlsx") == "source_excel"
    assert registration.classify_human_input_extension(".xlsm") == "source_excel"
    with pytest.raises(AppHTTPException) as raised:
        registration.classify_human_input_extension(".dxf")
    assert raised.value.detail["code"] == "INPUT_DXF_NOT_ALLOWED"

    production = templates.WORKFLOW_TEMPLATES["linux_production"]
    assert [
        (
            stage.code,
            stage.execution_mode,
            stage.implementation_status,
            stage.execution_kind,
            tuple(stage.required_inputs),
            tuple(stage.artifact_types),
            tuple(stage.required_outputs),
        )
        for stage in production.stages
    ] == EXPECTED_PRODUCTION_STAGES


def test_workflow_internal_responsibilities_are_traceable() -> None:
    for relative, expected_files in EXPECTED_INTERNAL_LAYERS.items():
        directory = APP_ROOT / relative
        assert expected_files <= {path.name for path in directory.glob("*.py")}, relative

    assert "subprocess" not in _imports(APP_ROOT / "modules/workflows/stage_execution.py")
    assert "openpyxl" not in _imports(APP_ROOT / "modules/workflows/intake/conversion.py")
    assert "app.modules.jobs.interface" not in _imports(
        APP_ROOT / "modules/workflows/models/orchestration.py"
    )


def test_other_business_modules_use_only_workflow_interface() -> None:
    violations: list[str] = []
    prefix = "app.modules.workflows"
    for path in sorted(APP_ROOT.rglob("*.py")):
        relative = path.relative_to(APP_ROOT)
        if relative.parts[:2] == ("modules", "workflows") or relative.parts[:1] == ("bootstrap",):
            continue
        for imported in _imports(path):
            if imported.startswith(prefix) and not imported.startswith(f"{prefix}.interface"):
                violations.append(f"{relative} -> {imported}")

    assert violations == []


def test_file_delete_guard_and_classifier_use_workflow_interface() -> None:
    for relative in (
        "modules/files/access.py",
        "modules/dxf_classification/execution.py",
        "modules/dxf_classification/persistence.py",
    ):
        imports = _imports(APP_ROOT / relative)
        assert "app.modules.workflows.interface" in imports
        assert not any(name.startswith("app.models.workflow") for name in imports)
        assert not any(name.startswith("app.services.workflow") for name in imports)


def test_model_registry_uses_workflow_domain_package() -> None:
    from app.bootstrap.model_registry import load_models

    names = {module.__name__ for module in load_models()}
    assert "app.modules.workflows.models" in names
    assert "app.models.workflow" not in names
    assert "app.models.workflow_input" not in names


def test_legacy_workflow_implementation_files_are_retired() -> None:
    retired = (
        "api/v1/workflow_inputs_api.py",
        "api/v1/workflows_api.py",
        "models/workflow.py",
        "models/workflow_input.py",
        "schemas/workflow_input_schema.py",
        "schemas/workflow_schema.py",
        "services/workflow_input_service.py",
        "services/workflow_service.py",
    )

    assert [path for path in retired if (APP_ROOT / path).exists()] == []
