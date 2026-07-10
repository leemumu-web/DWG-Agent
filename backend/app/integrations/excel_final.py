from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

PipelineCallable = Callable[[Path, Path | None], Path]


def load_excel_final_pipeline() -> tuple[PipelineCallable, PipelineCallable]:
    """Load the package while bridging its former top-level module name."""
    from excel_final import handbook

    existing = sys.modules.get("handbook")
    if existing is not None and existing is not handbook:
        raise RuntimeError("The legacy 'handbook' module name is already in use.")
    sys.modules["handbook"] = handbook

    from excel_final.pipeline import run_init_pipeline, run_pipeline

    return run_init_pipeline, run_pipeline
