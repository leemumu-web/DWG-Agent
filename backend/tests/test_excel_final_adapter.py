from __future__ import annotations

import sys
from pathlib import Path

from app.integrations.excel_final import load_excel_final_pipeline


def test_excel_final_adapter_bridges_legacy_handbook_import():
    sys.modules.pop("handbook", None)

    run_init_pipeline, run_pipeline = load_excel_final_pipeline()

    from excel_final import handbook

    assert callable(run_init_pipeline)
    assert callable(run_pipeline)
    assert sys.modules["handbook"] is handbook


def test_excel_final_completion_event_does_not_pass_duplicate_batch_id():
    source = (
        Path(__file__).resolve().parents[1] / "app/services/excel_final_service.py"
    ).read_text(encoding="utf-8")

    assert "batch_id=batch.id" not in source
