from __future__ import annotations

from pathlib import Path


def ensure_within_root(root: Path, candidate: Path) -> Path:
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    if not str(candidate_resolved).startswith(str(root_resolved)):
        raise ValueError("Path escapes root")
    return candidate_resolved
