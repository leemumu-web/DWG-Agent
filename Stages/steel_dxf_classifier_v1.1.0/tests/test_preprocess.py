from pathlib import Path

import pytest

from steel_dxf_classifier import preprocess
from steel_dxf_classifier.preprocess import (
    FilenamePreprocessError,
    preprocess_dxf_filenames,
)


def file_snapshot(directory: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in directory.iterdir()
        if path.is_file()
    }


def test_preprocess_normalizes_first_level_names_and_is_idempotent(
    tmp_path: Path,
) -> None:
    (tmp_path / "A.dxf").write_bytes(b"A")
    (tmp_path / "B.DXF").write_bytes(b"B")
    (tmp_path / "C_拆板前.dxf").write_bytes(b"C")
    (tmp_path / "note.txt").write_bytes(b"note")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "N.dxf").write_bytes(b"nested")

    first = preprocess_dxf_filenames(tmp_path)
    second = preprocess_dxf_filenames(tmp_path)

    assert [path.name for path in first] == [
        "A_拆板前.dxf",
        "B_拆板前.dxf",
        "C_拆板前.dxf",
    ]
    assert second == first
    assert file_snapshot(tmp_path) == {
        "A_拆板前.dxf": b"A",
        "B_拆板前.dxf": b"B",
        "C_拆板前.dxf": b"C",
        "note.txt": b"note",
    }
    assert (nested / "N.dxf").read_bytes() == b"nested"


def test_preprocess_rejects_collision_without_mutating_files(tmp_path: Path) -> None:
    (tmp_path / "A.dxf").write_bytes(b"old")
    (tmp_path / "A_拆板前.dxf").write_bytes(b"existing")
    before = file_snapshot(tmp_path)

    with pytest.raises(FilenamePreprocessError, match="collision"):
        preprocess_dxf_filenames(tmp_path)

    assert file_snapshot(tmp_path) == before


def test_preprocess_rolls_back_when_promotion_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "one.dxf").write_bytes(b"one")
    (tmp_path / "two.dxf").write_bytes(b"two")
    before = file_snapshot(tmp_path)
    real_replace = preprocess.os.replace
    calls = 0

    def fail_first_promotion(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("injected promotion failure")
        real_replace(source, destination)

    monkeypatch.setattr(preprocess.os, "replace", fail_first_promotion)

    with pytest.raises(FilenamePreprocessError, match="rolled back"):
        preprocess_dxf_filenames(tmp_path)

    assert file_snapshot(tmp_path) == before
    assert not any(path.name.startswith(".dxf-preprocess-") for path in tmp_path.iterdir())
