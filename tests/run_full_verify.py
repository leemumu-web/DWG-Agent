#!/usr/bin/env python3
"""Non-destructive smoke verification for a running DWG-Agent stack.

The verifier intentionally performs no reset, upload, job creation, or mutation.
Credentials are read from the environment and are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 10,
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read()
            try:
                parsed = json.loads(body) if body else {}
            except json.JSONDecodeError:
                parsed = {
                    "_invalid_json": True,
                    "_content_type": response.headers.get_content_type(),
                }
            return response.status, parsed
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return exc.code, json.loads(body) if body else {}


def envelope_status(body: dict[str, Any]) -> str | None:
    data = body.get("data")
    return data.get("status") if isinstance(data, dict) else None


def run(base_url: str, username: str | None, password: str | None) -> list[Check]:
    checks: list[Check] = []

    status, body = request_json(base_url, "GET", "/health")
    checks.append(Check("liveness", status == 200 and envelope_status(body) == "ok", f"HTTP {status}"))

    status, body = request_json(base_url, "GET", "/health/ready")
    ready = body.get("data", {}) if isinstance(body.get("data"), dict) else {}
    checks.append(
        Check(
            "readiness",
            status == 200 and ready.get("status") == "ok",
            f"HTTP {status}; database={ready.get('database', {}).get('status', 'unknown')}; "
            f"storage={ready.get('storage', {}).get('status', 'unknown')}",
        )
    )

    status, body = request_json(base_url, "GET", "/openapi.json")
    paths = body.get("paths", {}) if isinstance(body, dict) else {}
    checks.append(Check("openapi", status == 200 and len(paths) > 0, f"HTTP {status}; paths={len(paths)}"))

    if bool(username) != bool(password):
        checks.append(Check("authentication", False, "set both DWG_VERIFY_USERNAME and DWG_VERIFY_PASSWORD"))
        return checks
    if username and password:
        status, body = request_json(
            base_url,
            "POST",
            "/api/v1/auth/sessions",
            payload={"username": username, "password": password},
        )
        data = body.get("data", {}) if isinstance(body.get("data"), dict) else {}
        token = data.get("access_token")
        checks.append(Check("authentication", status == 201 and bool(token), f"HTTP {status}"))
        if token:
            for name, path in (("files-list", "/api/v1/files?page=1&page_size=1"), ("jobs-list", "/api/v1/jobs?page=1&page_size=1")):
                list_status, list_body = request_json(base_url, "GET", path, token=token)
                list_data = list_body.get("data")
                has_page = (
                    isinstance(list_data, list)
                    and isinstance(list_body.get("meta"), dict)
                    and isinstance(list_body.get("pagination"), dict)
                )
                checks.append(Check(name, list_status == 200 and has_page, f"HTTP {list_status}"))

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    status_script = os.path.join(project_root, "scripts", "status.sh")
    proc = subprocess.run(
        ["bash", status_script], cwd=project_root, capture_output=True, text=True, timeout=30
    )
    checks.append(Check("process-topology", proc.returncode == 0, f"status.sh exit={proc.returncode}"))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("DWG_VERIFY_BASE_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    checks = run(
        args.base_url,
        os.getenv("DWG_VERIFY_USERNAME"),
        os.getenv("DWG_VERIFY_PASSWORD"),
    )
    if args.json:
        print(json.dumps({"ok": all(item.ok for item in checks), "checks": [asdict(item) for item in checks]}, indent=2))
    else:
        for item in checks:
            print(f"{'PASS' if item.ok else 'FAIL'}  {item.name}: {item.detail}")
    return 0 if all(item.ok for item in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
