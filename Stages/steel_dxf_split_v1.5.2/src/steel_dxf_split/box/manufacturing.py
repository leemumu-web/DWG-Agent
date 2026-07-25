from __future__ import annotations

from .assembly import AssemblySearchResult
from .manufacturing_ir import BoxManufacturingIR


def freeze_manufacturing(search: AssemblySearchResult) -> BoxManufacturingIR:
    """Select the Project 2 winner's immutable manufacturing IR."""

    return search.best.mir
