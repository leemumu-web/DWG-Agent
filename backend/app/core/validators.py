from __future__ import annotations

from app.core.exceptions import AppHTTPException

# Whitelists of sortable columns per resource — prevents SQL injection /
# column enumeration via unvalidated sort_by query parameters (BUG-13).
_SORT_COLUMNS: dict[str, set[str]] = {
    "users": {"id", "username", "real_name", "employee_no", "email", "status", "created_at", "updated_at"},
    "projects": {"id", "code", "name", "status", "created_at", "updated_at"},
    "files": {"id", "original_name", "size_bytes", "content_type", "status", "created_at", "updated_at"},
    "jobs": {"id", "task_type", "status", "priority", "progress", "created_at", "updated_at", "started_at", "finished_at"},
    "drawings": {"id", "drawing_no", "title", "discipline", "status", "created_at", "updated_at"},
}


def validate_sort_by(resource: str, value: str, default: str = "created_at") -> str:
    """Validate *value* against the whitelist for *resource*.

    Returns the validated value or *default* when the value is invalid.
    An empty / missing sort_by is treated as valid (caller should apply
    its own default ordering).

    May be used as a FastAPI dependency, or called inline in route
    functions before the value is interpolated into an ORDER BY clause.
    """
    allowed = _SORT_COLUMNS.get(resource, set())
    if not allowed:
        # Unknown resource — reject outright to prevent open-ended column
        # injection into SQL.
        raise AppHTTPException(
            422,
            "INVALID_SORT_RESOURCE",
            f"Sort is not supported for resource {resource!r}.",
        )
    clean = value.strip().lower()
    if not clean:
        return default
    if clean not in allowed:
        return default
    return clean
