"""Minimal runtime configuration owned by the standalone Excel Final Stage."""

from __future__ import annotations

from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "data/output"

# The platform injects the read-only connection at the isolated process boundary.
DB_CONFIG: dict[str, object] = {}
