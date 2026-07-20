"""Explicit import registry for every current Celery task module."""

from __future__ import annotations

from types import ModuleType


def load_tasks() -> tuple[ModuleType, ...]:
    """Import task modules while retaining their stable public task names."""
    from app.workers import (
        tasks_agent,
        tasks_cad,
        tasks_dispatch,
        tasks_dxf,
        tasks_dxf2dwg,
        tasks_dxf2excel,
        tasks_dxf_classification,
        tasks_excel_final,
        tasks_maintenance,
        tasks_report,
    )

    return (
        tasks_agent,
        tasks_cad,
        tasks_dispatch,
        tasks_dxf,
        tasks_dxf2dwg,
        tasks_dxf2excel,
        tasks_dxf_classification,
        tasks_excel_final,
        tasks_maintenance,
        tasks_report,
    )


__all__ = ["load_tasks"]
