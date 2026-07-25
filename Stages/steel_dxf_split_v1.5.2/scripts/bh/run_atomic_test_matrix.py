#!/usr/bin/env python3
"""Run the current test inventory in isolated, resumable pytest workers.

Large ezdxf/Shapely graphs may retain native resources until process exit.
This runner gives every node a clean interpreter, kills its process group on
hard timeout, and checkpoints after each node so interrupted regressions resume
without repeating successful cases.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from steel_dxf_split import __version__
from steel_dxf_split.process_control import run_isolated_process

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "scripts" / "bh" / "pytest_worker.py"
DEFAULT_TEST_MODULES = sorted(
    str(path.relative_to(ROOT)) for path in (ROOT / "tests").glob("test_*.py")
)
REPORT_SCHEMA = "BH-ATOMIC-TEST-MATRIX-1.3"
FINGERPRINT_DIRECTORIES = (
    "docs",
    "samples/bh_pairs",
    "samples/bh_pairs",
    "scripts",
    "src",
    "tests",
)
FINGERPRINT_FILES = (
    ".gitattributes",
    ".gitignore",
    "CONTEXT.md",
    "README.md",
    "VERSION",
    "pyproject.toml",
    "uv.lock",
)


def build_test_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT), str(ROOT / "src")))
    return env


def collect_nodes(env: dict[str, str], modules: list[str]) -> list[str]:
    """Collect exact node ids through a pytest hook, not human-formatted output."""
    collector = ROOT / "scripts" / "bh" / "collect_pytest_nodes.py"
    result = subprocess.run(
        [sys.executable, str(collector), *modules],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stdout + result.stderr)
    sentinel = "__PYTEST_NODE_IDS__="
    payload = next(
        (line[len(sentinel):] for line in result.stdout.splitlines() if line.startswith(sentinel)),
        None,
    )
    if payload is None:
        raise RuntimeError("pytest collector emitted no node-id payload:\n" + result.stdout)
    nodes = json.loads(payload)
    if not nodes or len(nodes) != len(set(nodes)):
        raise RuntimeError("pytest collector returned empty or duplicate node ids")
    return nodes


def run_node(
    node: str,
    env: dict[str, str],
    timeout_seconds: int,
) -> tuple[int, str, str, float, float, float]:
    result = run_isolated_process(
        [sys.executable, str(WORKER), node],
        timeout_seconds,
        cwd=ROOT,
        env=env,
    )
    status = (
        "timeout"
        if result.timed_out
        else "passed"
        if result.returncode == 0
        else "failed"
    )
    active_seconds = (
        result.duration_seconds
        if result.active_supervision_seconds is None
        else result.active_supervision_seconds
    )
    return (
        result.returncode,
        status,
        result.output,
        result.duration_seconds,
        active_seconds,
        result.unbudgeted_wall_seconds,
    )


def workspace_fingerprint(root: Path = ROOT) -> str:
    """Bind resumable passes to the exact platform and release input tree."""

    digest = hashlib.sha256()
    runtime = (os.name, sys.platform, sys.version, __version__)
    digest.update(json.dumps(runtime, ensure_ascii=True).encode("utf-8"))
    candidates = [root / name for name in FINGERPRINT_FILES]
    for directory in FINGERPRINT_DIRECTORIES:
        base = root / directory
        if base.is_dir():
            candidates.extend(path for path in base.rglob("*") if path.is_file())
    for path in sorted(
        {
            candidate.resolve()
            for candidate in candidates
            if candidate.is_file()
            and "__pycache__" not in candidate.parts
            and candidate.suffix not in {".pyc", ".pyo"}
        },
        key=lambda item: item.relative_to(root.resolve()).as_posix(),
    ):
        relative = path.relative_to(root.resolve()).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_resumable_results(
    path: Path,
    nodes: list[str],
    *,
    workspace_fingerprint: str,
) -> dict[str, dict[str, object]]:
    """Reuse only passing nodes proven to belong to this exact workspace."""

    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if (
        previous.get("schema") != REPORT_SCHEMA
        or previous.get("version") != __version__
        or previous.get("workspace_fingerprint") != workspace_fingerprint
    ):
        return {}
    expected_nodes = set(nodes)
    results = previous.get("results")
    if not isinstance(results, list):
        return {}
    return {
        str(item["node"]): item
        for item in results
        if isinstance(item, dict)
        and item.get("status") == "passed"
        and item.get("node") in expected_nodes
    }


def write_report(
    path: Path,
    nodes: list[str],
    results_by_node: dict[str, dict[str, object]],
    *,
    workspace_fingerprint: str,
) -> dict[str, object]:
    results = [results_by_node[node] for node in nodes if node in results_by_node]
    report = {
        "version": __version__,
        "schema": REPORT_SCHEMA,
        "workspace_fingerprint": workspace_fingerprint,
        "execution_model": (
            "one fresh process per pytest node with platform process-tree timeout"
        ),
        "collected": len(nodes),
        "executed": len(results),
        "passed": sum(item["status"] == "passed" for item in results),
        "failed": sum(item["status"] == "failed" for item in results),
        "timeouts": sum(item["status"] == "timeout" for item in results),
        "complete": len(results) == len(nodes),
        "all_passed": len(results) == len(nodes) and all(item["status"] == "passed" for item in results),
        "results": results,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("modules", nargs="*", default=DEFAULT_TEST_MODULES)
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--max-nodes", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--continue-on-failure", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "output" / "atomic_test_matrix.json",
    )
    args = parser.parse_args()

    env = build_test_environment()
    nodes = collect_nodes(env, list(args.modules))
    fingerprint = workspace_fingerprint()
    log_dir = ROOT / "output" / "atomic_test_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    results_by_node: dict[str, dict[str, object]] = {}
    if args.resume and args.report.exists():
        results_by_node = load_resumable_results(
            args.report,
            nodes,
            workspace_fingerprint=fingerprint,
        )

    pending = [node for node in nodes if node not in results_by_node]
    if args.max_nodes is not None:
        pending = pending[: args.max_nodes]

    for index, node in enumerate(pending, start=1):
        absolute_index = nodes.index(node) + 1
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", node)[-180:]
        log_path = log_dir / f"{absolute_index:03d}_{safe}.log"
        print(f"[{absolute_index:03d}/{len(nodes):03d}] {node}", flush=True)
        (
            returncode,
            status,
            output,
            duration,
            active_supervision_seconds,
            unbudgeted_wall_seconds,
        ) = run_node(node, env, args.timeout_seconds)
        log_path.write_text(output, encoding="utf-8")
        results_by_node[node] = {
            "node": node,
            "status": status,
            "returncode": returncode,
            "duration_seconds": duration,
            "active_supervision_seconds": active_supervision_seconds,
            "unbudgeted_wall_seconds": unbudgeted_wall_seconds,
            "log": str(log_path.relative_to(ROOT)),
        }
        report = write_report(
            args.report,
            nodes,
            results_by_node,
            workspace_fingerprint=fingerprint,
        )
        if returncode != 0 and not args.continue_on_failure:
            print(json.dumps({key: report[key] for key in ("collected", "executed", "passed", "failed", "timeouts", "complete", "all_passed")}, ensure_ascii=False))
            return returncode

    report = write_report(
        args.report,
        nodes,
        results_by_node,
        workspace_fingerprint=fingerprint,
    )
    print(json.dumps({key: report[key] for key in ("collected", "executed", "passed", "failed", "timeouts", "complete", "all_passed")}, ensure_ascii=False))
    # A deliberately partial --max-nodes run is successful if all executed
    # nodes passed; a full run additionally requires completeness.
    if args.max_nodes is not None and not report["complete"]:
        return 0 if report["failed"] == 0 and report["timeouts"] == 0 else 1
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
