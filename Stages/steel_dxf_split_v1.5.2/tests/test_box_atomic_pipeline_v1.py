from __future__ import annotations

import os
from pathlib import Path

import pytest

from steel_dxf_split.box import delivery
from steel_dxf_split.box.contracts import BoxSourceContract
from steel_dxf_split.pipeline import SplitOptions, split_dxf

ROOT = Path(__file__).resolve().parents[1]
BOX_SOURCE = (
    ROOT
    / "samples"
    / "box_pairs"
    / "BOX_拆板前_dxf"
    / "2b1-cb-56_拆板前.dxf"
)


def test_box_writer_failure_leaves_no_partial_final_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "output"

    def fail_writer(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected BOX writer failure")

    monkeypatch.setattr(delivery, "write_box_clean", fail_writer)

    with pytest.raises(RuntimeError, match="injected BOX writer failure"):
        split_dxf(
            BOX_SOURCE,
            output,
            SplitOptions(box_source_contract=BoxSourceContract()),
        )

    assert not list(output.rglob("*"))


def test_partial_promotion_failure_restores_every_existing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    backups = stage / "backups"
    stage.mkdir()
    output.mkdir()
    staged_first = stage / "first.new"
    staged_second = stage / "second.new"
    final_first = output / "first.dxf"
    final_second = output / "second.json"
    staged_first.write_bytes(b"new-first")
    staged_second.write_bytes(b"new-second")
    final_first.write_bytes(b"old-first")
    final_second.write_bytes(b"old-second")
    real_replace = os.replace

    def fail_second_promotion(source: str | Path, destination: str | Path) -> None:
        if Path(source) == staged_second:
            raise OSError("injected second promotion failure")
        real_replace(source, destination)

    monkeypatch.setattr(delivery.os, "replace", fail_second_promotion)

    with pytest.raises(OSError, match="second promotion failure"):
        delivery._promote_staged_files(
            (
                (staged_first, final_first),
                (staged_second, final_second),
            ),
            backup_dir=backups,
        )

    assert final_first.read_bytes() == b"old-first"
    assert final_second.read_bytes() == b"old-second"
    assert not list(backups.glob("*.bak"))
