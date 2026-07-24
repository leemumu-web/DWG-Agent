#!/usr/bin/env python3
"""Validate the maintained Chinese documentation against repository contracts."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
REQUIRED_DOCS = {
    "README.md",
    "architecture/implementation-status.md",
    "architecture/overview.md",
    "architecture/platform-specification.md",
    "architecture/workflow.md",
    "guides/deployment.md",
    "guides/development.md",
    "guides/operations.md",
    "guides/security.md",
    "reference/api.md",
    "reference/configuration.md",
    "reference/database.md",
    "verification/current.md",
}

LINK_RE = re.compile(r"(?<!!)\[[^]]*]\(([^)]+)\)")

COMPONENT_READMES = (
    ROOT / "backend/README.md",
    ROOT / "backend/migrations/README.md",
    ROOT / "frontend/README.md",
    ROOT / "infra/README.md",
    ROOT / "infra/gateway/nginx/README.md",
    ROOT / "infra/database/mysql/README.md",
    ROOT / "infra/storage/minio/README.md",
    ROOT / "infra/messaging/rabbitmq/README.md",
    ROOT / "infra/operations/backup/README.md",
    ROOT / "infra/operations/monitoring/README.md",
    ROOT / "infra/verification/README.md",
    ROOT / "agents/cad-agent/README.md",
    ROOT / "agents/excel-agent/README.md",
    ROOT / "agents/report-agent/README.md",
    ROOT / "windows/README.md",
    ROOT / "windows/node-agent/README.md",
    ROOT / "windows/cam-runner/README.md",
    ROOT / "windows/sinocam-adapter/README.md",
    ROOT / "windows/protocols/README.md",
    ROOT / "Stages/excel_final/README.md",
)


def _documentation_set(errors: list[str]) -> None:
    current = {
        path.relative_to(DOCS).as_posix() for path in DOCS.rglob("*.md")
    }
    missing = REQUIRED_DOCS - current
    if missing:
        errors.append(
            "Required documentation is incomplete: "
            f"missing={sorted(missing)}"
        )
    unexpected_root_docs = {
        path.name for path in DOCS.glob("*.md") if path.name != "README.md"
    }
    if unexpected_root_docs:
        errors.append(
            "Documentation must be classified below docs/: "
            f"unclassified={sorted(unexpected_root_docs)}"
        )
    if (DOCS / "zh").exists():
        errors.append("docs/zh must not be recreated; maintained documentation is Chinese-only")
    forbidden = ("docs/zh/", "英文对应文档", "English mirror", "双语契约", "中英文参考")
    for path in sorted(DOCS.rglob("*.md")):
        content = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in content:
                errors.append(f"{path.relative_to(ROOT)} contains obsolete marker: {marker}")


def _read_required(path: Path, errors: list[str]) -> str:
    if not path.is_file():
        errors.append(f"missing required document: {path.relative_to(ROOT)}")
        return ""
    return path.read_text(encoding="utf-8")


def _generated_api_docs(errors: list[str]) -> None:
    sys.path.insert(0, str(ROOT / "scripts" / "docs"))
    from generate_api import render

    expected = {DOCS / "reference/api.md": render()}
    for path, generated in expected.items():
        if _read_required(path, errors) != generated:
            errors.append(
                f"{path.relative_to(ROOT)} is stale; run "
                "cd backend && uv run python ../scripts/docs/generate_api.py"
            )


def _owned_markdown_files() -> list[Path]:
    markdown_files = [
        ROOT / "README.md",
        ROOT / "README_EN.md",
        ROOT / "CHANGELOG.md",
        ROOT / "CONTRIBUTING.md",
        *COMPONENT_READMES,
    ]
    markdown_files.extend(sorted(DOCS.rglob("*.md")))
    for root in (
        ROOT / "backend/app",
        ROOT / "backend/tests",
        ROOT / "frontend/src",
        ROOT / "frontend/tests/e2e",
        ROOT / "infra",
        ROOT / "scripts",
        ROOT / "agents",
        ROOT / "Stages",
        ROOT / "windows",
    ):
        markdown_files.extend(sorted(root.rglob("README.md")))
    markdown_files.extend(
        ROOT / relative
        for relative in (
            "Stages/dwg2dxf/README.md",
            "Stages/dwg2dxf/convert/README.md",
            "Stages/dxf2dwg/README.md",
            "Stages/dxf2dwg/convert/README.md",
            "Stages/dxf2excel/README.md",
            "Stages/excel_final/PROCESS.md",
        )
    )
    return list(dict.fromkeys(markdown_files))


def _markdown_hygiene(errors: list[str]) -> None:
    for path in _owned_markdown_files():
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT)
        if "�" in content:
            errors.append(f"{relative} contains a Unicode replacement character")
        if content and not content.endswith("\n"):
            errors.append(f"{relative} must end with a newline")
        trailing = [
            line_number
            for line_number, line in enumerate(content.splitlines(), start=1)
            if line.rstrip() != line
        ]
        if trailing:
            errors.append(f"{relative} has trailing whitespace on lines {trailing}")

        in_fence = False
        previous_heading = 0
        fence_count = 0
        for line_number, line in enumerate(content.splitlines(), start=1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                fence_count += 1
                continue
            if in_fence:
                continue
            heading = re.match(r"^(#{1,6})\s+", line)
            if not heading:
                continue
            level = len(heading.group(1))
            if previous_heading and level > previous_heading + 1:
                errors.append(
                    f"{relative}:{line_number} skips heading level "
                    f"H{previous_heading} -> H{level}"
                )
            previous_heading = level
        if fence_count % 2:
            errors.append(f"{relative} has an unclosed fenced code block")


def _local_links(errors: list[str]) -> None:
    markdown_files = _owned_markdown_files()
    for source in markdown_files:
        if not source.exists():
            errors.append(f"Missing owned documentation file: {source.relative_to(ROOT)}")
            continue
        text = source.read_text(encoding="utf-8")
        for raw_target in LINK_RE.findall(text):
            target = raw_target.strip().strip("<>")
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            relative = unquote(target.split("#", 1)[0].split("?", 1)[0])
            if relative and not (source.parent / relative).resolve().exists():
                errors.append(
                    f"{source.relative_to(ROOT)} has missing local link target: {target}"
                )


def _port_convention(errors: list[str]) -> None:
    frontend_env = (ROOT / "frontend/.env.example").read_text(encoding="utf-8")
    if "http://127.0.0.1:8010" not in frontend_env:
        errors.append("frontend/.env.example must document local FastAPI at 127.0.0.1:8010")
    if "http://127.0.0.1:8000" in frontend_env:
        errors.append("frontend/.env.example still documents obsolete local FastAPI port 8000")

    # Backend port is unified to 8010 (local and container). No maintained file may
    # reintroduce the obsolete 8000 for the FastAPI backend.
    prod_env_path = ROOT / "frontend/.env.production"
    if prod_env_path.exists():
        prod_env = prod_env_path.read_text(encoding="utf-8")
        if "127.0.0.1:8000" in prod_env or "backend-api:8000" in prod_env:
            errors.append("frontend/.env.production still references obsolete FastAPI port 8000")


def _database_contract(errors: list[str]) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from app.bootstrap.model_registry import MODEL_MODULES as _model_modules  # noqa: F401
    from app.platform.config.settings import Settings
    from app.platform.database.base import Base

    fields = Settings.model_fields
    expected_defaults = {
        "DB_POOL_SIZE": fields["db_pool_size"].default,
        "DB_POOL_MAX_OVERFLOW": fields["db_pool_max_overflow"].default,
        "DB_POOL_TIMEOUT_SECONDS": fields["db_pool_timeout_seconds"].default,
        "DB_POOL_RECYCLE_SECONDS": fields["db_pool_recycle_seconds"].default,
    }
    sequence_tables = {
        "message_id_sequence",
        "queue_id_sequence",
        "task_id_sequence",
        "taskset_id_sequence",
    }
    model_table_count = len(Base.metadata.tables)
    alembic_config = Config(str(ROOT / "backend/alembic.ini"))
    alembic_config.set_main_option("script_location", str(ROOT / "backend/migrations"))
    heads = ScriptDirectory.from_config(alembic_config).get_heads()
    if len(heads) != 1:
        errors.append(f"Expected one Alembic head, found {heads}")
        current_head = None
    else:
        current_head = heads[0]

    for path in (DOCS / "reference/database.md",):
        content = _read_required(path, errors)
        for env_name, default in expected_defaults.items():
            if f"| `{env_name}` | {default}" not in content:
                errors.append(
                    f"{path.relative_to(ROOT)} does not match the {env_name} default ({default})"
                )
        for table in sequence_tables:
            if f"`{table}`" not in content:
                errors.append(f"{path.relative_to(ROOT)} omits Celery runtime table {table}")
        initialized_table_count = model_table_count + 1 + 8
        if (
            f"**{model_table_count} 张" not in content
            or f"**{initialized_table_count} 张" not in content
        ):
            errors.append(
                f"{path.relative_to(ROOT)} must document {model_table_count} model tables "
                f"and {initialized_table_count} tables after all Celery runtime tables exist"
            )
        if current_head and current_head not in content:
            errors.append(
                f"{path.relative_to(ROOT)} omits current Alembic head {current_head}"
            )
        if "settings.database_url.startswith" in content or "`pool_size` | 10" in content:
            errors.append(f"{path.relative_to(ROOT)} contains obsolete database pool examples")


def _repository_boundaries(errors: list[str]) -> None:
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    specification = _read_required(
        DOCS / "architecture/platform-specification.md", errors
    )
    deployment_doc = _read_required(DOCS / "guides/deployment.md", errors)

    if "`codex`" in specification or "codex branch" in specification.lower():
        errors.append("Technical specification contains an obsolete codex-branch status")

    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    nginx = (ROOT / "infra/gateway/nginx/nginx.conf").read_text(encoding="utf-8")
    dead_tls_mapping = '"443:8443"' in compose and not re.search(
        r"^\s*listen\s+8443\b", nginx, re.MULTILINE
    )
    if dead_tls_mapping:
        if "443/TLS 尚不可用" not in root_readme:
            errors.append("README must disclose that the current 443/TLS mapping is unavailable")
        if "没有可用 HTTPS" not in deployment_doc:
            errors.append("Deployment docs must disclose the inactive Compose TLS mapping")
    elif '"443:8443"' not in compose:
        if "不发布 443" not in deployment_doc:
            errors.append("Deployment docs must state that current Compose does not publish port 443")
        if "Compose 仅发布 HTTP" not in root_readme:
            errors.append("README must state that current Compose publishes HTTP only")

    git_entries = subprocess.run(
        ["git", "ls-files", "-s", "Stages/dxf2excel", "Stages/dxf2excel/**"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if any(entry.startswith("160000 ") for entry in git_entries):
        errors.append("Stages/dxf2excel must be a tracked source directory, not a gitlink")
    required_stage_files = {
        "Stages/dxf2excel/pyproject.toml",
        "Stages/dxf2excel/uv.lock",
        "Stages/dxf2excel/src/dxf2excel/pipeline.py",
        "Stages/dxf2excel/tests/test_decoder.py",
    }
    tracked_stage_files = {
        entry.split("\t", 1)[1] for entry in git_entries if "\t" in entry
    }
    missing_stage_files = required_stage_files - tracked_stage_files
    if missing_stage_files:
        errors.append(
            "Stages/dxf2excel tracked source is incomplete: "
            f"missing={sorted(missing_stage_files)}"
        )

    from generate_api import app

    schema = app.openapi()
    path_count = len(schema["paths"])
    operation_count = sum(
        1
        for path_item in schema["paths"].values()
        for method in path_item
        if method.lower() in {"get", "post", "put", "patch", "delete", "options", "head"}
    )
    if (
        f"{path_count} 个 OpenAPI path" not in root_readme
        or f"{operation_count} 个 operation" not in root_readme
    ):
        errors.append(
            f"README.md must document the current {path_count} OpenAPI paths and "
            f"{operation_count} operations"
        )

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from app.bootstrap.model_registry import load_models
    from app.platform.database.base import Base

    load_models()
    alembic_config = Config(str(ROOT / "backend/alembic.ini"))
    alembic_config.set_main_option("script_location", str(ROOT / "backend/migrations"))
    current_head = ScriptDirectory.from_config(alembic_config).get_current_head()
    model_table_count = len(Base.metadata.tables)
    initialized_table_count = model_table_count + 1 + 8
    database_doc = _read_required(DOCS / "reference/database.md", errors)
    if current_head and current_head not in database_doc:
        errors.append(
            f"docs/reference/database.md omits current Alembic head {current_head}"
        )
    if (
        f"{model_table_count} 张模型表" not in database_doc
        or f"最多为 **{initialized_table_count} 张表**" not in database_doc
    ):
        errors.append(
            "docs/reference/database.md must document the current model/runtime table "
            f"counts ({model_table_count}/{initialized_table_count})"
        )

    required_root_docs = ("CHANGELOG.md", "CONTRIBUTING.md")
    for relative in required_root_docs:
        if not (ROOT / relative).is_file():
            errors.append(f"Missing developer-facing root document: {relative}")


def _component_document_contract(errors: list[str]) -> None:
    for path in COMPONENT_READMES:
        if not path.exists():
            errors.append(f"Missing component README: {path.relative_to(ROOT)}")
            continue
        content = path.read_text(encoding="utf-8")
        if "## English" in content or "docs/zh/" in content:
            errors.append(f"{path.relative_to(ROOT)} contains obsolete mirror content")


def _production_docs_contract(errors: list[str]) -> None:
    main_source = (ROOT / "backend/app/bootstrap/application.py").read_text(
        encoding="utf-8"
    )
    production_disables_docs = (
        'docs_url="/docs" if (settings.app_env == "development" or settings.debug) else None'
        in main_source
    )
    if not production_disables_docs:
        errors.append("FastAPI runtime documentation gate changed; update documentation contract")
        return
    for path in (DOCS / "reference/api.md",):
        content = _read_required(path, errors)
        if "`APP_ENV=production`" not in content or "`DEBUG=false`" not in content:
            errors.append(
                f"{path.relative_to(ROOT)} must document disabled production runtime docs"
            )


def _workflow_dxf_contract(errors: list[str]) -> None:
    path = DOCS / "architecture/workflow.md"
    content = _read_required(path, errors)
    for marker in (
        "source_dwg",
        "canonical_dxf",
        "classified_dxf",
        "processed_dxf",
        "cam_input_dxf",
        "cam_output_dxf",
        "accepted_dxf",
        "delivery_dxf",
        "stage2_excel",
        "definition_revision 4",
    ):
        if marker not in content:
            errors.append(f"{path.relative_to(ROOT)} omits workflow contract {marker}")
    for obsolete in (
        "source_file/derived_dxf",
        "drawing_files",
        "processed_drawing",
        "processed_drawings",
        "cam_result",
        "delivery_file",
    ):
        if obsolete in content:
            errors.append(
                f"{path.relative_to(ROOT)} contains obsolete workflow contract {obsolete}"
            )


def check_docs() -> list[str]:
    errors: list[str] = []
    _documentation_set(errors)
    _generated_api_docs(errors)
    _markdown_hygiene(errors)
    _local_links(errors)
    _port_convention(errors)
    _database_contract(errors)
    _repository_boundaries(errors)
    _component_document_contract(errors)
    _production_docs_contract(errors)
    _workflow_dxf_contract(errors)
    return errors


def main() -> int:
    errors = check_docs()
    if errors:
        print(f"Documentation check failed ({len(errors)} error(s)):")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        "Documentation check passed: Chinese document set, generated API, "
        "Markdown hygiene, owned links, ports, database schema/head, repository boundaries, "
        "component documentation, "
        "and production documentation behavior."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
