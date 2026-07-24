"""Minimal runtime configuration owned by the standalone Excel Final Stage."""

from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "data/output"


def _read_db_config() -> dict[str, object]:
    """Read handbook database config from env vars (standalone) or platform injection."""
    host = os.environ.get("DWG_HANDBOOK_MYSQL_HOST")
    if host:
        return {
            "host": host,
            "port": int(os.environ.get("DWG_HANDBOOK_MYSQL_PORT", "3306")),
            "database": os.environ.get(
                "DWG_HANDBOOK_MYSQL_DATABASE",
                "hardware_handbook",
            ),
            "user": os.environ.get("DWG_HANDBOOK_MYSQL_USER", "dwg_user"),
            "password": os.environ.get("DWG_HANDBOOK_MYSQL_PASSWORD", ""),
            "charset": "utf8mb4",
            "connect_timeout": 5,
        }
    return {}


# The platform injects the read-only connection at the isolated process boundary.
DB_CONFIG: dict[str, object] = _read_db_config()
