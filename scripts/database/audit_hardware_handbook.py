#!/usr/bin/env python3
"""Verify that the deployed handbook database exactly matches the Excel authority."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pymysql

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.modules.excel_processing.handbook_catalog_source import (  # noqa: E402
    audit_database_connection,
    load_handbook_workbook,
)
from app.platform.config.settings import settings  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="逐表逐行核验线上五金手册库与唯一可信五金手册.xls",
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--database-name")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    snapshot = load_handbook_workbook(args.source)
    config = dict(settings.handbook_database_config)
    if args.database_name:
        config["database"] = args.database_name
    connection = pymysql.connect(**config)
    try:
        problems = audit_database_connection(snapshot, connection)
    finally:
        connection.close()
    summary = {
        "database_name": config["database"],
        "problem_count": len(problems),
        "problems": list(problems),
        "semantic_record_count": snapshot.semantic_record_count,
        "source_row_count": len(snapshot.source_rows),
        "source_sha256": snapshot.sha256,
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
