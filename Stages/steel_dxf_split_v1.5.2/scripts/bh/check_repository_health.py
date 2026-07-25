#!/usr/bin/env python3
"""Run the complete local v1.5 repository health gate in a fixed order."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from typing import TextIO


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class HealthCommand:
    name: str
    argv: tuple[str, ...]
    cwd: Path


def health_commands(root: Path, python: Path) -> tuple[HealthCommand, ...]:
    project_root = Path(root)
    interpreter = str(python)
    return (
        HealthCommand("锁文件一致性", ("uv", "lock", "--check"), project_root),
        HealthCommand("依赖一致性", ("uv", "pip", "check"), project_root),
        HealthCommand(
            "强制字节码编译",
            (
                interpreter,
                "-m",
                "compileall",
                "-q",
                "-f",
                "src",
                "tests",
                "scripts",
            ),
            project_root,
        ),
        HealthCommand(
            "Ruff 静态检查",
            ("uv", "run", "ruff", "check", "src", "tests", "scripts"),
            project_root,
        ),
        HealthCommand(
            "warning-as-error 完整测试",
            (interpreter, "-m", "pytest", "-W", "error", "-q"),
            project_root,
        ),
        HealthCommand(
            "Git 补丁格式",
            ("git", "diff", "--check"),
            project_root,
        ),
        HealthCommand(
            "Git 暂存补丁格式",
            ("git", "diff", "--cached", "--check"),
            project_root,
        ),
    )


def run_health_checks(
    root: Path,
    python: Path,
    *,
    stream: TextIO | None = None,
) -> int:
    destination = sys.stderr if stream is None else stream
    commands = health_commands(root, python)
    for index, command in enumerate(commands, start=1):
        print(
            f"HEALTH [{index}/{len(commands)}] START {command.name}",
            file=destination,
            flush=True,
        )
        started = perf_counter()
        completed = subprocess.run(
            command.argv,
            cwd=command.cwd,
            check=False,
        )
        duration = perf_counter() - started
        if completed.returncode != 0:
            print(
                f"HEALTH [{index}/{len(commands)}] FAIL {command.name} "
                f"exit={completed.returncode} duration={duration:.2f}s",
                file=destination,
                flush=True,
            )
            return completed.returncode
        print(
            f"HEALTH [{index}/{len(commands)}] PASS {command.name} "
            f"duration={duration:.2f}s",
            file=destination,
            flush=True,
        )
    return 0


def main() -> int:
    return run_health_checks(ROOT, Path(sys.executable))


if __name__ == "__main__":
    raise SystemExit(main())
