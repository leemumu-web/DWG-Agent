#!/usr/bin/env python3
"""Build and verify deterministic snapshots of public repository contracts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
SNAPSHOT_PATH = REPO_ROOT / "docs" / "architecture" / "runtime-contract.json"
HTTP_METHODS = {"delete", "get", "head", "options", "patch", "post", "put"}
ROUTE_PATH_RE = re.compile(r"<Route\b[^>]*\bpath=\"([^\"]+)\"")
COMPOSE_SERVICE_RE = re.compile(r"^  ([A-Za-z0-9][A-Za-z0-9_-]*):(?:\s*(?:#.*)?)?$")
REMNANT_WORKER_RE = re.compile(
    r"-Q\s+(remnant_(?:convert|parse)).*?--concurrency=\$\{[^:}]+:-(\d+)\}"
)

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _http_contract() -> tuple[list[str], list[str]]:
    from app.main import app

    schema = app.openapi()
    paths = sorted(schema["paths"])
    operations: list[str] = []
    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS:
                continue
            operation_id = operation.get("operationId")
            if not operation_id:
                raise RuntimeError(f"OpenAPI operation has no operationId: {method} {path}")
            operations.append(f"{method.upper()} {path} {operation_id}")
    return paths, sorted(operations)


def _orm_tables() -> list[str]:
    from app.bootstrap.model_registry import load_models
    from app.platform.database.base import Base

    load_models()
    return sorted(Base.metadata.tables)


def _celery_tasks() -> list[str]:
    from app.platform.messaging.celery_app import celery_app

    celery_app.loader.import_default_modules()
    return sorted(name for name in celery_app.tasks if name.startswith("app.workers."))


def _celery_task_routes() -> list[str]:
    from app.platform.messaging.celery_app import celery_app

    routes = celery_app.conf.task_routes or {}
    return sorted(f"{pattern} -> {route['queue']}" for pattern, route in routes.items())


def _frontend_routes() -> list[str]:
    router_path = REPO_ROOT / "frontend" / "src" / "app" / "router.tsx"
    source = router_path.read_text(encoding="utf-8")
    declared = ROUTE_PATH_RE.findall(source)
    absolute = {path for path in declared if path.startswith("/") or path == "*"}
    relative = {path for path in declared if not path.startswith("/") and path != "*"}

    # The current router has one nested public URL family. Treat an unexpected
    # second relative family as a contract-parser change, not as a silent guess.
    if relative and "/files" not in absolute:
        raise RuntimeError("relative frontend routes exist without the /files parent")
    resolved = absolute | {f"/files/{path}" for path in relative}
    return sorted(resolved)


def _compose_services() -> list[str]:
    compose_path = REPO_ROOT / "compose.yaml"
    lines = compose_path.read_text(encoding="utf-8").splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line == "services:") + 1
    except StopIteration as exc:
        raise RuntimeError("compose.yaml has no top-level services section") from exc

    services: list[str] = []
    for line in lines[start:]:
        if line and not line.startswith((" ", "\t", "#")):
            break
        match = COMPOSE_SERVICE_RE.match(line)
        if match:
            services.append(match.group(1))
    if not services:
        raise RuntimeError("compose.yaml services section is empty or unparsable")
    return sorted(services)


def _worker_queue_concurrency() -> dict[str, int]:
    source = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")
    values = {queue: int(value) for queue, value in REMNANT_WORKER_RE.findall(source)}
    expected = {"remnant_convert", "remnant_parse"}
    if values.keys() != expected:
        raise RuntimeError(f"missing remnant worker concurrency contract: {expected - values.keys()}")
    return dict(sorted(values.items()))


def _alembic_heads() -> list[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    return sorted(ScriptDirectory.from_config(config).get_heads())


def build_contract_snapshot() -> dict[str, Any]:
    """Return all compatibility surfaces in a deterministic JSON-safe shape."""
    http_paths, http_operations = _http_contract()
    return {
        "alembic_heads": _alembic_heads(),
        "celery_task_routes": _celery_task_routes(),
        "celery_tasks": _celery_tasks(),
        "compose_services": _compose_services(),
        "frontend_routes": _frontend_routes(),
        "http_operations": http_operations,
        "http_paths": http_paths,
        "orm_tables": _orm_tables(),
        "worker_queue_concurrency": _worker_queue_concurrency(),
    }


def render_snapshot(snapshot: dict[str, Any] | None = None) -> str:
    return json.dumps(snapshot or build_contract_snapshot(), ensure_ascii=False, indent=2) + "\n"


def check_snapshot(path: Path = SNAPSHOT_PATH) -> list[str]:
    if not path.is_file():
        return [f"missing runtime contract snapshot: {path.relative_to(REPO_ROOT)}"]
    expected = render_snapshot()
    actual = path.read_text(encoding="utf-8")
    if actual != expected:
        return [
            "runtime contract snapshot is stale; inspect the compatibility change, then run "
            "backend/.venv/bin/python scripts/architecture/snapshot_contracts.py --write"
        ]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="replace the committed snapshot")
    mode.add_argument("--check", action="store_true", help="compare runtime with the snapshot")
    args = parser.parse_args()

    if args.write:
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(render_snapshot(), encoding="utf-8")
        print(f"Wrote {SNAPSHOT_PATH.relative_to(REPO_ROOT)}")
        return 0
    if args.check:
        errors = check_snapshot()
        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 1
        print("Runtime contract snapshot matches HTTP, ORM, Celery, frontend and Compose surfaces.")
        return 0
    print(render_snapshot(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
