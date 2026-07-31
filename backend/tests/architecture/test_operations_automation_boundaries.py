from __future__ import annotations

import ast
import importlib
from pathlib import Path
from types import SimpleNamespace

from fastapi.routing import APIRoute

from tests.support.paths import REPO_ROOT

APP_ROOT = REPO_ROOT / "backend" / "app"

DATA_ADMIN_ROUTES = [
    ("POST", "/daily-archives/preview", "preview_daily_archive_run"),
    ("POST", "/daily-archives", "create_daily_archive_run"),
    ("GET", "/daily-archives", "list_daily_archive_runs"),
    ("GET", "/daily-archives/{archive_id}", "get_daily_archive_run"),
    ("GET", "/mysql/tables", "list_mysql_tables"),
    ("GET", "/mysql/tables/{table_name}", "get_mysql_table"),
    ("GET", "/mysql/tables/{table_name}/rows", "list_mysql_rows"),
    ("POST", "/mysql/tables/{table_name}/rows", "create_mysql_row"),
    ("PATCH", "/mysql/tables/{table_name}/rows", "update_mysql_row"),
    ("DELETE", "/mysql/tables/{table_name}/rows", "delete_mysql_row"),
    ("DELETE", "/objects", "delete_registered_object"),
    ("POST", "/objects/moves", "move_registered_object"),
    ("GET", "/overview", "get_data_overview"),
    ("GET", "/files", "list_data_files"),
    ("GET", "/files/{file_id}", "get_data_file"),
    ("GET", "/objects", "list_storage_objects"),
    ("GET", "/objects/tree", "get_storage_object_tree"),
    ("GET", "/transfers", "list_transfers"),
    ("GET", "/transfers/{transfer_uid}", "get_transfer"),
    ("POST", "/scans", "start_scan"),
    ("GET", "/scans", "list_scans"),
    ("POST", "/remediations/preview", "preview_storage_remediation"),
    ("POST", "/remediations/execute", "execute_storage_remediation"),
    ("GET", "/scans/{scan_id}", "get_scan"),
    ("GET", "/scans/{scan_id}/findings", "list_scan_findings"),
]

CONTROL_PLANE_ROUTES = [
    ("GET", "/overview", "get_overview"),
    ("GET", "/events", "list_events"),
    ("GET", "/messages", "list_messages"),
    ("PATCH", "/messages/{message_id}/read", "mark_message_read"),
    ("GET", "/contracts/windows-node-agent", "get_windows_node_contract"),
    (
        "POST",
        "/maintenance/reconcile-stale-jobs",
        "queue_stale_job_reconciliation",
    ),
]

AGENT_ROUTES = [
    ("POST", "/agent-runs", "create_agent_run"),
    ("GET", "/agent-runs/{agent_run_id}", "get_agent_run"),
    ("GET", "/agent-runs/{agent_run_id}/steps", "get_agent_run_steps"),
    ("GET", "/agent-tools", "list_agent_tools"),
]

MOVED_TASKS = {
    "app.workers.tasks_maintenance.create_daily_archive": "maintenance",
    "app.workers.tasks_maintenance.reconcile_stale_jobs": "maintenance",
    "app.workers.tasks_report.run_stub_job": "report",
    "app.workers.tasks_report.scan_storage_consistency": "report",
}

REAL_TASK_MODULES = (
    "app.modules.cad_processing.tasks",
    "app.modules.dxf_classification.tasks",
    "app.modules.dxf_splitting.tasks",
    "app.modules.excel_processing.tasks",
    "app.modules.jobs.tasks",
    "app.modules.operations.daily_archive.tasks",
    "app.modules.operations.storage_reconciliation.tasks",
    "app.modules.operations.control_plane.tasks",
    "app.modules.remnant_inventory.tasks",
)

EXPECTED_OPERATION_FILES = {
    "audit": {"interface.py", "models.py", "routes.py", "schemas.py"},
    "daily_archive": {
        "execution.py",
        "models.py",
        "planning.py",
        "presentation.py",
        "routes.py",
        "schemas.py",
        "tasks.py",
    },
    "data_catalog": {
        "infrastructure.py",
        "mysql_routes.py",
        "object_mutations.py",
        "presentation.py",
        "queries.py",
        "routes.py",
        "system_routes.py",
    },
    "storage_reconciliation": {
        "presentation.py",
        "remediation.py",
        "routes.py",
        "scanning.py",
        "schemas.py",
        "tasks.py",
    },
    "control_plane": {
        "interface.py",
        "models.py",
        "routes.py",
        "service.py",
        "tasks.py",
    },
}


def _route_contract(router) -> list[tuple[str, str, str]]:
    contract: list[tuple[str, str, str]] = []
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = sorted(route.methods - {"HEAD", "OPTIONS"})
        assert len(methods) == 1, route.path
        contract.append((methods[0], route.path, route.endpoint.__name__))
    return contract


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_operations_tree_is_split_by_use_case_owner() -> None:
    root = APP_ROOT / "modules" / "operations"
    assert (root / "router.py").is_file()
    for module, expected in EXPECTED_OPERATION_FILES.items():
        actual = {path.name for path in (root / module).glob("*.py") if path.name != "__init__.py"}
        assert actual == expected, module


