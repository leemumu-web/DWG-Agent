"""CLI entry-point contracts."""

from __future__ import annotations

import subprocess
import sys


def test_python_module_entrypoint_runs_typer_application() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "dxf2excel", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "DXF" in completed.stdout
    assert "extract" in completed.stdout
    assert "validate" in completed.stdout
