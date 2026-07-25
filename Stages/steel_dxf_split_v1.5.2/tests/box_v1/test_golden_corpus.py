from __future__ import annotations

from pathlib import Path

import pytest

from tests.box_v1.paths import INPUTS, REFERENCES
from tools import compare_box_corpus as compare


def test_golden_comparison_freezes_source_result_before_reference_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    frozen = object()
    reference = object()
    monkeypatch.setattr(
        compare,
        "compile_source_only",
        lambda _path: events.append("compile") or frozen,
    )
    monkeypatch.setattr(
        compare,
        "load_manual_reference",
        lambda _path: events.append("reference") or reference,
    )
    monkeypatch.setattr(
        compare,
        "_compare_frozen_pair",
        lambda core, manual, **_kwargs: {"ok": core is frozen and manual is reference},
    )

    result = compare.compare_pair(
        INPUTS / "2b1-cb-56_拆板前.dxf",
        REFERENCES / "2b1-cb-56_拆板后.dxf",
        candidate_dir=tmp_path,
    )

    assert events == ["compile", "reference"]
    assert result["ok"] is True


def test_authoritative_twenty_pair_corpus_passes_without_mutation(
    tmp_path: Path,
) -> None:
    report = compare.compare_corpus(
        INPUTS,
        REFERENCES,
        candidate_root=tmp_path / "candidates",
    )

    assert report["sample_count"] == 20
    assert report["passed"] == 20
    assert report["failed"] == 0
    assert report["all_passed"] is True
    assert report["read_only_corpus"]["inputs_unchanged"] is True
    assert report["read_only_corpus"]["references_unchanged"] is True
    failed = tuple(sample for sample in report["samples"] if not sample["ok"])
    assert failed == ()
    assert all(
        sample["proof_disposition"] == "auto_accept" for sample in report["samples"]
    )
    assert all(
        sample["search_status"]["search_complete"] is True
        for sample in report["samples"]
    )
    assert all(
        sample["checks"]["manual_geometry_and_holes"] is True
        for sample in report["samples"]
    )
    assert all(
        sample["ground_truth_used_for_decision"] is False
        for sample in report["samples"]
    )
    assert all(sample["saved_dxf"]["ok"] is True for sample in report["samples"])
