#!/usr/bin/env python3
"""Create a one-run Docker environment for continuous integration.

The generated file is private, placeholder-free, and intentionally cannot be
created outside CI. Secret values never leave the file through stdout/stderr.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from pathlib import Path
from typing import NoReturn

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / ".env.docker.example"
PROJECT_PATTERN = re.compile(r"dwg-agent-ci-[A-Za-z0-9][A-Za-z0-9-]{0,79}\Z")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--port", type=int, required=True)
    return parser.parse_args()


def _fail(message: str) -> NoReturn:
    print(f"CI environment error: {message}", file=sys.stderr)
    raise SystemExit(2)


def _render(template: str, overrides: dict[str, str]) -> str:
    rendered: list[str] = []
    remaining = dict(overrides)
    for line in template.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            rendered.append(line)
            continue
        key = line.split("=", 1)[0]
        if key in remaining:
            rendered.append(f"{key}={remaining.pop(key)}")
        else:
            rendered.append(line)
    if remaining:
        rendered.extend(("", "# ── Continuous integration overrides ─────────────────────────"))
        rendered.extend(f"{key}={value}" for key, value in remaining.items())
    return "\n".join(rendered) + "\n"


def main() -> None:
    args = _parse_args()
    if os.environ.get("CI", "").lower() != "true" and os.environ.get(
        "GITHUB_ACTIONS", ""
    ).lower() != "true":
        _fail("refusing to create credentials outside CI")
    if not PROJECT_PATTERN.fullmatch(args.project):
        _fail("project must be an isolated dwg-agent-ci-* name")
    if not 1024 <= args.port <= 65535:
        _fail("port must be between 1024 and 65535")

    mysql_password = secrets.token_hex(24)
    mysql_root_password = secrets.token_hex(24)
    minio_user = f"ci{secrets.token_hex(8)}"
    minio_password = secrets.token_hex(32)
    admin_password = f"Ci-{secrets.token_hex(24)}"
    overrides = {
        "CI_COMPOSE_PROJECT": args.project,
        "DOCKER_MIN_FREE_GIB": "5",
        "HTTP_BIND_ADDRESS": "127.0.0.1",
        "HTTP_PORT": str(args.port),
        "MYSQL_PASSWORD": mysql_password,
        "MYSQL_ROOT_PASSWORD": mysql_root_password,
        "MYSQL_INNODB_BUFFER_POOL_SIZE": "512M",
        "MYSQL_MAX_CONNECTIONS": "100",
        "MINIO_ACCESS_KEY": minio_user,
        "MINIO_ROOT_USER": minio_user,
        "MINIO_SECRET_KEY": minio_password,
        "MINIO_ROOT_PASSWORD": minio_password,
        "JWT_SECRET_KEY": secrets.token_hex(48),
        "SUPER_ADMIN_USERNAME": "super_admin",
        "SUPER_ADMIN_PASSWORD": admin_password,
        "VERIFY_ADMIN_USERNAME": "super_admin",
        "VERIFY_ADMIN_PASSWORD": admin_password,
        "WEB_CONCURRENCY": "2",
        "DWG_WORKER_CONCURRENCY": "1",
        "DXF2DWG_WORKER_CONCURRENCY": "1",
        "DXF_CLASSIFICATION_WORKER_CONCURRENCY": "1",
        # CI runners stay on the serial CLI path; production templates use
        # the validated two-process inner pool.
        "DXF_SPLIT_CLI_WORKER_CONCURRENCY": "1",
        "DXF_SPLIT_WORKER_MEMORY_LIMIT": "2g",
        "REMNANT_PARSE_WORKER_CONCURRENCY": "1",
        # GitHub-hosted runners currently expose 2 vCPUs. Production limits
        # may be higher, but Docker rejects any single container limit above
        # the host capacity before the isolated stack can start.
        "WORKER_CPU_LIMIT": "1.0",
        "NGINX_CPU_LIMIT": "1.0",
        "API_CPU_LIMIT": "1.0",
        "DISPATCHER_CPU_LIMIT": "0.5",
        "REPORT_WORKER_CPU_LIMIT": "1.0",
        "DXF_WORKER_CPU_LIMIT": "1.0",
        "DXF2DWG_WORKER_CPU_LIMIT": "1.0",
        "DXF2EXCEL_WORKER_CPU_LIMIT": "1.0",
        "DXF_CLASSIFICATION_WORKER_CPU_LIMIT": "1.0",
        "DXF_SPLIT_WORKER_CPU_LIMIT": "1.0",
        "REMNANT_CONVERT_WORKER_CPU_LIMIT": "1.0",
        "REMNANT_PARSE_WORKER_CPU_LIMIT": "1.0",
        "EXCEL_FINAL_WORKER_CPU_LIMIT": "1.0",
        "EXCEL_STAGE2_WORKER_CPU_LIMIT": "1.0",
        "EXCEL_STAGE3_WORKER_CPU_LIMIT": "1.0",
        "MAINTENANCE_WORKER_CPU_LIMIT": "1.0",
        "MYSQL_CPU_LIMIT": "1.0",
        "MINIO_CPU_LIMIT": "1.0",
        "API_MEMORY_LIMIT": "2g",
        "MYSQL_MEMORY_LIMIT": "2g",
        "MINIO_MEMORY_LIMIT": "1g",
        "DWG_AGENT_IMAGE": f"dwg-agent-backend:ci-{args.project}",
        "DWG_AGENT_FRONTEND_IMAGE": f"dwg-agent-frontend:ci-{args.project}",
    }
    content = _render(TEMPLATE.read_text(encoding="utf-8"), overrides)
    active = "\n".join(
        line for line in content.splitlines() if not line.lstrip().startswith("#")
    )
    if "CHANGE_ME_" in active:
        _fail("template contains an unresolved active placeholder")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        _fail("output already exists")
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)


if __name__ == "__main__":
    main()
