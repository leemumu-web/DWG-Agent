"""Explicit import registry for every current Celery task module."""

from __future__ import annotations

from types import ModuleType


def load_tasks() -> tuple[ModuleType, ...]:
    """Import task modules while retaining their stable public task names."""
    from app.modules.cad_processing import tasks as cad_processing
    from app.modules.dxf_classification import tasks as dxf_classification
    from app.modules.excel_processing import tasks as excel_processing
    from app.modules.jobs.recovery import register_job_worker_maintenance
    from app.workers import (
        tasks_agent,
        tasks_cad,
        tasks_dispatch,
        tasks_maintenance,
        tasks_report,
    )

    register_job_worker_maintenance()

    return (
        tasks_agent,
        tasks_cad,
        cad_processing,
        dxf_classification,
        tasks_dispatch,
        excel_processing,
        tasks_maintenance,
        tasks_report,
    )


__all__ = ["load_tasks"]
