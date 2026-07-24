from __future__ import annotations

import ast
import importlib
import tomllib
from pathlib import Path

from tests.support.paths import REPO_ROOT

APP_ROOT = REPO_ROOT / "backend" / "app"

STAGE_PRODUCTS = {
    "Stages/dwg2dxf": ("dwg-converter", "0.1.0", "dwg-converter"),
    "Stages/dxf2dwg": ("dxf-converter", "0.1.0", "dxf-converter"),
    "Stages/dxf2excel": ("dxf2excel", "0.1.0", "dxf2excel"),
    "Stages/steel_dxf_classifier_v1.1.0": (
        "steel-dxf-classifier",
        "1.2.0",
        "steel-dxf-classify",
    ),
}

CAD_PUBLIC_CONTRACT = {
    "MAX_DXF_SIZE_BYTES",
    "convert_dwg_directory",
    "enqueue_dwg_to_dxf_batch",
    "enqueue_dwg_to_dxf_job",
    "enqueue_dxf_to_dwg_batch",
    "enqueue_dxf_to_dwg_job",
    "enqueue_dxf_to_excel_job",
    "get_or_create_dxf_preview",
    "invalidate_dxf_previews_for_source",
    "preview_batch_name",
    "run_dwg_to_dxf_batch",
    "run_dwg_to_dxf_conversion",
    "run_dxf_to_dwg_batch",
    "run_dxf_to_dwg_conversion",
    "run_dxf_to_excel_extraction",
    "validate_dxf_source_size",
}

CLASSIFICATION_PUBLIC_CONTRACT = {
    "CLASSIFIER_VERSION",
    "CLI_SCHEMA",
    "REPORT_SCHEMA",
    "DxfClassificationItem",
    "DxfClassificationItemRead",
    "DxfClassificationRun",
    "DxfClassificationRunRead",
    "build_classification_run_read",
    "classifier_project_name",
    "enqueue_dxf_classification_job",
    "latest_classification_run",
    "run_dxf_classification",
}

EXPECTED_TASKS = {
    "app.workers.tasks_dxf.convert_dwg_to_dxf": "dxf",
    "app.workers.tasks_dxf.convert_dwg_to_dxf_batch": "dxf",
    "app.workers.tasks_dxf2dwg.convert_dxf_to_dwg": "dxf2dwg",
    "app.workers.tasks_dxf2dwg.convert_dxf_to_dwg_batch": "dxf2dwg",
    "app.workers.tasks_dxf2excel.extract_dxf_to_excel": "dxf2excel",
    "app.workers.tasks_dxf_classification.classify_steel_dxf": "dxf_classification",
}

