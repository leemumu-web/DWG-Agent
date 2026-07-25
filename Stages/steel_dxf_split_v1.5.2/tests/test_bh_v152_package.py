from __future__ import annotations

import json
from importlib.resources import files
import os
from pathlib import Path

from tools.verify_bh_v152_source import verify_bh_source


BH_V152_UPSTREAM = Path(
    os.environ.get(
        "BH_V152_UPSTREAM",
        r"D:\Documents\Codex\worktrees\steel-dxf-split\v1.5.2",
    )
)


def test_root_worker_uses_bh_v152() -> None:
    import steel_dxf_split

    assert steel_dxf_split.__version__ == "1.5.2"
    assert callable(steel_dxf_split.split_dxf)


def test_bh_release_evidence_is_packaged_at_root() -> None:
    artifact = files("steel_dxf_split").joinpath(
        "release_evidence/project_tekla_bh_dxf_v1.json"
    )
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["schema"].startswith("BH-")


def test_bh_v152_source_is_exact_except_worker_seams_and_declared_domain_patch() -> (
    None
):
    result = verify_bh_source(
        BH_V152_UPSTREAM,
        Path.cwd(),
    )
    assert result == {
        "exact": 39,
        "adapted": 4,
        "declared_adapted_files": [
            "__init__.py",
            "artifact_io.py",
            "cli.py",
            "pipeline.py",
        ],
        "patched": 15,
        "patchset_id": (
            "bh-paired-output-hole-color-unified-part-mark-compact-label-and-"
            "uniform-scale-2026-07-24"
        ),
        "declared_patch_files": [
            "bh_constraints.py",
            "bh_development.py",
            "bh_extractor.py",
            "bh_geometry.py",
            "bh_hypothesis.py",
            "bh_knowledge.py",
            "bh_provenance.py",
            "bh_release_evidence.py",
            "bh_solver.py",
            "bh_text.py",
            "bh_validator.py",
            "bh_writer.py",
            "dxf_preview.py",
            "release_evidence/project_tekla_bh_dxf_v1.json",
            "weld_allowance.py",
        ],
        "added": 2,
        "declared_added_files": ["bh_metric_scale.py", "bh_project_ledger.py"],
        "retired": 3,
        "declared_retired_files": [
            "batch_cli.py",
            "weld_allowance_cli.py",
            "weld_allowance_release.py",
        ],
        "missing": 0,
        "unexpected": 0,
        "unexpected_files": [],
        "invalid_adaptations": [],
    }
