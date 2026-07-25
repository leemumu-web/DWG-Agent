"""Repository contracts for the domain-oriented backend test layout."""

import ast

from tests.support.paths import BACKEND_ROOT, FRONTEND_ROOT, REPO_ROOT, STAGES_ROOT

TESTS_ROOT = BACKEND_ROOT / "tests"
EXPECTED_TEST_DOMAINS = {
    "architecture",
    "automation",
    "cad_processing",
    "contracts",
    "dxf_classification",
    "dxf_splitting",
    "excel_processing",
    "files",
    "identity",
    "infrastructure",
    "jobs",
    "operations",
    "projects",
    "remnant_inventory",
    "regression",
    "security",
    "workflows",
}


def test_backend_tests_are_not_flattened_at_the_suite_root() -> None:
    root_tests = sorted(path.name for path in TESTS_ROOT.glob("test_*.py"))

    assert root_tests == []
    assert {path.name for path in TESTS_ROOT.glob("*.py")} == {"__init__.py", "conftest.py"}


def test_backend_test_domains_are_explicit_and_complete() -> None:
    actual = {
        path.name
        for path in TESTS_ROOT.iterdir()
        if path.is_dir() and not path.name.startswith((".", "__")) and path.name != "support"
    }

    assert actual == EXPECTED_TEST_DOMAINS
    assert all(any((TESTS_ROOT / domain).glob("test_*.py")) for domain in actual)


def test_shared_test_paths_resolve_repository_layers() -> None:
    assert TESTS_ROOT == BACKEND_ROOT / "tests"
    assert BACKEND_ROOT == REPO_ROOT / "backend"
    assert FRONTEND_ROOT == REPO_ROOT / "frontend"
    assert STAGES_ROOT == REPO_ROOT / "Stages"
    assert all(path.is_dir() for path in (BACKEND_ROOT, FRONTEND_ROOT, STAGES_ROOT))


def test_test_modules_depend_only_on_the_shared_support_package() -> None:
    violations: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        for module in imported:
            if (module == "tests" or module.startswith("tests.")) and not module.startswith(
                "tests.support"
            ):
                violations.append(f"{path.relative_to(TESTS_ROOT)} -> {module}")

    assert violations == []


def test_repository_path_counting_is_centralized() -> None:
    path_helper = TESTS_ROOT / "support" / "paths.py"
    violations: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if path == path_helper:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(isinstance(node, ast.Name) and node.id == "__file__" for node in ast.walk(tree)):
            violations.append(str(path.relative_to(TESTS_ROOT)))

    assert violations == []
