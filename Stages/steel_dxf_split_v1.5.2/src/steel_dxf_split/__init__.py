"""Unified paired-output Tekla BH and BOX manufacturing compiler.

The package root intentionally uses lazy imports. Inspecting BH or BOX domain
modules must not initialize the full geometry pipeline. The public API detects
the family once, dispatches to one native core, and publishes a normal DXF plus
its weld-allowance variant as one validated task.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["SplitOptions", "SplitResult", "split_dxf"]
__version__ = "1.5.2"

if TYPE_CHECKING:
    from .pipeline import SplitOptions, SplitResult, split_dxf


def __getattr__(name: str) -> Any:
    if name in __all__:
        from .pipeline import SplitOptions, SplitResult, split_dxf

        return {
            "SplitOptions": SplitOptions,
            "SplitResult": SplitResult,
            "split_dxf": split_dxf,
        }[name]
    raise AttributeError(name)
