"""Fail when a maintained repository partition has no local introduction."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

BASE_PARTITIONS = [
    "agents",
    "agents/cad-agent",
    "agents/excel-agent",
    "agents/report-agent",
    "Stages",
    "Stages/dwg2dxf",
    "Stages/dxf2dwg",
    "Stages/dxf2excel",
    "Stages/excel_final",
    "Stages/steel_dxf_classifier_v1.1.0",
    "backend/app/bootstrap",
    "backend/app/integrations",
    "backend/app/modules",
    "backend/app/platform",
    *[
        f"backend/app/modules/{name}"
        for name in (
            "automation",
            "cad_processing",
            "dxf_classification",
            "excel_processing",
            "files",
            "identity",
            "jobs",
            "operations",
            "projects",
            "workflows",
        )
    ],
    *[
        f"backend/app/platform/{name}"
        for name in (
            "config",
            "database",
            "http",
            "messaging",
            "observability",
            "security",
            "storage",
        )
    ],
    *[
        f"backend/tests/{name}"
        for name in (
            "architecture",
            "automation",
            "cad_processing",
            "contracts",
            "dxf_classification",
            "excel_processing",
            "files",
            "identity",
            "infrastructure",
            "jobs",
            "operations",
            "projects",
            "regression",
            "security",
            "support",
            "workflows",
        )
    ],
    "infra",
    *[
        f"infra/{name}"
        for name in ("database", "gateway", "messaging", "operations", "storage", "verification")
    ],
    "scripts",
    *[
        f"scripts/{name}"
        for name in ("architecture", "cad", "docs", "lib", "storage", "windows")
    ],
    "frontend/src/app",
    "frontend/src/features",
    "frontend/src/shared",
    *[
        f"frontend/src/features/{name}"
        for name in (
            "automation",
            "cad-processing",
            "dashboard",
            "excel-processing",
            "files",
            "identity",
            "jobs",
            "operations",
            "projects",
            "reviews",
            "workflows",
        )
    ],
    "frontend/src/features/cad-processing/components",
    "frontend/src/features/cad-processing/components/conversion",
    "frontend/src/features/cad-processing/components/dxf2excel",
    "frontend/src/features/cad-processing/hooks",
    "frontend/src/features/excel-processing/components",
    "frontend/src/features/excel-processing/model",
    "frontend/src/features/operations/api",
    "frontend/src/features/operations/components",
    "frontend/src/features/operations/components/data-console",
    "frontend/src/features/operations/pages",
    "frontend/src/features/operations/types",
    "frontend/src/features/workflows/model",
    *[
        f"frontend/src/shared/{name}"
        for name in ("api", "auth", "components", "styles")
    ],
    "frontend/tests/e2e",
    *[
        f"frontend/tests/e2e/{name}"
        for name in (
            "contracts",
            "excel-processing",
            "files",
            "jobs",
            "operations",
            "support",
            "workflows",
        )
    ],
    "windows",
    "windows/cam-runner",
    "windows/node-agent",
    "windows/protocols",
    "windows/sinocam-adapter",
]

SOURCE_SUFFIXES = {
    ".conf",
    ".css",
    ".mjs",
    ".ps1",
    ".py",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
SOURCE_ROOTS = (
    "backend/app",
    "backend/tests",
    "frontend/src",
    "frontend/tests/e2e",
    "infra",
    "scripts",
)
IGNORED_DIRECTORY_NAMES = {
    ".pytest_cache",
    "__pycache__",
    "dist",
    "logs",
    "node_modules",
    "ssl",
}
BOUNDARY_MARKERS = (
    "边界",
    "不得",
    "不能",
    "未实现",
    "cannot",
    "do not",
    "does not",
    "excludes",
    "must not",
    "not delivered",
    "not implemented",
)


def _source_owned_partitions() -> list[str]:
    """Discover leaf ownership boundaries instead of trusting a hand-maintained count."""
    discovered: list[str] = []
    for relative_root in SOURCE_ROOTS:
        source_root = ROOT / relative_root
        if not source_root.is_dir():
            continue
        directories = [source_root, *sorted(path for path in source_root.rglob("*") if path.is_dir())]
        for directory in directories:
            if any(part in IGNORED_DIRECTORY_NAMES for part in directory.parts):
                continue
            owns_source = any(
                child.is_file()
                and child.name != "__init__.py"
                and child.suffix in SOURCE_SUFFIXES
                for child in directory.iterdir()
            )
            if owns_source:
                discovered.append(directory.relative_to(ROOT).as_posix())
    return sorted(discovered)


PARTITIONS = tuple(dict.fromkeys([*BASE_PARTITIONS, *_source_owned_partitions()]))


def _direct_source_files(directory: Path) -> list[Path]:
    return sorted(
        child
        for child in directory.iterdir()
        if child.is_file()
        and child.name not in {"README.md", "__init__.py"}
        and child.suffix in SOURCE_SUFFIXES
    )


def validation_errors() -> list[str]:
    errors: list[str] = []
    for relative in PARTITIONS:
        directory = ROOT / relative
        readme = directory / "README.md"
        if not directory.is_dir():
            errors.append(f"partition directory missing: {relative}")
            continue
        if not readme.is_file():
            errors.append(f"partition README missing: {relative}/README.md")
            continue
        content = readme.read_text(encoding="utf-8").strip()
        lowered = content.lower()
        if len(content) < 240 or not content.startswith("#"):
            errors.append(
                f"partition README lacks substantive business detail: {relative}/README.md"
            )
        source_files = _direct_source_files(directory)
        if source_files and not any(source.name in content for source in source_files):
            errors.append(
                "partition README does not identify any owned source file: "
                f"{relative}/README.md"
            )
        if not any(marker in lowered for marker in BOUNDARY_MARKERS):
            errors.append(
                "partition README does not state a responsibility or capability boundary: "
                f"{relative}/README.md"
            )
    return errors


def main() -> int:
    errors = validation_errors()
    if errors:
        print(f"Partition documentation check failed ({len(errors)} error(s)):")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Partition documentation check passed: {len(PARTITIONS)} documented boundaries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
