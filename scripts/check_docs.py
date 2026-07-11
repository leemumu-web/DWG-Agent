#!/usr/bin/env python3
"""Validate bilingual documentation structure, generated API docs, and local links."""

from __future__ import annotations

import re
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


def _structure(text: str) -> tuple[list[int], int, int]:
    headings = [len(match.group(1)) for match in HEADING_RE.finditer(text)]
    fences = sum(line.lstrip().startswith("```") for line in text.splitlines())
    table_rows = sum(line.strip().startswith("|") for line in text.splitlines())
    return headings, fences, table_rows


def _technical_tokens(text: str) -> tuple[set[str], set[str], set[str]]:
    return (
        set(ENDPOINT_RE.findall(text)),
        set(ENV_RE.findall(text)),
        set(REVISION_RE.findall(text)),
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
            labels = ("endpoints", "environment variables", "migration revisions")
            for label, en_set, zh_set in zip(labels, en_tokens, zh_tokens, strict=True):
                if en_set != zh_set:
                    errors.append(
                        f"docs/{name} {label} differ: "
                        f"English-only={sorted(en_set - zh_set)}, "
                        f"Chinese-only={sorted(zh_set - en_set)}"
                    )


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


def _local_links(errors: list[str]) -> None:
    markdown_files = [ROOT / "README.md", ROOT / "DWG-Agent企业平台技术规范.md"]
    markdown_files.extend(sorted(DOCS.glob("*.md")))
    markdown_files.extend(sorted(ZH_DOCS.glob("*.md")))
    for source in markdown_files:
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
    from app.core.config import Settings

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
        if "**31" not in content or "**22" not in content:
            errors.append(f"{path.relative_to(ROOT)} must document 31 total and 22 business tables")
        if "settings.database_url.startswith" in content or "`pool_size` | 10" in content:
            errors.append(f"{path.relative_to(ROOT)} contains obsolete database pool examples")


def check_docs() -> list[str]:
    errors: list[str] = []
    _doc_pairs(errors)
    _generated_api_docs(errors)
    _local_links(errors)
    _port_convention(errors)
    _database_contract(errors)
    return errors


def main() -> int:
    errors = check_docs()
    if errors:
        print(f"Documentation check failed ({len(errors)} error(s)):")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        "Documentation check passed: bilingual structure, tokens, generated API, "
        "links, ports, database defaults."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
