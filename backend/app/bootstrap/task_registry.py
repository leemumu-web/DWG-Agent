"""Explicit import registry for every current Celery task module."""

from __future__ import annotations

from types import ModuleType


def load_tasks() -> tuple[ModuleType, ...]:
    """Import task modules while retaining their stable public task names."""
    from app.modules.cad_processing import tasks as cad_processing
    from app.modules.dxf_classification import tasks as dxf_classification
    from app.modules.dxf_splitting import tasks as dxf_splitting
    from app.modules.excel_processing import tasks as excel_processing
    from app.modules.jobs import tasks as jobs
    from app.modules.jobs.recovery import register_job_worker_maintenance
    from app.modules.operations.control_plane import tasks as control_plane
    from app.modules.operations.control_plane.interface import (
        register_control_plane_worker_observer,
    )
    from app.modules.operations.daily_archive import tasks as daily_archive
    from app.modules.operations.storage_reconciliation import (
        tasks as storage_reconciliation,
    )
    from app.modules.remnant_inventory import tasks as remnant_inventory

    register_job_worker_maintenance()
    register_control_plane_worker_observer()

    return (
        cad_processing,
        dxf_classification,
        dxf_splitting,
        excel_processing,
        jobs,
        daily_archive,
        storage_reconciliation,
        control_plane,
        remnant_inventory,
    )


__all__ = ["load_tasks"]
