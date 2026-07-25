from __future__ import annotations

from pathlib import Path

from steel_dxf_split.pipeline import SplitResult


def _native_report(family: str) -> dict[str, object]:
    previews = {
        "before": "before.png",
        "after": "after.png",
        "shared_view": True,
    }
    if family == "BH":
        return {
            "profile_family": "BH",
            "automation_route": "production",
            "automation_assessment": {"disposition": "auto_accept"},
            "proof_report": {"disposition": "auto_accept", "obligations": []},
            "outputs": {"previews": previews},
            "search_status": {"search_complete": True},
            "semantic_fingerprints": {"manufacturing_ir": "b" * 64},
            "compiler": {"stages": [{"duration_ms": 125.0}]},
            "preview_rendering": {"render_seconds": 0.25},
            "validation": {"values": {"mass_error_pct": 0.0}},
            "supervised_comparison": None,
            "diagnostic_codes": [],
        }
    return {
        "profile_family": "BOX",
        "automation_route": "auto_accepted",
        "single_file_disposition": "auto_accept",
        "proof_report": {
            "disposition": "auto_accept",
            "obligations": [
                {"diagnostic_code": "BOX-PROOF-DIRECT-SOURCE-BOUNDARY"}
            ],
        },
        "outputs": {"previews": previews},
        "search_status": {"search_complete": True},
        "manufacturing_ir": {"fingerprint": "c" * 64},
        "timing": {"preview_render_seconds": 0.2},
    }


def test_bh_and_box_normalize_to_one_worker_result_schema(tmp_path: Path) -> None:
    summaries: list[dict[str, object]] = []
    results: list[SplitResult] = []
    for family in ("BH", "BOX"):
        result = SplitResult.from_native(
            production_path=tmp_path / f"{family}.dxf",
            review_candidate_path=None,
            report_path=tmp_path / f"{family}.json",
            report=_native_report(family),
        )
        results.append(result)
        summaries.append(
            result.to_summary(
                input_path=tmp_path / f"{family}-source.dxf",
                compiler_version="1.5.2",
                processing_seconds=1.0,
            )
        )

    assert {result.automation_route for result in results} == {"auto_accepted"}
    assert {result.disposition for result in results} == {"auto_accept"}
    assert results[0].native_automation_route == "production"
    assert results[1].native_automation_route == "auto_accepted"
    assert summaries[0].keys() == summaries[1].keys()
    assert all(summary["production_ready"] is True for summary in summaries)
    assert all(summary["weld_allowance"] is None for summary in summaries)
    assert all(summary["task_dir"] is None for summary in summaries)
