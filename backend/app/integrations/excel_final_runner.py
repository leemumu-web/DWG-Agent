from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_RESULT_PREFIX = "DWG_EXCEL_FINAL_RESULT="


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Isolated Excel Final Stage runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    process_parser = subparsers.add_parser("process")
    process_parser.add_argument("--stage-root", required=True, type=Path)
    process_parser.add_argument("--format", required=True, choices=("init", "tsv"))
    process_parser.add_argument("--input", required=True, type=Path)
    process_parser.add_argument("--output", required=True, type=Path)

    lookup_parser = subparsers.add_parser("lookup")
    lookup_parser.add_argument("--stage-root", required=True, type=Path)
    lookup_parser.add_argument("--spec", required=True)
    return parser.parse_args()


def _configure_stage(stage_root: Path) -> dict[str, Any]:
    root = stage_root.resolve()
    for filename in ("pipeline.py", "handbook.py", "config.py"):
        if not (root / filename).is_file():
            raise RuntimeError(f"Excel Final Stage file is missing: {root / filename}")
    sys.path.insert(0, str(root))

    import config as stage_config

    database_config: dict[str, Any] = {
        "host": os.environ["DWG_HANDBOOK_MYSQL_HOST"],
        "port": int(os.environ["DWG_HANDBOOK_MYSQL_PORT"]),
        "database": os.environ["DWG_HANDBOOK_MYSQL_DATABASE"],
        "user": os.environ["DWG_HANDBOOK_MYSQL_USER"],
        "password": os.environ["DWG_HANDBOOK_MYSQL_PASSWORD"],
        "charset": "utf8mb4",
        "connect_timeout": 5,
    }
    stage_config.DB_CONFIG.clear()
    stage_config.DB_CONFIG.update(database_config)
    return database_config


def _process(args: argparse.Namespace) -> None:
    _configure_stage(args.stage_root)
    from pipeline import run_init_pipeline, run_pipeline

    if args.format == "init":
        result = run_init_pipeline(args.input.resolve(), args.output.resolve())
    else:
        result = run_pipeline(args.input.resolve(), args.output.resolve())
    if not Path(result).is_file():
        raise RuntimeError(f"Excel Final Stage did not create its output: {result}")


def _lookup(args: argparse.Namespace) -> None:
    database_config = _configure_stage(args.stage_root)
    from handbook import SteelHandbookDB

    handbook = SteelHandbookDB(database_config, max_retries=1)
    try:
        weight, source = handbook.lookup(args.spec)
    finally:
        handbook.close()
    print(
        _RESULT_PREFIX
        + json.dumps(
            {"weight_kg_per_m": weight, "source": source},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def main() -> None:
    args = _parse_args()
    if args.command == "process":
        _process(args)
    else:
        _lookup(args)


if __name__ == "__main__":
    main()
