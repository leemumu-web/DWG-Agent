from __future__ import annotations

from pathlib import Path

import pytest

from input_contract import InputContractError
from source_intake import read_production_source


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CORPUS_ROOT = REPOSITORY_ROOT / "Data/十份排版"


def _corpus_files(name_fragment: str) -> list[Path]:
    if not CORPUS_ROOT.is_dir():
        return []
    return sorted({
        path.resolve()
        for path in CORPUS_ROOT.rglob("*")
        if path.is_file() and name_fragment in path.name
    })


@pytest.mark.live_data
def test_all_historical_part_lists_enter_source_intake_with_row_conservation() -> None:
    sources = _corpus_files("构件零件清单毛净重")
    if not sources:
        pytest.skip("historical Tekla part-list corpus is absent")

    assert len(sources) == 11
    total_parts = 0
    total_components = 0
    for source in sources:
        result = read_production_source(source)
        nonempty_rows = [
            (row_number, row)
            for row_number, row in enumerate(result.working_values[1:], start=2)
            if any(value not in (None, "") for value in row)
        ]
        total_rows = {
            row_number
            for row_number, row in nonempty_rows
            if any(
                "合计" in str(value)
                for value in row
                if value not in (None, "")
            )
        }
        classified_rows = {
            part.source_row for part in result.parts
        } | {
            component.source_row for component in result.component_rows
        }
        assert {
            row_number for row_number, _ in nonempty_rows
        } == classified_rows | total_rows
        assert len(total_rows) == 1
        total_parts += len(result.parts)
        total_components += len(result.component_rows)

    assert total_parts == 6680
    assert total_components == 396


@pytest.mark.live_data
def test_decodable_component_only_lists_are_explicitly_rejected() -> None:
    sources = _corpus_files("构件清单新")
    if not sources:
        pytest.skip("historical component-only corpus is absent")

    explicitly_rejected = 0
    undecodable = 0
    for source in sources:
        try:
            read_production_source(source)
        except InputContractError as exc:
            assert "没有零件明细" in str(exc)
            explicitly_rejected += 1
        except ValueError as exc:
            assert "decode" in str(exc).lower()
            undecodable += 1
        else:
            pytest.fail(f"component-only list was accepted: {source}")

    assert explicitly_rejected > 0
    assert explicitly_rejected + undecodable == len(sources)
