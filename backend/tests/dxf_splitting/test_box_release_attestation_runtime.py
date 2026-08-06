from __future__ import annotations

import compileall
import json
import py_compile
import shutil
from pathlib import Path

import pytest
from steel_dxf_split.box import release as box_release
from steel_dxf_split.box.release import (
    load_verified_box_release_attestation,
    production_implementation_fingerprint,
)


def test_packaged_box_release_attestation_matches_current_runtime() -> None:
    verified = load_verified_box_release_attestation()

    assert verified.passed is True
    assert verified.pair_count == 20
    assert verified.calibration_count == 10
    assert verified.acceptance_count == 10
    assert (
        verified.implementation_fingerprint
        == production_implementation_fingerprint()
    )


def test_box_release_fingerprint_covers_neutral_decision_kernel() -> None:
    payload = box_release.production_implementation_payload()
    entries = payload["files"]
    assert isinstance(entries, list)
    paths = {
        entry["path"]
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    }

    assert {
        "src/steel_dxf_split/manufacturing_decision/__init__.py",
        "src/steel_dxf_split/manufacturing_decision/engine.py",
        "src/steel_dxf_split/manufacturing_decision/errors.py",
        "src/steel_dxf_split/manufacturing_decision/model.py",
    } <= paths


def test_packaged_box_release_attestation_accepts_verified_bytecode_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root = Path(box_release.__file__).resolve().parents[1]
    package_root = tmp_path / "steel_dxf_split"
    shutil.copytree(
        source_root,
        package_root,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    monkeypatch.setattr(box_release, "_package_root", lambda: package_root)
    assert compileall.compile_dir(
        package_root,
        quiet=1,
        force=True,
        legacy=True,
        invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
    )

    box_release.write_protected_runtime_manifest()
    for source in package_root.rglob("*.py"):
        source.unlink()

    verified = load_verified_box_release_attestation(
        package_root / "release_evidence" / "box_release_attestation.json",
    )

    assert verified.passed is True
    assert verified.implementation_fingerprint == (
        production_implementation_fingerprint()
    )

    manifest_path = (
        package_root
        / "release_evidence"
        / "box_protected_runtime_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    compiled_logical = manifest["compiled_files"][0]["path"]
    compiled_path = package_root.joinpath(
        *Path(compiled_logical).parts[2:],
    )
    compiled_path.write_bytes(compiled_path.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="字节码缺失或漂移"):
        production_implementation_fingerprint()
