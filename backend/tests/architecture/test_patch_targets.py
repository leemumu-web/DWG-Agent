"""Static guard against silently stale dotted monkeypatch and mock targets."""

from __future__ import annotations

import ast
import importlib
from collections.abc import Iterator
from pathlib import Path

from tests.support.paths import BACKEND_ROOT

TESTS_ROOT = BACKEND_ROOT / "tests"

# Dynamic dotted targets are rejected unless their exact source location is
# documented here with a reason.  HTTP client's ``client.patch(url)`` calls and
# object-form ``monkeypatch.setattr(object, name, value)`` calls are not dotted
# import targets and therefore are intentionally outside this contract.
DYNAMIC_TARGET_EXCLUSIONS: dict[str, str] = {}


def _mock_patch_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != "unittest.mock":
            continue
        for alias in node.names:
            if alias.name == "patch":
                names.add(alias.asname or alias.name)
    return names


def _patch_target_nodes(tree: ast.AST) -> Iterator[ast.Call]:
    patch_names = _mock_patch_names(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue

        is_mock_patch = isinstance(node.func, ast.Name) and node.func.id in patch_names
        is_string_monkeypatch = (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "setattr"
            and len(node.args) == 2
        )
        if not (is_mock_patch or is_string_monkeypatch):
            continue
        yield node


def _dotted_targets(path: Path) -> Iterator[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in _patch_target_nodes(tree):
        target = node.args[0]
        if isinstance(target, ast.Constant) and isinstance(target.value, str):
            location = f"{path.relative_to(TESTS_ROOT)}:{node.lineno}"
            yield location, target.value


def _dynamic_target_locations(path: Path) -> Iterator[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in _patch_target_nodes(tree):
        location = f"{path.relative_to(TESTS_ROOT)}:{node.lineno}"
        target = node.args[0]
        if not (isinstance(target, ast.Constant) and isinstance(target.value, str)):
            yield location


def _resolve_dotted_target(target: str) -> None:
    parts = target.split(".")
    for split_at in range(len(parts) - 1, 0, -1):
        module_name = ".".join(parts[:split_at])
        try:
            value = importlib.import_module(module_name)
        except ModuleNotFoundError as error:
            if error.name == module_name:
                continue
            raise

        for attribute in parts[split_at:]:
            value = getattr(value, attribute)
        return

    raise ModuleNotFoundError(f"no importable module prefix in {target!r}")


def test_literal_patch_targets_resolve_to_real_attributes() -> None:
    failures: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        for location, target in _dotted_targets(path):
            try:
                _resolve_dotted_target(target)
            except (AttributeError, ImportError) as error:
                failures.append(f"{location} -> {target}: {error}")

    assert failures == []


def test_dynamic_patch_target_exclusions_have_bounded_reasons() -> None:
    actual = {
        location
        for path in sorted(TESTS_ROOT.rglob("test_*.py"))
        for location in _dynamic_target_locations(path)
    }

    assert actual == set(DYNAMIC_TARGET_EXCLUSIONS), (
        "Every dynamic dotted patch target needs an exact source-location exemption, and stale "
        "exemptions must be removed"
    )
    assert all(reason.strip() for reason in DYNAMIC_TARGET_EXCLUSIONS.values())
