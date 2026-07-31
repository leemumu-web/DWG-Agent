from __future__ import annotations

import ast
import importlib
from pathlib import Path

from tests.support.paths import REPO_ROOT

APP_ROOT = REPO_ROOT / "backend" / "app"

EXCEL_TABLES = {
    "excel_final_batches",
    "excel_final_components",
    "excel_final_parts",
}

EXCEL_PUBLIC_CONTRACT = {
    "ExcelFinalBatch",
    "ExcelFinalComponent",
    "ExcelFinalInputError",
    "ExcelFinalProcessError",
    "ExcelFinalUnavailableError",
    "ExcelInputFailure",
    "ExcelStage1Inspection",
    "ExcelFinalPart",
    "cleanup_excel_processing_rows",
    "enqueue_excel_final_job",
    "enqueue_excel_stage2_job",
    "inspect_excel_stage1_bytes",
    "run_excel_final_processing",
    "run_excel_stage2_processing",
}

EXPECTED_ROUTES = [
    (("POST",), "/upload", "upload_excel"),
    (("POST",), "/process", "process_file"),
    (("POST",), "/upload-and-process", "upload_and_process"),
    (("GET",), "/overview", "get_overview"),
    (("GET",), "/batches", "list_batches"),
    (("GET",), "/parts/search", "search_parts"),
    (("GET",), "/weights/lookup", "lookup_weight"),
    (("GET",), "/health", "health_check"),
    (("GET",), "/process/{job_id}", "get_process_status"),
    (("GET",), "/process/{job_id}/download", "download_result"),
    (("GET",), "/batches/{batch_id}", "get_batch_detail"),
    (("GET",), "/batches/{batch_id}/parts", "list_batch_parts"),
    (("GET",), "/batches/{batch_id}/parts/{part_id}", "get_part_detail"),
    (("GET",), "/batches/{batch_id}/components", "list_batch_components"),
]

EXPECTED_INTERNAL_FILES = {
    "access.py",
    "execution.py",
    "idempotency.py",
    "importers.py",
    "interface.py",
    "models.py",
    "persistence.py",
    "presentation.py",
    "schemas.py",
    "stage_adapter.py",
    "stage2_execution.py",
    "stage_runner.py",
    "staging.py",
    "tasks.py",
    "uploads.py",
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


def test_excel_interface_is_exact_and_owns_relationship_projection() -> None:
    excel = importlib.import_module("app.modules.excel_processing.interface")

    assert set(excel.__all__) == EXCEL_PUBLIC_CONTRACT
    assert {
        excel.ExcelFinalBatch.__table__.name,
        excel.ExcelFinalComponent.__table__.name,
        excel.ExcelFinalPart.__table__.name,
    } == EXCEL_TABLES


def test_excel_router_preserves_operations_and_static_precedence() -> None:
    module = importlib.import_module("app.modules.excel_processing.routes.router")

    routes = _routes(module.router)
    assert routes == EXPECTED_ROUTES
    first_parameter = next(
        index for index, (_methods, path, _name) in enumerate(routes) if "{" in path
    )
    assert all("{" not in path for _methods, path, _name in routes[:first_parameter])


def test_excel_task_keeps_public_name_and_queue() -> None:
    tasks = importlib.import_module("app.modules.excel_processing.tasks")

    assert tasks.process_excel_final_task.name == (
        "app.workers.tasks_excel_final.process_excel_final"
    )
    from app.platform.messaging.celery_app import celery_app

    assert celery_app.conf.task_routes["app.workers.tasks_excel_final.*"] == {
        "queue": "excel_final"
    }


def test_excel_stage2_task_has_stable_public_name() -> None:
    tasks = importlib.import_module("app.modules.excel_processing.tasks")

    assert tasks.process_excel_stage2_task.name == (
        "app.workers.tasks_excel_stage2.process_excel_stage2"
    )
    from app.platform.messaging.celery_app import celery_app

    assert celery_app.conf.task_routes["app.workers.tasks_excel_stage2.*"] == {
        "queue": "excel_stage2"
    }


def test_excel_internal_responsibilities_are_traceable() -> None:
    module_root = APP_ROOT / "modules" / "excel_processing"

    assert EXPECTED_INTERNAL_FILES <= {path.name for path in module_root.glob("*.py")}
    assert "subprocess" not in _imports(module_root / "execution.py")
    assert "openpyxl" not in _imports(module_root / "routes" / "catalog.py")
    assert "app.modules.jobs.interface" not in _imports(module_root / "models.py")


def test_other_business_modules_use_only_excel_interface() -> None:
    violations: list[str] = []
    prefix = "app.modules.excel_processing"
    for path in sorted(APP_ROOT.rglob("*.py")):
        relative = path.relative_to(APP_ROOT)
        if relative.parts[:2] == ("modules", "excel_processing") or relative.parts[:1] == (
            "bootstrap",
        ):
            continue
        for imported in _imports(path):
            if imported.startswith(prefix) and not imported.startswith(f"{prefix}.interface"):
                violations.append(f"{relative} -> {imported}")

    assert violations == []


def test_jobs_request_excel_cleanup_through_interface_only() -> None:
    for filename in ("lifecycle.py", "recovery.py"):
        path = APP_ROOT / "modules" / "jobs" / filename
        imports = _imports(path)
        assert "app.modules.excel_processing.interface" in imports
        assert not any(name.startswith("app.models.excel_final") for name in imports)
        assert "ExcelFinalBatch" not in path.read_text(encoding="utf-8")


def test_registries_use_excel_domain_modules() -> None:
    from app.bootstrap.model_registry import load_models
    from app.bootstrap.task_registry import load_tasks

    assert "app.modules.excel_processing.models" in {module.__name__ for module in load_models()}
    assert "app.modules.excel_processing.tasks" in {module.__name__ for module in load_tasks()}


def test_legacy_excel_implementation_files_are_retired() -> None:
    retired = (
        "api/v1/excel_final_api.py",
        "integrations/excel_final.py",
        "integrations/excel_final_runner.py",
        "models/excel_final.py",
        "services/excel_final_service.py",
        "workers/tasks_excel_final.py",
    )

    assert [path for path in retired if (APP_ROOT / path).exists()] == []
