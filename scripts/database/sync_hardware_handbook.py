#!/usr/bin/env python3
"""Generate the source-exact hardware-handbook database SQL."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.modules.excel_processing.handbook_catalog_source import (  # noqa: E402
    load_handbook_workbook,
    render_database_sql,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从唯一可信五金手册.xls生成一一对应的MySQL初始化SQL",
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--output-sql", required=True, type=Path)
    parser.add_argument("--database-name", default="hardware_handbook")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    snapshot = load_handbook_workbook(args.source)
    sql = render_database_sql(snapshot, database_name=args.database_name)
    output = args.output_sql.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        prefix=f".{output.name}.",
        suffix=".tmp",
        dir=output.parent,
        delete=False,
    ) as handle:
        handle.write(sql)
        temporary = Path(handle.name)
    temporary.replace(output)
    summary = {
        "database_name": args.database_name,
        "lookup_conflict_count": sum(
            len(conflicts) for conflicts in snapshot.lookup_conflicts.values()
        ),
        "output_sql": str(output),
        "semantic_record_count": snapshot.semantic_record_count,
        "source_row_count": len(snapshot.source_rows),
        "source_sha256": snapshot.sha256,
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
