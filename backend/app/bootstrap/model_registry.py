"""Explicit import registry for all SQLAlchemy model modules."""

from __future__ import annotations

from types import ModuleType


def load_models() -> tuple[ModuleType, ...]:
    """Import every model owner exactly once and return the loaded modules."""
    from app.models import (
        agent_memory,
        agent_run,
        audit_log,
        control_plane,
        daily_archive,
        dxf_classification,
        excel_final,
        file,
        file_transfer,
        job,
        result,
        storage_scan,
        workflow,
        workflow_input,
    )
    from app.modules.identity.models import role, token_blacklist, user
    from app.modules.projects.models import drawing, project

    return (
        agent_memory,
        agent_run,
        audit_log,
        control_plane,
        daily_archive,
        drawing,
        dxf_classification,
        excel_final,
        file,
        file_transfer,
        job,
        project,
        result,
        role,
        storage_scan,
        token_blacklist,
        user,
        workflow,
        workflow_input,
    )


MODEL_MODULES = load_models()

__all__ = ["MODEL_MODULES", "load_models"]
