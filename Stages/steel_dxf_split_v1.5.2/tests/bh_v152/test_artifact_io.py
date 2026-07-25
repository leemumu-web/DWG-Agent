from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

from steel_dxf_split.artifact_io import write_json_atomic


@pytest.mark.skipif(
    os.name == "nt",
    reason="BH v1.5.2 原子目录 fsync 合同仅在 Linux Worker 上可用",
)
def test_write_json_atomic_replaces_a_complete_utf8_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"old": true}\n', encoding="utf-8")
    fsync_targets: list[bool] = []
    real_fsync = os.fsync

    def recording_fsync(file_descriptor: int) -> None:
        fsync_targets.append(stat.S_ISDIR(os.fstat(file_descriptor).st_mode))
        real_fsync(file_descriptor)

    monkeypatch.setattr(os, "fsync", recording_fsync)

    write_json_atomic(path, {"中文": "完整", "count": 2})

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "中文": "完整",
        "count": 2,
    }
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert set(tmp_path.iterdir()) == {path}
    assert fsync_targets == [False, True]


def test_write_json_atomic_preserves_the_previous_file_on_serialization_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "manifest.json"
    previous = '{"stable": true}\n'
    path.write_text(previous, encoding="utf-8")

    with pytest.raises(ValueError, match="Out of range float values"):
        write_json_atomic(path, {"invalid": float("nan")})

    assert path.read_text(encoding="utf-8") == previous
    assert set(tmp_path.iterdir()) == {path}


@pytest.mark.skipif(
    os.name == "nt",
    reason="BH v1.5.2 原子目录 fsync 合同仅在 Linux Worker 上可用",
)
def test_write_json_atomic_creates_the_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "manifest.json"

    write_json_atomic(path, {"ok": True})

    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}
    assert set(path.parent.iterdir()) == {path}
