from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tests.support.paths import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))

from scripts.remnant_inventory.report_corpus import (
    discover_drawings,
    main,
    write_corpus_report,
)


def _write_dwg(path: Path, version: bytes = b"AC1032") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(version + b"\x00" * 1018)


def test_cli_requires_explicit_input_and_output_paths() -> None:
    with pytest.raises(SystemExit) as exc:
        main([])

    assert exc.value.code == 2


def test_discovery_handles_144_ac1032_drawings(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for index in range(144):
        _write_dwg(source / f"group-{index % 3}" / f"remnant-{index:03}.dwg")

    drawings = discover_drawings(source)

    assert len(drawings) == 144
    assert {item.format for item in drawings} == {"dwg"}
    assert {item.dwg_version for item in drawings} == {"AC1032"}
    assert all(len(item.sha256) == 64 for item in drawings)


def test_report_writes_metadata_only_and_never_copies_source_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "report"
    _write_dwg(source / "secret-remnant.dwg")

    result = write_corpus_report(source, output, convert=False)

    assert result["aggregate"]["drawing_count"] == 1
    assert sorted(path.name for path in output.iterdir()) == ["candidates.csv", "report.json"]
    assert not list(output.rglob("*.dwg"))
    assert not list(output.rglob("*.dxf"))
    payload = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert payload["drawings"][0]["relative_path"] == "secret-remnant.dwg"
    assert "source_path" not in payload["drawings"][0]
