from __future__ import annotations

import sys

from tests.support.paths import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))

from scripts.architecture.check_module_catalog import (  # noqa: E402
    CATALOG_PATH,
    build_catalog_coverage,
    load_catalog,
    validate_catalog,
)


def test_module_catalog_has_no_validation_errors() -> None:
    catalog = load_catalog(CATALOG_PATH)

    assert validate_catalog(catalog) == []


def test_module_catalog_owns_every_runtime_contract_once() -> None:
    coverage = build_catalog_coverage(load_catalog(CATALOG_PATH))

    assert len(coverage["tables"]) == 46
    assert len(coverage["http_operations"]) == 200
    assert len(coverage["celery_tasks"]) == 14
    assert coverage["duplicate_table_owners"] == {}
    assert coverage["duplicate_operation_owners"] == {}
    assert coverage["duplicate_task_owners"] == {}


def test_placeholders_are_explicit_and_traceable() -> None:
    modules = {item["code"]: item for item in load_catalog(CATALOG_PATH)["modules"]}

    for code in ("messaging_target", "windows_execution"):
        assert modules[code]["status"] in {"placeholder", "external"}
        assert modules[code]["architecture_nodes"]
        assert modules[code]["docs"]