def test_operations_and_agent_routes_preserve_exact_contract_order() -> None:
    operations = importlib.import_module("app.modules.operations.router")
    audit = importlib.import_module("app.modules.operations.audit.routes")
    control_plane = importlib.import_module("app.modules.operations.control_plane.routes")
    system = importlib.import_module("app.modules.operations.data_catalog.system_routes")
    agent = importlib.import_module("app.modules.automation.agent.routes")

    assert _route_contract(operations.router) == DATA_ADMIN_ROUTES
    assert _route_contract(audit.router) == [
        ("GET", "", "list_audit_logs"),
        ("GET", "/{audit_log_id}", "get_audit_log"),
    ]
    assert _route_contract(control_plane.router) == CONTROL_PLANE_ROUTES
    assert _route_contract(system.router) == [
        ("GET", "/health", "get_system_health"),
        ("GET", "/infrastructure", "get_infrastructure_overview"),
        ("GET", "/health/oda", "get_oda_health"),
    ]
    assert _route_contract(agent.router) == AGENT_ROUTES


def test_operation_and_automation_table_owners_are_exact() -> None:
    from app.modules.automation.agent.models.memory import AgentMemory
    from app.modules.automation.agent.models.runs import AgentRun, AgentRunStep
    from app.modules.files.interface import StorageScanFinding, StorageScanRun
    from app.modules.operations.audit.models import AuditLog
    from app.modules.operations.control_plane.models import (
        ControlPlaneEvent,
        PlatformMessage,
        WorkerRuntime,
    )
    from app.modules.operations.daily_archive.models import DailyArchiveRun

    assert {
        AuditLog.__table__.name,
        ControlPlaneEvent.__table__.name,
        DailyArchiveRun.__table__.name,
        PlatformMessage.__table__.name,
        WorkerRuntime.__table__.name,
    } == {
        "audit_logs",
        "control_plane_events",
        "daily_archive_runs",
        "platform_messages",
        "worker_runtimes",
    }
    assert {
        AgentMemory.__table__.name,
        AgentRun.__table__.name,
        AgentRunStep.__table__.name,
    } == {"agent_memory", "agent_runs", "agent_run_steps"}
    assert StorageScanRun.__module__ == "app.modules.files.models"
    assert StorageScanFinding.__module__ == "app.modules.files.models"


def test_task_registry_contains_only_real_modules_and_keeps_public_names() -> None:
    from app.bootstrap.task_registry import load_tasks
    from app.platform.messaging.celery_app import (
        RESERVED_EXECUTION_QUEUES,
        celery_app,
    )

    modules = load_tasks()
    assert tuple(module.__name__ for module in modules) == REAL_TASK_MODULES
    assert RESERVED_EXECUTION_QUEUES == ("agent", "cad", "dispatch")

    for task_name, queue in MOVED_TASKS.items():
        assert task_name in celery_app.tasks
        matching = [
            route["queue"]
            for pattern, route in celery_app.conf.task_routes.items()
            if task_name.startswith(pattern.removesuffix("*"))
        ]
        assert matching == [queue], task_name

    registered = set(celery_app.tasks)
    assert not any(name.startswith("app.workers.tasks_agent.") for name in registered)
    assert not any(name.startswith("app.workers.tasks_cad.") for name in registered)
    assert not any(name.startswith("app.workers.tasks_dispatch.") for name in registered)


def test_reserved_execution_queues_keep_deterministic_routes_without_fake_tasks() -> None:
    from app.platform.messaging.celery_app import (
        RESERVED_EXECUTION_QUEUES,
        celery_app,
    )

    routes = celery_app.conf.task_routes
    for queue in RESERVED_EXECUTION_QUEUES:
        assert routes[f"app.workers.tasks_{queue}.*"] == {"queue": queue}


def test_excel_stage2_is_visible_to_control_plane_and_chinese_system_status(
    monkeypatch,
) -> None:
    from app.modules.operations.control_plane.service import PIPELINE_QUEUE_MAP
    from app.modules.operations.data_catalog import system_routes

    monkeypatch.setattr(system_routes.settings, "excel_stage2_pipeline_enabled", True)
    response = system_routes.get_system_health(
        SimpleNamespace(state=SimpleNamespace(request_id="system-health-test")),
        current_user=None,
    )

    assert PIPELINE_QUEUE_MAP["excel_stage2"] == "excel_stage2"
    assert response["data"]["features"]["excel_stage2_pipeline"] is True
    assert response["data"]["services"] == [
        {
            "code": "excel_stage2",
            "name": "Excel 第二阶段服务",
            "enabled": True,
        }
    ]


def test_automation_contract_is_explicit_and_non_executable() -> None:
    from app.modules.automation.contracts.interface import (
        automation_capability_contracts,
        windows_node_contract,
    )

    contracts = automation_capability_contracts()
    assert {item.code: item.status for item in contracts} == {
        "agent_runtime": "disabled",
        "mcp_cad": "not_implemented",
        "zwcad_worker": "external_not_implemented",
    }
    windows = windows_node_contract()
    assert windows["status"] == "pending"
    assert {
        "agent authentication",
        "lease fencing",
        "Named Pipe CAD runner",
        "command delivery",
    } <= set(windows["not_available"])


def test_legacy_horizontal_business_packages_and_fake_adapters_are_retired() -> None:
    for name in (
        "api",
        "models",
        "schemas",
        "services",
        "workers",
        "agents",
        "mcp_client",
        "repositories",
        "integrations/zwcad",
    ):
        assert not (APP_ROOT / name).exists(), name


def test_other_business_modules_use_operations_interfaces_only() -> None:
    violations: list[str] = []
    modules_root = APP_ROOT / "modules"
    for path in sorted(modules_root.rglob("*.py")):
        if "operations" in path.relative_to(modules_root).parts:
            continue
        for imported in _imports(path):
            if not imported.startswith("app.modules.operations."):
                continue
            if not imported.endswith(".interface"):
                violations.append(f"{path.relative_to(REPO_ROOT)} -> {imported}")
    assert violations == []
