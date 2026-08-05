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
        "1.3.0",
        "steel-dxf-classify",
    ),
    "Stages/steel_dxf_split_v1.5.2": (
        "steel-dxf-split",
        "1.5.2",
        "steel-dxf-split",
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
    "ClassificationError",
    "REPORT_SCHEMA",
    "DxfBhStage2ClassificationBatch",
    "DxfBhStage2Input",
    "DxfClassificationItem",
    "DxfClassificationItemRead",
    "DxfClassificationGroupItemRead",
    "DxfClassificationGroupPage",
    "DxfClassificationGroupRead",
    "DxfNextStageInput",
    "DxfSplitCandidateInput",
    "DxfClassificationRun",
    "DxfClassificationRunRead",
    "build_classification_group_page",
    "build_classification_run_read",
    "classifier_project_name",
    "enqueue_dxf_classification_job",
    "latest_classification_run",
    "load_bh_stage2_classification_batch",
    "list_next_stage_inputs",
    "list_split_candidate_inputs",
    "run_dxf_classification",
}

SPLIT_PUBLIC_CONTRACT = {
    "BH_SOURCE_CONTRACT",
    "BOX_SOURCE_CONTRACT",
    "CLI_SCHEMA",
    "MANIFEST_SCHEMA",
    "MAX_AUTOMATIC_ATTEMPTS",
    "SELECTIVE_EXPORT_COOKIE_NAME",
    "SPLITTER_VERSION",
    "VALIDATION_SCHEMA",
    "DxfSplitError",
    "DxfSplitExcelHandoff",
    "DxfSplitHandoffDrawing",
    "DxfSplitItem",
    "DxfSplitItemRead",
    "DxfSplitReviewDecision",
    "DxfSplitReviewDecisionRead",
    "DxfSplitReviewDecisionWrite",
    "DxfSplitReviewPage",
    "DxfSplitRun",
    "DxfSplitRunRead",
    "build_dxf_split_run_read",
    "complete_split_review",
    "create_download_token",
    "decide_split_item",
    "dxf_split_file_reference_exists",
    "enqueue_dxf_splitting_job",
    "export_download_path",
    "export_filename",
    "export_preview",
    "find_dxf_split_file_workflow_id",
    "get_dxf_split_outcome",
    "get_excel_split_handoff",
    "latest_dxf_split_run",
    "list_split_review_items",
    "manual_review_archive_members",
    "reconcile_dxf_split_run_for_terminal_job",
    "reconcile_orphan_dxf_split_runs",
    "require_download_token",
    "review_candidate_archive_members",
    "split_candidate_available",
    "split_results_archive_members",
    "storage_members",
    "run_dxf_splitting",
}

EXPECTED_TASKS = {
    "app.workers.tasks_dxf.convert_dwg_to_dxf": "dxf",
    "app.workers.tasks_dxf.convert_dwg_to_dxf_batch": "dxf",
    "app.workers.tasks_dxf2dwg.convert_dxf_to_dwg": "dxf2dwg",
    "app.workers.tasks_dxf2dwg.convert_dxf_to_dwg_batch": "dxf2dwg",
    "app.workers.tasks_dxf2excel.extract_dxf_to_excel": "dxf2excel",
    "app.workers.tasks_dxf_classification.classify_steel_dxf": "dxf_classification",
    "app.workers.tasks_dxf_split.split_steel_dxf": "dxf_split",
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
    "modules/dxf_splitting": {
        "adapter.py",
        "execution.py",
        "persistence.py",
        "validation.py",
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
    assert (classifier_root / "VERSION").read_text(encoding="utf-8").strip() == "1.3.0"
    assert (classifier_root / "docs/IO_CONTRACT.md").is_file()
    splitter_root = REPO_ROOT / "Stages/steel_dxf_split_v1.5.2"
    assert (splitter_root / "VERSION").read_text(encoding="utf-8").strip() == "1.5.2"


def test_split_stage_vendors_runtime_slice_not_development_corpus() -> None:
    splitter_root = REPO_ROOT / "Stages/steel_dxf_split_v1.5.2"
    package_root = splitter_root / "src/steel_dxf_split"

    assert {
        path.name for path in (package_root / "release_evidence").glob("*.json")
    } == {
        "box_build_contract.json",
        "box_release_attestation.json",
        "project_tekla_bh_dxf_v1.json",
    }

    excluded = {
        ".python-version",
        "CONTEXT.md",
        "requirements-preview.txt",
        "uv.lock",
        "docs",
        "release",
        "samples",
        "scripts",
        "tests",
        "tools",
    }
    assert not {path.name for path in splitter_root.iterdir()} & excluded
    assert not list(splitter_root.rglob("*.dxf"))


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


def test_split_interface_is_exact_and_owns_two_tables() -> None:
    interface = importlib.import_module("app.modules.dxf_splitting.interface")

    assert set(interface.__all__) == SPLIT_PUBLIC_CONTRACT
    assert {
        interface.DxfSplitRun.__table__.name,
        interface.DxfSplitItem.__table__.name,
    } == {"dxf_split_runs", "dxf_split_items"}


def test_conversion_classification_and_split_tasks_keep_public_names_and_queues() -> None:
    cad_tasks = importlib.import_module("app.modules.cad_processing.tasks")
    classification_tasks = importlib.import_module("app.modules.dxf_classification.tasks")
    split_tasks = importlib.import_module("app.modules.dxf_splitting.tasks")
    task_objects = (
        cad_tasks.convert_dwg_to_dxf_task,
        cad_tasks.convert_dwg_to_dxf_batch_task,
        cad_tasks.convert_dxf_to_dwg_task,
        cad_tasks.convert_dxf_to_dwg_batch_task,
        cad_tasks.extract_dxf_to_excel_task,
        classification_tasks.classify_steel_dxf_task,
        split_tasks.split_steel_dxf_task,
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
    assert "app.modules.dxf_splitting.tasks" in names
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


def test_other_business_modules_use_only_processing_interfaces() -> None:
    protected = (
        "app.modules.cad_processing",
        "app.modules.dxf_classification",
        "app.modules.dxf_splitting",
    )
    violations: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        relative = path.relative_to(APP_ROOT)
        if relative.parts[:2] in {
            ("modules", "cad_processing"),
            ("modules", "dxf_classification"),
            ("modules", "dxf_splitting"),
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
