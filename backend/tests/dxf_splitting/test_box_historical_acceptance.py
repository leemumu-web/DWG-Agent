from __future__ import annotations

from tools.box_acceptance.historical_acceptance import (
    allowed_merge_families,
    select_deduplication_only_samples,
    summarize_historical_results,
)


def _sample(
    sample_id: str,
    *,
    family: str = "BOX",
    source_sheet: str | None = "first",
    keys: tuple[str, ...] = ("web_deduplication",),
) -> dict[str, object]:
    return {
        "sample_id": sample_id,
        "family": family,
        "source_sheet": source_sheet,
        "constraints": [
            {"key": key, "description": key}
            for key in keys
        ],
        "historical_wrong_result": {"relative_path": "old.dwg", "sha256": "a" * 64},
    }


def test_historical_acceptance_selects_only_complete_deduplication_cases() -> None:
    """Catch 7+7, no-output, BH, or second-sheet samples leaking into the 49 gate."""

    manifest = {
        "samples": [
            _sample("web-only"),
            _sample(
                "web-and-flange",
                keys=("web_deduplication", "flange_deduplication"),
            ),
            _sample("partial-length", keys=("web_deduplication", "flange_length")),
            _sample("no-output", keys=("formal_output_required",)),
            _sample("second-sheet", source_sheet="second"),
            _sample("bh", family="BH"),
            _sample("diagnostic", source_sheet=None, keys=()),
        ]
    }

    selected = select_deduplication_only_samples(manifest)

    assert [sample["sample_id"] for sample in selected] == [
        "web-only",
        "web-and-flange",
    ]
    assert allowed_merge_families(selected[0]) == frozenset({"web"})
    assert allowed_merge_families(selected[1]) == frozenset({"web", "flange"})


def test_historical_summary_never_lets_auto_accept_override_a_real_failure() -> None:
    """Catch the internal disposition being counted as external production success."""

    summary = summarize_historical_results(
        (
            {
                "sample_id": "passed",
                "ok": True,
                "internal_disposition": "auto_accept",
                "forbidden_changes": [],
            },
            {
                "sample_id": "failed",
                "ok": False,
                "internal_disposition": "auto_accept",
                "forbidden_changes": ["contour"],
            },
        )
    )

    assert summary == {
        "sample_count": 2,
        "passed": 1,
        "failed": 1,
        "errors": 0,
        "auto_accept_external_failures": ["failed"],
        "forbidden_change_counts": {"contour": 1},
    }
