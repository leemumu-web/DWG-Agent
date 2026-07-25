from __future__ import annotations

from pathlib import Path


def test_retired_wrapper_scripts_and_context_cannot_restore_old_entrypoints() -> None:
    root = Path(__file__).resolve().parents[1]
    retired = (
        root / "scripts" / "run.ps1",
        root / "scripts" / "run.sh",
        root / "scripts" / "run_bh_pairs.ps1",
        root / "scripts" / "run_bh_pairs.sh",
        root / "scripts" / "run_box_pairs.ps1",
    )
    assert all(not path.exists() for path in retired)
    context = (root / "CONTEXT.md").read_text(encoding="utf-8")
    assert "steel-dxf-split-batch" not in context
    assert "src/steel_dxf_split/batch_cli.py" not in context
    assert "pipeline.py" in context
    assert "normal" in context
    assert "weld_allowance" in context
