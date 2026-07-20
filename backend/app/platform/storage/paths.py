from __future__ import annotations

from pathlib import Path

from app.platform.http.exceptions import AppHTTPException


def ensure_within_root(root: Path, candidate: Path) -> Path:
    """Resolve *candidate* against *root* — raise 400 if path escapes root.

    Per §6.2.6: every file path emitted by storage must pass through this guard.
    """
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    # is_relative_to rejects sibling directories sharing a common
    # prefix (e.g. /app/var/storage-evil), which str.startswith
    # would accept.  Requires Python ≥ 3.9.
    if not candidate_resolved.is_relative_to(root_resolved):
        raise AppHTTPException(
            400,
            "INVALID_STORAGE_PATH",
            "Path escapes the configured storage root.",
        )
    return candidate_resolved
