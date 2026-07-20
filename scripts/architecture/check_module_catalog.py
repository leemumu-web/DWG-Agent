#!/usr/bin/env python3
"""Validate module ownership against the live repository contract."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.architecture.snapshot_contracts import build_contract_snapshot  # noqa: E402

CATALOG_PATH = REPO_ROOT / "docs" / "architecture" / "module-catalog.json"
ALLOWED_STATUSES = {"external", "implemented", "partial", "placeholder"}
PATH_FIELDS = ("backend_paths", "frontend_paths", "stages", "tests", "docs")
REQUIRED_MODULE_FIELDS = {
    "architecture_nodes",
    "backend_paths",
    "celery_tasks",
    "code",
    "docs",
    "frontend_paths",
    "http_prefixes",
    "notes",
    "queues",
    "stages",
    "status",
    "tables",
    "tests",
    "title",
}


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _owners(values_by_module: list[tuple[str, list[str]]]) -> tuple[dict[str, str], dict[str, list[str]]]:
    candidates: dict[str, list[str]] = defaultdict(list)
    for module_code, values in values_by_module:
        for value in values:
            candidates[value].append(module_code)
    primary = {value: owners[0] for value, owners in candidates.items()}
    duplicates = {
        value: sorted(owners) for value, owners in candidates.items() if len(owners) > 1
    }
    return primary, duplicates


def _operation_path(operation: str) -> str:
    parts = operation.split(" ", 2)
    if len(parts) != 3:
        raise ValueError(f"invalid HTTP operation contract: {operation}")
    return parts[1]


def _prefix_matches(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def build_catalog_coverage(catalog: dict[str, Any]) -> dict[str, Any]:
    snapshot = build_contract_snapshot()
    modules = catalog.get("modules", [])
    table_owners, duplicate_table_owners = _owners(
        [(item.get("code", ""), item.get("tables", [])) for item in modules]
    )
    task_owners, duplicate_task_owners = _owners(
        [(item.get("code", ""), item.get("celery_tasks", [])) for item in modules]
    )

    operation_candidates: dict[str, list[str]] = defaultdict(list)
    for operation in snapshot["http_operations"]:
        path = _operation_path(operation)
        for item in modules:
            if any(_prefix_matches(path, prefix) for prefix in item.get("http_prefixes", [])):
                operation_candidates[operation].append(item.get("code", ""))
    operation_owners = {
        operation: owners[0] for operation, owners in operation_candidates.items() if owners
    }
    duplicate_operation_owners = {
        operation: sorted(owners)
        for operation, owners in operation_candidates.items()
        if len(owners) > 1
    }
    return {
        "celery_tasks": task_owners,
        "duplicate_operation_owners": duplicate_operation_owners,
        "duplicate_table_owners": duplicate_table_owners,
        "duplicate_task_owners": duplicate_task_owners,
        "http_operations": operation_owners,
        "tables": table_owners,
    }


def validate_catalog(catalog: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if catalog.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    modules = catalog.get("modules")
    if not isinstance(modules, list) or not modules:
        return [*errors, "modules must be a non-empty list"]

    codes = [item.get("code") for item in modules]
    if len(codes) != len(set(codes)):
        errors.append("module codes must be unique")

    for item in modules:
        code = item.get("code", "<missing>")
        missing_fields = REQUIRED_MODULE_FIELDS - set(item)
        if missing_fields:
            errors.append(f"{code}: missing fields {sorted(missing_fields)}")
        if item.get("status") not in ALLOWED_STATUSES:
            errors.append(f"{code}: invalid status {item.get('status')!r}")
        if not item.get("architecture_nodes"):
            errors.append(f"{code}: architecture_nodes must not be empty")
        if not item.get("docs"):
            errors.append(f"{code}: docs must not be empty")
        for field in PATH_FIELDS:
            values = item.get(field, [])
            if not isinstance(values, list):
                errors.append(f"{code}: {field} must be a list")
                continue
            if values != sorted(set(values)):
                errors.append(f"{code}: {field} must be sorted and unique")
            for relative in values:
                if not (REPO_ROOT / relative).exists():
                    errors.append(f"{code}: missing {field} path {relative}")
        for field in ("architecture_nodes", "celery_tasks", "http_prefixes", "queues", "tables"):
            values = item.get(field, [])
            if not isinstance(values, list):
                errors.append(f"{code}: {field} must be a list")
            elif values != sorted(set(values)):
                errors.append(f"{code}: {field} must be sorted and unique")

    snapshot = build_contract_snapshot()
    coverage = build_catalog_coverage(catalog)
    expected_tables = set(snapshot["orm_tables"])
    actual_tables = set(coverage["tables"])
    if missing := expected_tables - actual_tables:
        errors.append(f"unowned ORM tables: {sorted(missing)}")
    if extra := actual_tables - expected_tables:
        errors.append(f"catalog tables absent from ORM: {sorted(extra)}")
    if coverage["duplicate_table_owners"]:
        errors.append(f"duplicate ORM table owners: {coverage['duplicate_table_owners']}")

    expected_tasks = set(snapshot["celery_tasks"])
    actual_tasks = set(coverage["celery_tasks"])
    if missing := expected_tasks - actual_tasks:
        errors.append(f"unowned Celery tasks: {sorted(missing)}")
    if extra := actual_tasks - expected_tasks:
        errors.append(f"catalog tasks absent from Celery: {sorted(extra)}")
    if coverage["duplicate_task_owners"]:
        errors.append(f"duplicate Celery task owners: {coverage['duplicate_task_owners']}")

    expected_operations = set(snapshot["http_operations"])
    actual_operations = set(coverage["http_operations"])
    if missing := expected_operations - actual_operations:
        errors.append(f"unowned HTTP operations: {sorted(missing)}")
    if coverage["duplicate_operation_owners"]:
        errors.append(
            f"duplicate HTTP operation owners: {coverage['duplicate_operation_owners']}"
        )
    return sorted(errors)


def main() -> int:
    if not CATALOG_PATH.is_file():
        print(f"ERROR: missing {CATALOG_PATH.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 1
    catalog = load_catalog()
    errors = validate_catalog(catalog)
    if errors:
        print(f"Module catalog check failed ({len(errors)} error(s)):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    coverage = build_catalog_coverage(catalog)
    print(
        "Module catalog check passed: "
        f"{len(catalog['modules'])} modules, "
        f"{len(coverage['tables'])} tables, "
        f"{len(coverage['http_operations'])} HTTP operations, "
        f"{len(coverage['celery_tasks'])} Celery tasks."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
