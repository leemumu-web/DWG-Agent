#!/usr/bin/env python3
"""Run every pytest node in a fresh process with resumable progress.

Large ezdxf/Shapely graphs may retain native resources until process exit.
This runner gives every node a clean interpreter, kills its process group on
hard timeout, and checkpoints after each node so interrupted regressions resume
without repeating successful cases.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "pytest_worker.py"
DEFAULT_TEST_MODULES = [
    "tests/test_bh_supervised_pairs.py",
    "tests/test_bh_supervised_pairs_v06.py",
    "tests/test_bh_supervised_pairs_v07.py",
    "tests/test_bh_compiler_v08.py",
    "tests/test_bh_supervised_pairs_v09.py",
    "tests/test_bh_semantic_solver_v10.py",
    "tests/test_bh_semantic_core_v10.py",
    "tests/test_bh_semantic_contract_v11.py",
    "tests/test_bh_risk_analysis_v11.py",
    "tests/test_box_split_v04.py",
]


def collect_nodes(env: dict[str, str], modules: list[str]) -> list[str]:
    """Collect exact node ids through a pytest hook, not human-formatted output."""
    collector = ROOT / "scripts" / "collect_pytest_nodes.py"
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


def run_node(node: str, env: dict[str, str], timeout_seconds: int) -> tuple[int, str, str, float]:
    started = time.perf_counter()
    process = subprocess.Popen(
        [sys.executable, str(WORKER), node],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=timeout_seconds)
        returncode = int(process.returncode)
        status = "passed" if returncode == 0 else "failed"
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate()
        output += f"\nTIMEOUT after {timeout_seconds} seconds\n"
        returncode = 124
        status = "timeout"
    return returncode, status, output, time.perf_counter() - started


def write_report(path: Path, nodes: list[str], results_by_node: dict[str, dict[str, object]]) -> dict[str, object]:
    results = [results_by_node[node] for node in nodes if node in results_by_node]
    report = {
        "version": "1.1.0",
        "schema": "BH-ATOMIC-TEST-MATRIX-1.1",
        "execution_model": "one fresh process per pytest node with process-group timeout",
        "collected": len(nodes),
        "executed": len(results),
        "passed": sum(item["status"] == "passed" for item in results),
        "failed": sum(item["status"] == "failed" for item in results),
        "timeouts": sum(item["status"] == "timeout" for item in results),
        "complete": len(results) == len(nodes),
        "all_passed": len(results) == len(nodes) and all(item["status"] == "passed" for item in results),
        "results": results,
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
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
        default=ROOT / "output" / "v1.1_atomic_test_matrix.json",
    )
    args = parser.parse_args()

    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONPATH"] = str(ROOT / "src")
    nodes = collect_nodes(env, list(args.modules))
    log_dir = ROOT / "output" / "atomic_test_logs_v11"
    log_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    results_by_node: dict[str, dict[str, object]] = {}
    if args.resume and args.report.exists():
        previous = json.loads(args.report.read_text(encoding="utf-8"))
        results_by_node = {
            item["node"]: item
            for item in previous.get("results", [])
            if item.get("status") == "passed"
        }

    pending = [node for node in nodes if node not in results_by_node]
    if args.max_nodes is not None:
        pending = pending[: args.max_nodes]

    for index, node in enumerate(pending, start=1):
        absolute_index = nodes.index(node) + 1
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", node)[-180:]
        log_path = log_dir / f"{absolute_index:03d}_{safe}.log"
        print(f"[{absolute_index:03d}/{len(nodes):03d}] {node}", flush=True)
        returncode, status, output, duration = run_node(node, env, args.timeout_seconds)
        log_path.write_text(output, encoding="utf-8")
        results_by_node[node] = {
            "node": node,
            "status": status,
            "returncode": returncode,
            "duration_seconds": duration,
            "log": str(log_path.relative_to(ROOT)),
        }
        report = write_report(args.report, nodes, results_by_node)
        if returncode != 0 and not args.continue_on_failure:
            print(json.dumps({key: report[key] for key in ("collected", "executed", "passed", "failed", "timeouts", "complete", "all_passed")}, ensure_ascii=False))
            return returncode

    report = write_report(args.report, nodes, results_by_node)
    print(json.dumps({key: report[key] for key in ("collected", "executed", "passed", "failed", "timeouts", "complete", "all_passed")}, ensure_ascii=False))
    # A deliberately partial --max-nodes run is successful if all executed
    # nodes passed; a full run additionally requires completeness.
    if args.max_nodes is not None and not report["complete"]:
        return 0 if report["failed"] == 0 and report["timeouts"] == 0 else 1
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
