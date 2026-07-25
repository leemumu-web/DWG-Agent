#!/usr/bin/env python3
"""Run selected pytest nodes in fresh processes with resumable state.

Large DXF/Shapely regression tests are isolated one node per interpreter.  The
state file is updated after every node, so CI or constrained runners may resume
without rerunning completed manufacturing cases.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "pytest_worker.py"
DEFAULT_MODULES = [
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


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("modules", nargs="*", default=DEFAULT_MODULES)
    p.add_argument("--state", type=Path, default=ROOT / "output" / "v1.1_atomic_test_matrix.json")
    p.add_argument("--max-nodes", type=int, default=0, help="0 means all pending nodes")
    p.add_argument("--node-timeout", type=int, default=240)
    p.add_argument("--reset", action="store_true")
    return p


def collect_nodes(modules: list[str], env: dict[str, str]) -> list[str]:
    """Collect exact node ids through a pytest hook, not human-formatted output."""
    collector = ROOT / "scripts" / "collect_pytest_nodes.py"
    completed = subprocess.run(
        [sys.executable, str(collector), *modules],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stdout + completed.stderr)
    sentinel = "__PYTEST_NODE_IDS__="
    payload = next(
        (line[len(sentinel):] for line in completed.stdout.splitlines() if line.startswith(sentinel)),
        None,
    )
    if payload is None:
        raise RuntimeError("pytest collector emitted no node-id payload:\n" + completed.stdout)
    nodes = json.loads(payload)
    if not nodes or len(nodes) != len(set(nodes)):
        raise RuntimeError("pytest collector returned empty or duplicate node ids")
    return nodes


def load_state(path: Path, modules: list[str], nodes: list[str], reset: bool) -> dict[str, Any]:
    if path.exists() and not reset:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("nodes") == nodes:
            return state
    return {
        "schema": "PYTEST-ATOMIC-MATRIX-1.1",
        "version": "1.1.0",
        "execution_model": "one fresh Python process per pytest node",
        "modules": modules,
        "nodes": nodes,
        "results": [],
    }


def summarize(state: dict[str, Any]) -> None:
    results = state["results"]
    state.update({
        "collected": len(state["nodes"]),
        "executed": len(results),
        "passed": sum(x["status"] == "passed" for x in results),
        "failed": sum(x["status"] == "failed" for x in results),
        "timeouts": sum(x["status"] == "timeout" for x in results),
        "all_passed": len(results) == len(state["nodes"]) and all(x["status"] == "passed" for x in results),
    })


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONPATH"] = str(ROOT / "src")
    modules = [str(Path(m)) for m in args.modules]
    nodes = collect_nodes(modules, env)
    state = load_state(args.state, modules, nodes, args.reset)
    completed_nodes = {x["node"] for x in state["results"] if x["status"] == "passed"}
    pending = [node for node in nodes if node not in completed_nodes]
    if args.max_nodes > 0:
        pending = pending[: args.max_nodes]
    args.state.parent.mkdir(parents=True, exist_ok=True)
    log_dir = args.state.parent / "atomic_test_logs_v11"
    log_dir.mkdir(parents=True, exist_ok=True)

    for node in pending:
        print(f"[{len(state['results']) + 1:03d}/{len(nodes):03d}] {node}", flush=True)
        started = time.perf_counter()
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", node)[-180:]
        log = log_dir / f"{len(state['results']) + 1:03d}_{safe}.log"
        try:
            run = subprocess.run(
                [sys.executable, str(WORKER), node],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=args.node_timeout,
                check=False,
                close_fds=True,
            )
            code = run.returncode
            output = run.stdout + run.stderr
            status = "passed" if code == 0 else "failed"
        except subprocess.TimeoutExpired as exc:
            code = 124
            output = (exc.stdout or "") + (exc.stderr or "")
            status = "timeout"
        log.write_text(output, encoding="utf-8")
        state["results"].append({
            "node": node,
            "status": status,
            "returncode": code,
            "duration_seconds": time.perf_counter() - started,
            "log": str(log.resolve().relative_to(ROOT.resolve())),
        })
        summarize(state)
        args.state.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        if code != 0:
            print(output, file=sys.stderr)
            return code

    summarize(state)
    args.state.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: state[k] for k in ("collected", "executed", "passed", "failed", "timeouts", "all_passed")}, ensure_ascii=False))
    return 0 if not state["failed"] and not state["timeouts"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
