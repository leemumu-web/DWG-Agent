import json
import os
from pathlib import Path

import pytest

from steel_dxf_split import layered_cli
from steel_dxf_split.process_control import IsolatedProcessResult


main = layered_cli.main


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / "samples" / "bh_pairs"
REFERENCE_DIR = ROOT / "samples" / "bh_pairs"


@pytest.mark.skipif(os.name == "nt", reason="Isolated corpus workers are Linux-only")
def test_cli_processes_sorted_pairs_and_builds_site(tmp_path: Path) -> None:
    inputs = [
        SOURCE_DIR / "2b1-cb-26_拆板前.dxf",
        SOURCE_DIR / "2b1-cb-18_拆板前.dxf",
    ]
    code = main(
        [
            *(str(path) for path in inputs),
            "--reference-dir",
            str(REFERENCE_DIR),
            "--output-root",
            str(tmp_path),
            "--clean",
        ]
    )
    assert code == 0
    manifest = json.loads(
        (tmp_path / "manifest.json").read_text(encoding="utf-8")
    )
    assert [item["sample_id"] for item in manifest["samples"]] == [
        "2b1-cb-18",
        "2b1-cb-26",
    ]
    assert all(item["ok"] for item in manifest["samples"])
    for item in manifest["samples"]:
        timing = item["worker_process_timing"]
        assert timing["clock"] == "time.perf_counter"
        assert timing["timeout_basis"] == "active_supervision"
        assert timing["wall_seconds"] == pytest.approx(
            timing["active_supervision_seconds"]
            + timing["unbudgeted_wall_seconds"]
        )
    assert manifest["corpus_artifacts"]
    assert (tmp_path / "dxf/corpus/13-corpus-summary.dxf").exists()
    assert (tmp_path / "svg/corpus/13-corpus-summary.svg").exists()
    assert (tmp_path / "site/index.html").exists()
    index = (tmp_path / "site/index.html").read_text(encoding="utf-8")
    assert "阶段 13 DXF" in index
    assert "阶段 13 SVG" in index
    neighbor = tmp_path.with_name(tmp_path.name + "-neighbor")
    neighbor.mkdir()
    (neighbor / "keep.txt").write_text("keep", encoding="utf-8")
    (tmp_path / "stale.txt").write_text("stale", encoding="utf-8")
    second_code = main(
        [
            str(SOURCE_DIR / "2b1-cb-18_拆板前.dxf"),
            "--reference-dir",
            str(REFERENCE_DIR),
            "--output-root",
            str(tmp_path),
            "--clean",
        ]
    )
    assert second_code == 0
    assert not (tmp_path / "stale.txt").exists()
    assert (neighbor / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_cli_records_missing_manual_reference(tmp_path: Path) -> None:
    reference_dir = tmp_path / "empty-references"
    reference_dir.mkdir()
    output_root = tmp_path / "run"
    code = main(
        [
            str(SOURCE_DIR / "2b1-cb-18_拆板前.dxf"),
            "--reference-dir",
            str(reference_dir),
            "--output-root",
            str(output_root),
        ]
    )
    assert code == 1
    manifest = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["samples"][0]["error"]["code"] == "MISSING_REFERENCE"


def test_cli_records_worker_nonzero_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def failed_run(*args, **kwargs):
        return IsolatedProcessResult(2, "boom", 0.01, False)

    monkeypatch.setattr(layered_cli, "run_isolated_process", failed_run)
    output_root = tmp_path / "run"
    code = main(
        [
            str(SOURCE_DIR / "2b1-cb-18_拆板前.dxf"),
            "--reference-dir",
            str(REFERENCE_DIR),
            "--output-root",
            str(output_root),
        ]
    )
    assert code == 1
    manifest = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["samples"][0]["error"]["code"] == "WORKER_FAILED"


def test_cli_records_worker_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout_run(*args, **kwargs):
        return IsolatedProcessResult(
            124,
            "timed out",
            30.01,
            True,
            active_supervision_seconds=0.01,
            unbudgeted_wall_seconds=30.0,
        )

    monkeypatch.setattr(layered_cli, "run_isolated_process", timeout_run)
    output_root = tmp_path / "run"
    code = main(
        [
            str(SOURCE_DIR / "2b1-cb-18_拆板前.dxf"),
            "--reference-dir",
            str(REFERENCE_DIR),
            "--output-root",
            str(output_root),
            "--timeout-seconds",
            "0.01",
        ]
    )
    assert code == 1
    manifest = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["samples"][0]["error"]["code"] == "WORKER_TIMEOUT"
    assert manifest["samples"][0]["error"]["message"] == (
        "active=0.010000s; wall=30.010000s; unbudgeted=30.000000s"
    )


def test_cli_rejects_cleaning_the_project_root() -> None:
    with pytest.raises(ValueError, match="unsafe output root"):
        main(
            [
                str(SOURCE_DIR / "2b1-cb-18_拆板前.dxf"),
                "--reference-dir",
                str(REFERENCE_DIR),
                "--output-root",
                str(ROOT),
                "--clean",
            ]
        )
