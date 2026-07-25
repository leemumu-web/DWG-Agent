from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from steel_dxf_split.box.artifact_io import fsync_directory, write_json_atomic


def test_directory_fsync_is_a_portable_best_effort_operation(tmp_path: Path) -> None:
    fsync_directory(tmp_path)


@pytest.mark.skipif(
    not hasattr(os, "O_DIRECTORY"),
    reason="项目 2 的目录 fsync 契约只适用于 POSIX",
)
def test_atomic_json_fsyncs_file_then_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "manifest.json"
    targets: list[bool] = []
    real_fsync = os.fsync

    def record(file_descriptor: int) -> None:
        targets.append(stat.S_ISDIR(os.fstat(file_descriptor).st_mode))
        real_fsync(file_descriptor)

    monkeypatch.setattr(os, "fsync", record)

    write_json_atomic(destination, {"ok": True})

    assert targets == [False, True]


@pytest.mark.skipif(
    not hasattr(os, "O_DIRECTORY"),
    reason="项目 2 的目录 fsync 契约只适用于 POSIX",
)
def test_atomic_json_writes_utf8_and_leaves_no_pending_file(tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "证据.json"

    write_json_atomic(destination, {"part": "箱梁", "value": 1.25})

    assert json.loads(destination.read_text(encoding="utf-8")) == {
        "part": "箱梁",
        "value": 1.25,
    }
    assert not list(destination.parent.glob("*.pending"))


def test_atomic_json_rejects_non_finite_data_and_preserves_previous_file(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "manifest.json"
    destination.write_text("previous", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON"):
        write_json_atomic(destination, {"invalid": float("nan")})

    assert destination.read_text(encoding="utf-8") == "previous"
    assert not list(tmp_path.glob("*.pending"))
