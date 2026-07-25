#!/usr/bin/env python3
"""Run the complete regression matrix in isolated pytest workers."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "pytest_worker.py"
MODULES = [
    ROOT / "tests" / "test_box_split_v04.py",
    ROOT / "tests" / "test_bh_supervised_pairs.py",
    ROOT / "tests" / "test_bh_supervised_pairs_v06.py",
    ROOT / "tests" / "test_bh_supervised_pairs_v07.py",
    ROOT / "tests" / "test_bh_compiler_v08.py",
]


def main() -> int:
    total = 0
    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    for index, module in enumerate(MODULES, start=1):
        print(f"\n=== [{index}/{len(MODULES)}] {module.name} ===", flush=True)
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
    print(f"\nAll {total} regression modules passed in isolated workers.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
