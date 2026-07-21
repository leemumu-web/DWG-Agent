"""Stable repository paths for tests that inspect source or deployment files."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
FRONTEND_ROOT = REPO_ROOT / "frontend"
STAGES_ROOT = REPO_ROOT / "Stages"
