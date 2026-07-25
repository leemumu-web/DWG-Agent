from __future__ import annotations

import ast
import json
from pathlib import Path

from app.platform.database.base import Base
from tests.support.paths import REPO_ROOT

APP_ROOT = REPO_ROOT / "backend" / "app"
SNAPSHOT = json.loads(
    (REPO_ROOT / "docs" / "architecture" / "runtime-contract.json").read_text(
        encoding="utf-8"
    )
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_platform_never_imports_business_modules() -> None:
    violations: list[str] = []
    for path in sorted((APP_ROOT / "platform").rglob("*.py")):
        for imported in _imports(path):
            if imported == "app.modules" or imported.startswith("app.modules."):
                violations.append(f"{path.relative_to(REPO_ROOT)} -> {imported}")
    assert violations == []


def test_legacy_platform_directories_are_retired() -> None:
    for name in ("core", "db", "storage", "utils"):
        assert not (APP_ROOT / name).exists(), name


def test_no_python_import_uses_retired_platform_paths() -> None:
    retired = ("app.core", "app.db", "app.storage", "app.utils")
    violations: list[str] = []
    roots = (
        APP_ROOT,
        REPO_ROOT / "backend" / "migrations",
        REPO_ROOT / "backend" / "tests",
        REPO_ROOT / "scripts",
    )
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            for imported in _imports(path):
                if imported.startswith(retired):
                    violations.append(f"{path.relative_to(REPO_ROOT)} -> {imported}")
    assert violations == []


def test_model_registry_loads_every_contract_table() -> None:
    from app.bootstrap.model_registry import load_models

    modules = load_models()

    assert len(modules) == 17
    assert sorted(Base.metadata.tables) == SNAPSHOT["orm_tables"]


def test_task_registry_loads_every_stable_task_name() -> None:
    from app.bootstrap.task_registry import load_tasks
    from app.platform.messaging.celery_app import celery_app

    modules = load_tasks()
    names = sorted(
        name for name in celery_app.tasks if name.startswith("app.workers.tasks_")
    )

    assert len(modules) == 9
    assert names == SNAPSHOT["celery_tasks"]


def test_main_is_only_the_stable_asgi_facade() -> None:
    source = (APP_ROOT / "main.py").read_text(encoding="utf-8")

    assert source == (
        '"""Stable ASGI import facade."""\n\n'
        "from app.bootstrap.application import app\n\n"
        '__all__ = ["app"]\n'
    )
