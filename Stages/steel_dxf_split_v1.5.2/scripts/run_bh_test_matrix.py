#!/usr/bin/env python3
"""Run BH regression modules in isolated workers."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "pytest_worker.py"
MODULES = [
    ROOT / "tests" / "test_bh_supervised_pairs.py",
    ROOT / "tests" / "test_bh_supervised_pairs_v06.py",
    ROOT / "tests" / "test_bh_supervised_pairs_v07.py",
    ROOT / "tests" / "test_bh_compiler_v08.py",
    ROOT / "tests" / "test_bh_supervised_pairs_v09.py",
    ROOT / "tests" / "test_bh_semantic_solver_v10.py",
    ROOT / "tests" / "test_bh_semantic_core_v10.py",
    ROOT / "tests" / "test_bh_semantic_contract_v11.py",
]


def main() -> int:
    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    total = 0
    for index, module in enumerate(MODULES, start=1):
        print(f"\n=== BH [{index}/{len(MODULES)}] {module.name} ===", flush=True)
        result = subprocess.run(
            [sys.executable, str(WORKER), str(module)],
            cwd=ROOT,
            env=env,
            check=False,
            close_fds=True,
        )
        if result.returncode != 0:
            return result.returncode
        total += 1
    print(f"\nAll {total} BH regression modules passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
