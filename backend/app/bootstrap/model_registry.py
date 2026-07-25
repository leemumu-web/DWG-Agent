"""Explicit import registry for all SQLAlchemy model modules."""

from __future__ import annotations

from types import ModuleType


def load_models() -> tuple[ModuleType, ...]:
    """Import every model owner exactly once and return the loaded modules."""
    from app.modules.automation.agent.models import memory as agent_memory
    from app.modules.automation.agent.models import runs as agent_run
    from app.modules.dxf_classification import models as dxf_classification
    from app.modules.dxf_splitting import models as dxf_splitting
    from app.modules.excel_processing import models as excel_processing
    from app.modules.files import models as files
    from app.modules.identity.models import role, token_blacklist, user
    from app.modules.jobs import models as jobs
    from app.modules.operations.audit import models as audit_log
    from app.modules.operations.control_plane import models as control_plane
    from app.modules.operations.daily_archive import models as daily_archive
    from app.modules.projects.models import drawing, project
    from app.modules.remnant_inventory import models as remnant_inventory
    from app.modules.workflows import models as workflows

    return (
        agent_memory,
        agent_run,
        audit_log,
        control_plane,
        daily_archive,
        drawing,
        dxf_classification,
        dxf_splitting,
        excel_processing,
        files,
        jobs,
        project,
        remnant_inventory,
        role,
        token_blacklist,
        user,
        workflows,
    )


MODEL_MODULES = load_models()

__all__ = ["MODEL_MODULES", "load_models"]
