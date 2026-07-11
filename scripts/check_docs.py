#!/usr/bin/env python3
"""Validate bilingual documentation structure, generated API docs, and local links."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ZH_DOCS = DOCS / "zh"

HEADING_RE = re.compile(r"^(#{1,6})\s+", re.MULTILINE)
LINK_RE = re.compile(r"(?<!!)\[[^]]*]\(([^)]+)\)")
ENDPOINT_RE = re.compile(r"/api/v1/[A-Za-z0-9_./{}?=*-]+")
ENV_RE = re.compile(r"`([A-Z][A-Z0-9_]{2,})`")
REVISION_RE = re.compile(r"\b[0-9a-f]{12}\b")
GIT_SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")
SHELL_FENCE_RE = re.compile(r"```(?:bash|sh|dotenv)\n(.*?)```", re.DOTALL)

COMPONENT_READMES = (
    ROOT / "backend/README.md",
    ROOT / "backend/migrations/README.md",
    ROOT / "frontend/README.md",
    ROOT / "infra/README.md",
    ROOT / "infra/nginx/README.md",
    ROOT / "agents/cad-agent/README.md",
    ROOT / "agents/excel-agent/README.md",
    ROOT / "agents/report-agent/README.md",
    ROOT / "cad-worker/README.md",
    ROOT / "Stages/excel_final/README.md",
)


def _structure(text: str) -> tuple[list[int], int, int]:
    headings = [len(match.group(1)) for match in HEADING_RE.finditer(text)]
    fences = sum(line.lstrip().startswith("```") for line in text.splitlines())
    table_rows = sum(line.strip().startswith("|") for line in text.splitlines())
    return headings, fences, table_rows


def _shell_commands(text: str) -> tuple[str, ...]:
    commands: list[str] = []
    for block in SHELL_FENCE_RE.findall(text):
        commands.extend(
            line.strip()
            for line in block.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    return tuple(commands)


def _technical_tokens(
    text: str,
) -> tuple[set[str], set[str], set[str], set[str], tuple[str, ...]]:
    return (
        set(ENDPOINT_RE.findall(text)),
        set(ENV_RE.findall(text)),
        set(REVISION_RE.findall(text)),
        set(GIT_SHA_RE.findall(text)),
        _shell_commands(text),
    )


def _doc_pairs(errors: list[str]) -> None:
    english = {path.name for path in DOCS.glob("*.md")}
    chinese = {path.name for path in ZH_DOCS.glob("*.md")}
    if english != chinese:
        errors.append(
            "Bilingual file sets differ: "
            f"English-only={sorted(english - chinese)}, Chinese-only={sorted(chinese - english)}"
        )

    for name in sorted(english & chinese):
        en_text = (DOCS / name).read_text(encoding="utf-8")
        zh_text = (ZH_DOCS / name).read_text(encoding="utf-8")
        en_structure = _structure(en_text)
        zh_structure = _structure(zh_text)
        if en_structure != zh_structure:
            errors.append(
                f"docs/{name} structure differs from docs/zh/{name}: "
                f"{en_structure} != {zh_structure}"
            )
        en_tokens = _technical_tokens(en_text)
        zh_tokens = _technical_tokens(zh_text)
        if en_tokens != zh_tokens:
            labels = (
                "endpoints",
                "environment variables",
                "migration revisions",
                "Git commit identifiers",
                "shell commands",
            )
            for label, en_set, zh_set in zip(labels, en_tokens, zh_tokens, strict=True):
                if en_set != zh_set:
                    if isinstance(en_set, set) and isinstance(zh_set, set):
                        detail = (
                            f"English-only={sorted(en_set - zh_set)}, "
                            f"Chinese-only={sorted(zh_set - en_set)}"
                        )
                    else:
                        detail = f"English={en_set}, Chinese={zh_set}"
                    errors.append(f"docs/{name} {label} differ: {detail}")


def _generated_api_docs(errors: list[str]) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from generate_api_docs import render

    expected = {
        DOCS / "api.md": render(chinese=False),
        ZH_DOCS / "api.md": render(chinese=True),
    }
    for path, generated in expected.items():
        if path.read_text(encoding="utf-8") != generated:
            errors.append(
                f"{path.relative_to(ROOT)} is stale; run "
                "cd backend && uv run python ../scripts/generate_api_docs.py"
            )


def _owned_markdown_files() -> list[Path]:
    markdown_files = [
        ROOT / "README.md",
        ROOT / "DWG-Agent企业平台技术规范.md",
        ROOT / "CLAUDE.md",
        *COMPONENT_READMES,
    ]
    markdown_files.extend(sorted(DOCS.glob("*.md")))
    markdown_files.extend(sorted(ZH_DOCS.glob("*.md")))
    markdown_files.extend(
        ROOT / relative
        for relative in (
            "Stages/dwg2dxf/README.md",
            "Stages/dwg2dxf/convert/README.md",
            "Stages/dxf2dwg/README.md",
            "Stages/dxf2dwg/convert/README.md",
            "Stages/excel_final/PROCESS.md",
            "Stages/excel_final/multi_split/CLAUDE.md",
        )
    )
    return list(dict.fromkeys(markdown_files))


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


def _database_contract(errors: list[str]) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from app import models as _models  # noqa: F401
    from app.core.config import Settings
    from app.db.base import Base

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

    for path in (DOCS / "database.md", ZH_DOCS / "database.md"):
        content = path.read_text(encoding="utf-8")
        for env_name, default in expected_defaults.items():
            if f"| `{env_name}` | {default}" not in content:
                errors.append(
                    f"{path.relative_to(ROOT)} does not match the {env_name} default ({default})"
                )
        for table in sequence_tables:
            if f"`{table}`" not in content:
                errors.append(f"{path.relative_to(ROOT)} omits Celery runtime table {table}")
        if "**31" not in content or f"**{model_table_count}" not in content:
            errors.append(f"{path.relative_to(ROOT)} must document 31 total and 22 business tables")
        if current_head and current_head not in content:
            errors.append(
                f"{path.relative_to(ROOT)} omits current Alembic head {current_head}"
            )
        if "settings.database_url.startswith" in content or "`pool_size` | 10" in content:
            errors.append(f"{path.relative_to(ROOT)} contains obsolete database pool examples")


def _repository_boundaries(errors: list[str]) -> None:
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    specification = (ROOT / "DWG-Agent企业平台技术规范.md").read_text(encoding="utf-8")
    deployment_docs = (
        (DOCS / "deployment.md").read_text(encoding="utf-8"),
        (ZH_DOCS / "deployment.md").read_text(encoding="utf-8"),
    )

    if "`codex`" in specification or "codex branch" in specification.lower():
        errors.append("Technical specification contains an obsolete codex-branch status")

    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    nginx = (ROOT / "infra/nginx/nginx.conf").read_text(encoding="utf-8")
    dead_tls_mapping = '"443:8443"' in compose and not re.search(
        r"^\s*listen\s+8443\b", nginx, re.MULTILINE
    )
    if dead_tls_mapping:
        if "443/TLS 尚不可用" not in root_readme:
            errors.append("README must disclose that the current 443/TLS mapping is unavailable")
        if not all(
            marker in content
            for marker, content in zip(
                ("no functional HTTPS", "没有可用 HTTPS"), deployment_docs, strict=True
            )
        ):
            errors.append("Deployment docs must disclose the inactive Compose TLS mapping")

    gitlink = subprocess.run(
        ["git", "ls-files", "-s", "Stages/dxf2excel"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()
    broken_gitlink = gitlink.startswith("160000 ") and not (ROOT / ".gitmodules").exists()
    if broken_gitlink:
        required = "Stages/dxf2excel"
        for path in (
            ROOT / "README.md",
            DOCS / "deployment.md",
            ZH_DOCS / "deployment.md",
            DOCS / "roadmap.md",
            ZH_DOCS / "roadmap.md",
        ):
            if required not in path.read_text(encoding="utf-8"):
                errors.append(
                    f"{path.relative_to(ROOT)} omits the broken dxf2excel gitlink boundary"
                )


def _component_bilingual_contract(errors: list[str]) -> None:
    for path in COMPONENT_READMES:
        if not path.exists():
            errors.append(f"Missing component README: {path.relative_to(ROOT)}")
            continue
        content = path.read_text(encoding="utf-8")
        if "## English" not in content or "## 中文" not in content:
            errors.append(
                f"{path.relative_to(ROOT)} must contain English and Chinese sections"
            )


def _production_docs_contract(errors: list[str]) -> None:
    main_source = (ROOT / "backend/app/main.py").read_text(encoding="utf-8")
    production_disables_docs = (
        'docs_url="/docs" if (settings.app_env == "development" or settings.debug) else None'
        in main_source
    )
    if not production_disables_docs:
        errors.append("FastAPI runtime documentation gate changed; update documentation contract")
        return
    for path in (DOCS / "api.md", ZH_DOCS / "api.md"):
        content = path.read_text(encoding="utf-8")
        if "`APP_ENV=production`" not in content or "`DEBUG=false`" not in content:
            errors.append(
                f"{path.relative_to(ROOT)} must document disabled production runtime docs"
            )


def check_docs() -> list[str]:
    errors: list[str] = []
    _doc_pairs(errors)
    _generated_api_docs(errors)
    _local_links(errors)
    _port_convention(errors)
    _database_contract(errors)
    _repository_boundaries(errors)
    _component_bilingual_contract(errors)
    _production_docs_contract(errors)
    return errors


def main() -> int:
    errors = check_docs()
    if errors:
        print(f"Documentation check failed ({len(errors)} error(s)):")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        "Documentation check passed: bilingual structure/tokens/commands, generated API, "
        "owned links, ports, database schema/head, repository boundaries, component mirrors, "
        "and production documentation behavior."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
