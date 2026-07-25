"""Isolated child-process entry point for the standalone Excel Final Stage."""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

_RESULT_PREFIX = "DWG_EXCEL_FINAL_RESULT="
_ERROR_PREFIX = "DWG_EXCEL_FINAL_ERROR="
_PROTOCOL_VERSION = 1
_REQUIRED_STAGE_FILES = (
    "main.py",
    "pipeline.py",
    "handbook.py",
    "config.py",
    "material_routing.py",
)
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

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--stage-root", required=True, type=Path)
    inspect_parser.add_argument("--input", required=True, type=Path)

    lookup_parser = subparsers.add_parser("lookup")
    lookup_parser.add_argument("--stage-root", required=True, type=Path)
    lookup_parser.add_argument("--category", required=True, choices=_LOOKUP_CATEGORIES)
    lookup_parser.add_argument("--spec", required=True)
    lookup_parser.add_argument("--material")
    return parser.parse_args()


def _configure_stage_imports(stage_root: Path) -> Path:
    root = stage_root.resolve()
    for filename in _REQUIRED_STAGE_FILES:
        if not (root / filename).is_file():
            raise RuntimeError(f"Excel Final Stage file is missing: {root / filename}")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def _configure_handbook_database() -> dict[str, Any]:
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
    _configure_handbook_database()
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


def _inspect(args: argparse.Namespace) -> None:
    from input_errors import INPUT_CONTRACT_VERSION
    from source_intake import read_production_source

    result = read_production_source(args.input.resolve())
    _emit_result(
        {
            "protocol_version": _PROTOCOL_VERSION,
            "operation": "inspect",
            "input_contract_version": INPUT_CONTRACT_VERSION,
            "source_format": result.source_format.value,
            "sheet_name": result.sheet_name,
            "header_row": int(result.diagnostics["header_row"]),
            "part_count": len(result.parts),
            "component_count": len(result.component_rows),
            "warnings": list(result.warnings),
            "ignored_sheets": list(result.ignored_sheets),
        }
    )


def _lookup(args: argparse.Namespace) -> None:
    from spec_parser import LookupPolicy, classify_normalized_spec
    from weights import CIRCULAR_HOLLOW_DENSITY_SOURCE, circular_hollow_linear_weight

    classification = classify_normalized_spec(args.spec, material=args.material or "")
    if classification.lookup_policy is LookupPolicy.CIRCULAR_HOLLOW_FORMULA:
        if classification.normalized_width is None:
            raise RuntimeError("PIP/PD formula classification has no wall thickness")
        weight = circular_hollow_linear_weight(
            Decimal(classification.normalized_spec),
            classification.normalized_width,
        )
        normalized_spec = args.spec
        source = CIRCULAR_HOLLOW_DENSITY_SOURCE
        status = "hit"
    else:
        database_config = _configure_handbook_database()
        from handbook import SteelHandbookDB

        handbook = SteelHandbookDB(database_config)
        try:
            result = handbook.lookup(args.category, args.spec, material=args.material)
        finally:
            handbook.close()
        weight = result.value_kg_per_m
        normalized_spec = result.normalized_spec
        source = result.source
        status = result.status.value
    _emit_result(
        {
            "protocol_version": _PROTOCOL_VERSION,
            "operation": "lookup",
            "category": args.category,
            "normalized_spec": normalized_spec,
            "material": args.material,
            "weight_kg_per_m": (
                float(weight)
                if weight is not None
                else None
            ),
            "source": source,
            "status": status,
        }
    )


def _emit_result(payload: dict[str, object]) -> None:
    print(
        _RESULT_PREFIX
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def _emit_error(operation: str, failure: dict[str, object]) -> None:
    print(
        _ERROR_PREFIX
        + json.dumps(
            {
                "protocol_version": _PROTOCOL_VERSION,
                "operation": operation,
                "failure": failure,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def main() -> None:
    args = _parse_args()
    _configure_stage_imports(args.stage_root)
    from input_errors import InputContractError

    try:
        if args.command == "process":
            _process(args)
        elif args.command == "inspect":
            _inspect(args)
        else:
            _lookup(args)
    except InputContractError as exc:
        _emit_error(args.command, exc.failure.as_dict())
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
