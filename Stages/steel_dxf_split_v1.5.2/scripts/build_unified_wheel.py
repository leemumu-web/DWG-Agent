from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from uuid import uuid4


EXPECTED_ENTRY_POINTS = (
    "[console_scripts]\n"
    "steel-dxf-split = steel_dxf_split.cli:main\n"
)
RETIRED_MODULE_SUFFIXES = (
    "/batch_cli.py",
    "/weld_allowance_cli.py",
    "/weld_allowance_release.py",
)
REQUIRED_WHEEL_MEMBERS = (
    "steel_dxf_split/paired_output.py",
    "steel_dxf_split/hole_color_policy.py",
    "steel_dxf_split/part_mark_layout.py",
    "steel_dxf_split/release_evidence/box_release_attestation.json",
)
SOURCE_FILES = ("pyproject.toml", "README.md", "uv.lock")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a verified wheel from a clean source snapshot."
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def copy_clean_source(repository_root: Path, source_root: Path) -> None:
    for filename in SOURCE_FILES:
        shutil.copy2(repository_root / filename, source_root / filename)
    shutil.copytree(repository_root / "src", source_root / "src")


def verify_wheel(wheel_path: Path) -> None:
    with zipfile.ZipFile(wheel_path) as archive:
        names = archive.namelist()
        entry_point_files = [
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        ]
        if len(entry_point_files) != 1:
            raise ValueError("wheel must contain exactly one entry_points.txt")
        entry_points = archive.read(entry_point_files[0]).decode("utf-8")

    if entry_points != EXPECTED_ENTRY_POINTS:
        raise ValueError("wheel console scripts do not match the unified contract")
    if any(
        name.endswith(retired)
        for name in names
        for retired in RETIRED_MODULE_SUFFIXES
    ):
        raise ValueError("wheel contains a retired runtime module")

    missing_members = [member for member in REQUIRED_WHEEL_MEMBERS if member not in names]
    if missing_members:
        raise ValueError(f"wheel is missing required members: {', '.join(missing_members)}")


def build_wheel(repository_root: Path, output_dir: Path) -> Path:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required to build the clean wheel")

    with tempfile.TemporaryDirectory(prefix="steel-dxf-split-wheel-") as temporary_dir:
        temporary_root = Path(temporary_dir)
        source_root = temporary_root / "source"
        temporary_output = temporary_root / "wheel-output"
        source_root.mkdir()
        temporary_output.mkdir()
        copy_clean_source(repository_root, source_root)

        subprocess.run(
            [
                uv,
                "build",
                "--offline",
                "--no-python-downloads",
                "--wheel",
                "--no-build-logs",
                "--no-create-gitignore",
                "--out-dir",
                str(temporary_output),
                str(source_root),
            ],
            cwd=temporary_root,
            check=True,
        )

        wheels = list(temporary_output.glob("*.whl"))
        if len(wheels) != 1:
            raise ValueError("clean build must produce exactly one wheel")
        wheel = wheels[0]
        verify_wheel(wheel)

        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / wheel.name
        pending = output_dir / f".{wheel.name}.{uuid4().hex}.pending"
        try:
            shutil.copy2(wheel, pending)
            os.replace(pending, destination)
        finally:
            pending.unlink(missing_ok=True)
        return destination


def main() -> int:
    args = parse_args()
    repository_root = Path(__file__).resolve().parents[1]
    destination = build_wheel(repository_root, args.output_dir.resolve())
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
