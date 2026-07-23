"""Isolated child-process entry point for the standalone Excel Final Stage."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_RESULT_PREFIX = "DWG_EXCEL_FINAL_RESULT="
_PROTOCOL_VERSION = 1
_LOOKUP_CATEGORIES = (
    "flat_steel",
    "round_bar",
    "rebar",
    "square_bar",
    "i_beam",
    "h_beam",
    "t_beam",
    "channel",
    "angle",
    "steel_pipe",
    "square_tube",
    "hfw_pipe",
    "w_beam",
    "plate",
    "skip",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Isolated Excel Final Stage runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    process_parser = subparsers.add_parser("process")
    process_parser.add_argument("--stage-root", required=True, type=Path)
    process_parser.add_argument("--input", required=True, type=Path)
    process_parser.add_argument("--output", required=True, type=Path)
    process_parser.add_argument("--internal-output", required=True, type=Path)

    lookup_parser = subparsers.add_parser("lookup")
    lookup_parser.add_argument("--stage-root", required=True, type=Path)
    lookup_parser.add_argument("--category", required=True, choices=_LOOKUP_CATEGORIES)
    lookup_parser.add_argument("--spec", required=True)
    lookup_parser.add_argument("--material")
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
    from pipeline import run_auto_pipeline

    result = run_auto_pipeline(
        args.input.resolve(),
        args.output.resolve(),
        internal_output_file=args.internal_output.resolve(),
    )
    if not Path(result).is_file():
        raise RuntimeError(f"Excel Final Stage did not create its output: {result}")
    summary = dict(result.report_summary)
    summary["category_counts"] = dict(summary.get("category_counts", {}))
    summary["representative_messages"] = list(
        summary.get("representative_messages", [])
    )
    _emit_result(
        {
            "protocol_version": _PROTOCOL_VERSION,
            "operation": "process",
            "output_path": str(result.output_path),
            "quality_status": result.quality_status,
            "warning_count": result.warning_count,
            "severe_warning_count": result.severe_warning_count,
            "report_summary": summary,
        }
    )


def _lookup(args: argparse.Namespace) -> None:
    database_config = _configure_stage(args.stage_root)
    from handbook import SteelHandbookDB

    handbook = SteelHandbookDB(database_config)
    try:
        result = handbook.lookup(args.category, args.spec, material=args.material)
    finally:
        handbook.close()
    _emit_result(
        {
            "protocol_version": _PROTOCOL_VERSION,
            "operation": "lookup",
            "category": result.category,
            "normalized_spec": result.normalized_spec,
            "material": args.material,
            "weight_kg_per_m": (
                float(result.value_kg_per_m)
                if result.value_kg_per_m is not None
                else None
            ),
            "source": result.source,
            "status": result.status.value,
        }
    )


def _emit_result(payload: dict[str, object]) -> None:
    print(
        _RESULT_PREFIX
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def main() -> None:
    args = _parse_args()
    if args.command == "process":
        _process(args)
    else:
        _lookup(args)


if __name__ == "__main__":
    main()
