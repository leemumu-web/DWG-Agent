from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_documentation_consistency_gate_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/check_docs.py")],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_frontend_env_example_uses_fixed_local_backend_port() -> None:
    content = (REPO_ROOT / "frontend/.env.example").read_text(encoding="utf-8")

    assert "http://127.0.0.1:8010" in content
    assert "http://127.0.0.1:8000" not in content