EXPECTED_INTERNAL_LAYERS = {
    "modules/cad_processing/dwg_to_dxf": {
        "batch.py",
        "contracts.py",
        "execution.py",
        "persistence.py",
        "versions.py",
    },
    "modules/cad_processing/dxf_to_dwg": {
        "batch.py",
        "contracts.py",
        "execution.py",
        "persistence.py",
        "versions.py",
    },
    "modules/cad_processing/dxf_to_excel": {
        "contracts.py",
        "execution.py",
        "persistence.py",
        "staging.py",
    },
    "modules/dxf_classification": {
        "adapter.py",
        "execution.py",
        "persistence.py",
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


def test_stage_products_keep_paths_package_versions_and_cli_names() -> None:
    for relative, expected in STAGE_PRODUCTS.items():
        stage_root = REPO_ROOT / relative
        pyproject = stage_root / "pyproject.toml"
        assert stage_root.is_dir(), relative
        assert pyproject.is_file(), relative
        payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        project = payload["project"]
        scripts = project["scripts"]
        package_name, version, cli_name = expected
        assert project["name"] == package_name
        assert project["version"] == version
        assert cli_name in scripts

    classifier_root = REPO_ROOT / "Stages/steel_dxf_classifier_v1.1.0"
    assert (classifier_root / "VERSION").read_text(encoding="utf-8").strip() == "1.2.0"
    assert (classifier_root / "docs/IO_CONTRACT.md").is_file()


def test_cad_processing_interface_is_exact() -> None:
    interface = importlib.import_module("app.modules.cad_processing.interface")

    assert set(interface.__all__) == CAD_PUBLIC_CONTRACT


def test_classification_interface_is_exact_and_owns_two_tables() -> None:
    interface = importlib.import_module("app.modules.dxf_classification.interface")

    assert set(interface.__all__) == CLASSIFICATION_PUBLIC_CONTRACT
    assert {
        interface.DxfClassificationRun.__table__.name,
        interface.DxfClassificationItem.__table__.name,
    } == {"dxf_classification_runs", "dxf_classification_items"}


def test_conversion_and_classification_tasks_keep_public_names_and_queues() -> None:
    cad_tasks = importlib.import_module("app.modules.cad_processing.tasks")
    classification_tasks = importlib.import_module("app.modules.dxf_classification.tasks")
    task_objects = (
        cad_tasks.convert_dwg_to_dxf_task,
        cad_tasks.convert_dwg_to_dxf_batch_task,
        cad_tasks.convert_dxf_to_dwg_task,
        cad_tasks.convert_dxf_to_dwg_batch_task,
        cad_tasks.extract_dxf_to_excel_task,
        classification_tasks.classify_steel_dxf_task,
    )

    assert {task.name for task in task_objects} == set(EXPECTED_TASKS)

    from app.platform.messaging.celery_app import celery_app

    routes = celery_app.conf.task_routes
    for task_name, queue in EXPECTED_TASKS.items():
        matching = [
            route["queue"]
            for pattern, route in routes.items()
            if task_name.startswith(pattern.removesuffix("*"))
        ]
        assert matching == [queue], task_name


def test_task_registry_uses_domain_modules_not_legacy_worker_files() -> None:
    from app.bootstrap.task_registry import load_tasks

    names = {module.__name__ for module in load_tasks()}

    assert "app.modules.cad_processing.tasks" in names
    assert "app.modules.dxf_classification.tasks" in names
    assert not names & {
        "app.workers.tasks_dxf",
        "app.workers.tasks_dxf2dwg",
        "app.workers.tasks_dxf2excel",
        "app.workers.tasks_dxf_classification",
    }


def test_processing_internals_are_split_by_traceable_responsibility() -> None:
    for relative, expected_files in EXPECTED_INTERNAL_LAYERS.items():
        directory = APP_ROOT / relative
        assert expected_files <= {path.name for path in directory.glob("*.py")}, relative

    assert (APP_ROOT / "modules/cad_processing/preview_rendering.py").is_file()

    orchestration_files = (
        APP_ROOT / "modules/cad_processing/dwg_to_dxf/execution.py",
        APP_ROOT / "modules/cad_processing/dxf_to_dwg/execution.py",
        APP_ROOT / "modules/cad_processing/dxf_to_excel/execution.py",
    )
    for path in orchestration_files:
        assert "app.modules.files.interface" not in _imports(path), path

    assert "subprocess" not in _imports(APP_ROOT / "modules/dxf_classification/execution.py")
    assert "ezdxf" not in _imports(APP_ROOT / "modules/cad_processing/preview.py")


def test_other_business_modules_use_only_cad_and_classification_interfaces() -> None:
    protected = ("app.modules.cad_processing", "app.modules.dxf_classification")
    violations: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        relative = path.relative_to(APP_ROOT)
        if relative.parts[:2] in {
            ("modules", "cad_processing"),
            ("modules", "dxf_classification"),
        } or relative.parts[:1] == ("bootstrap",):
            continue
        for imported in _imports(path):
            for prefix in protected:
                if imported.startswith(prefix) and not imported.startswith(f"{prefix}.interface"):
                    violations.append(f"{relative} -> {imported}")

    assert violations == []


def test_legacy_cad_and_classification_implementation_files_are_retired() -> None:
    retired = (
        "models/dxf_classification.py",
        "schemas/dxf_classification_schema.py",
        "services/cad_batch_service.py",
        "services/dxf2dwg_service.py",
        "services/dxf2excel_service.py",
        "services/dxf_classification_service.py",
        "services/dxf_preview_service.py",
        "services/dxf_service.py",
        "services/dxf_stats.py",
        "workers/tasks_dxf.py",
        "workers/tasks_dxf2dwg.py",
        "workers/tasks_dxf2excel.py",
        "workers/tasks_dxf_classification.py",
    )

    assert [path for path in retired if (APP_ROOT / path).exists()] == []
