#!/usr/bin/env python3
"""Render the source Compose model into a complete offline server stack."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--mysql-image", required=True)
    parser.add_argument("--minio-image", required=True)
    return parser.parse_args()


def render(
    source: Path,
    output: Path,
    *,
    backend_image: str,
    frontend_image: str,
    mysql_image: str,
    minio_image: str,
) -> None:
    payload: dict[str, Any] = yaml.safe_load(source.read_text(encoding="utf-8"))
    services: dict[str, dict[str, Any]] = payload["services"]

    for name, service in services.items():
        service.pop("build", None)
        service.pop("profiles", None)
        service["pull_policy"] = "never"
        if name == "nginx":
            service["image"] = frontend_image
        elif name == "mysql":
            service["image"] = mysql_image
        elif name == "minio":
            service["image"] = minio_image
        else:
            service["image"] = backend_image

    server_payload = {
        "name": "dwg-agent",
        "services": services,
        "volumes": payload.get("volumes", {}),
        "networks": payload.get("networks", {}),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        yaml.safe_dump(
            server_payload,
            allow_unicode=True,
            sort_keys=False,
            width=1000,
        ),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    render(
        args.source,
        args.output,
        backend_image=args.backend_image,
        frontend_image=args.frontend_image,
        mysql_image=args.mysql_image,
        minio_image=args.minio_image,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
