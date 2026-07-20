"""Internal typed contracts shared by Excel workbook import and execution."""

from __future__ import annotations

from typing import NotRequired, TypedDict


class PartsImportStats(TypedDict):
    parts_imported: int
    error: NotRequired[str]


class ComponentsImportStats(TypedDict):
    components_imported: int


class BatchImportStats(PartsImportStats, ComponentsImportStats):
    batch_id: int


__all__ = ["BatchImportStats", "ComponentsImportStats", "PartsImportStats"]
