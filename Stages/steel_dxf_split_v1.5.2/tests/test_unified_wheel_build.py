from __future__ import annotations

import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


def _load_builder_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_unified_wheel.py"
    spec = importlib.util.spec_from_file_location("build_unified_wheel", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wheel_contract_requires_shared_part_mark_layout() -> None:
    builder = _load_builder_module()

    assert "steel_dxf_split/part_mark_layout.py" in builder.REQUIRED_WHEEL_MEMBERS


def test_clean_builder_publishes_verified_unified_wheel(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "build_unified_wheel.py"),
            "--output-dir",
            str(tmp_path),
        ],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1

    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
        entry_points = next(
            archive.read(name).decode("utf-8")
            for name in names
            if name.endswith(".dist-info/entry_points.txt")
        )

    assert entry_points == (
        "[console_scripts]\n"
        "steel-dxf-split = steel_dxf_split.cli:main\n"
    )
    assert not any(
        name.endswith(retired)
        for name in names
        for retired in (
            "/batch_cli.py",
            "/weld_allowance_cli.py",
            "/weld_allowance_release.py",
        )
    )
    assert "steel_dxf_split/paired_output.py" in names
    assert "steel_dxf_split/hole_color_policy.py" in names
    assert "steel_dxf_split/part_mark_layout.py" in names
    assert "steel_dxf_split/release_evidence/box_release_attestation.json" in names


def test_clean_source_snapshot_copies_only_contract_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    builder = _load_builder_module()
    repository_root = tmp_path / "repository"
    source_root = tmp_path / "temporary" / "source"
    source_root.mkdir(parents=True)
    copied_files: list[tuple[Path, Path]] = []
    copied_trees: list[tuple[Path, Path]] = []

    monkeypatch.setattr(
        builder.shutil,
        "copy2",
        lambda source, destination: copied_files.append((Path(source), Path(destination))),
    )
    monkeypatch.setattr(
        builder.shutil,
        "copytree",
        lambda source, destination: copied_trees.append((Path(source), Path(destination))),
    )

    builder.copy_clean_source(repository_root, source_root)

    assert copied_files == [
        (repository_root / "pyproject.toml", source_root / "pyproject.toml"),
        (repository_root / "README.md", source_root / "README.md"),
        (repository_root / "uv.lock", source_root / "uv.lock"),
    ]
    assert copied_trees == [(repository_root / "src", source_root / "src")]


def test_failed_atomic_promotion_removes_pending_wheel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    builder = _load_builder_module()
    output_dir = tmp_path / "published"
    destination = output_dir / "steel_dxf_split-1.5.2-py3-none-any.whl"
    output_dir.mkdir()
    destination.write_bytes(b"previous wheel")
    recorded_commands: list[list[str]] = []
    recorded_kwargs: list[dict[str, object]] = []

    monkeypatch.setattr(builder, "copy_clean_source", lambda *_: None)
    monkeypatch.setattr(builder.shutil, "which", lambda _: "uv")

    def write_verified_wheel(command: list[str], **kwargs: object) -> None:
        recorded_commands.append(command)
        recorded_kwargs.append(kwargs)
        wheel_output = Path(command[command.index("--out-dir") + 1])
        wheel_path = wheel_output / "steel_dxf_split-1.5.2-py3-none-any.whl"
        with zipfile.ZipFile(wheel_path, "w") as archive:
            archive.writestr(
                "steel_dxf_split-1.5.2.dist-info/entry_points.txt",
                builder.EXPECTED_ENTRY_POINTS,
            )
            for member in builder.REQUIRED_WHEEL_MEMBERS:
                archive.writestr(member, "")

    monkeypatch.setattr(builder.subprocess, "run", write_verified_wheel)
    monkeypatch.setattr(
        builder.os,
        "replace",
        lambda *_: (_ for _ in ()).throw(OSError("promote failed")),
    )

    with pytest.raises(OSError, match="promote failed"):
        builder.build_wheel(tmp_path / "repository", output_dir)

    assert recorded_commands[0][:8] == [
        "uv",
        "build",
        "--offline",
        "--no-python-downloads",
        "--wheel",
        "--no-build-logs",
        "--no-create-gitignore",
        "--out-dir",
    ]
    temporary_output = Path(recorded_commands[0][8])
    source_root = Path(recorded_commands[0][9])
    assert temporary_output.name == "wheel-output"
    assert source_root.name == "source"
    assert temporary_output.parent == source_root.parent
    assert recorded_kwargs == [{"cwd": temporary_output.parent, "check": True}]
    assert destination.read_bytes() == b"previous wheel"
    assert not list(output_dir.glob("*.pending"))
