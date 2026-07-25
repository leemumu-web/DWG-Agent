from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from steel_dxf_split.box import release
from steel_dxf_split.box.provenance import (
    BOX_CORE_COMMIT,
    BOX_CORE_TAG,
    BOX_CORE_VERSION,
)

ROOT = Path(__file__).resolve().parents[2]


def _write_attestation(path: Path) -> None:
    release.write_box_release_attestation(
        path,
        pair_count=20,
        calibration_count=10,
        acceptance_count=10,
        manifest_fingerprint="1" * 64,
        gate_fingerprint="2" * 64,
    )


def test_source_file_fingerprint_is_stable_across_checkout_line_endings(
    tmp_path: Path,
) -> None:
    lf = tmp_path / "lf.py"
    crlf = tmp_path / "crlf.py"
    lf.write_bytes(b"first = 1\nsecond = 2\n")
    crlf.write_bytes(b"first = 1\r\nsecond = 2\r\n")

    assert release._file_sha256(lf) == release._file_sha256(crlf)


def test_release_attestation_binds_internal_v1_core_and_current_implementation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "box-release-attestation.json"
    _write_attestation(path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "BOX-RELEASE-ATTESTATION-2.0"
    assert payload["certification"]["core"] == {
        "version": BOX_CORE_VERSION,
        "tag": BOX_CORE_TAG,
        "commit": BOX_CORE_COMMIT,
    }
    verified = release.load_verified_box_release_attestation(path)
    assert verified.passed is True
    assert verified.core_version == "1.0.0"
    assert verified.core_tag == "v1.0.0"
    assert verified.core_commit == BOX_CORE_COMMIT
    assert verified.release_path == path.resolve()


def test_release_attestation_defaults_to_packaged_wheel_resource() -> None:
    verified = release.load_verified_box_release_attestation()

    assert verified.passed is True
    assert verified.pair_count == 20
    assert verified.calibration_count == 10
    assert verified.acceptance_count == 10
    assert verified.release_path.name == "box_release_attestation.json"
    assert verified.release_path.parent.name == "release_evidence"


def test_release_attestation_detects_payload_tampering(tmp_path: Path) -> None:
    path = tmp_path / "box-release-attestation.json"
    _write_attestation(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["certification"]["pair_count"] = 21
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="摘要"):
        release.load_verified_box_release_attestation(path)


def test_release_attestation_uses_deterministic_lf_transport(
    tmp_path: Path,
) -> None:
    path = tmp_path / "box-release-attestation.json"

    _write_attestation(path)

    raw = path.read_bytes()
    assert b"\r" not in raw
    assert b"\n" in raw


def test_release_attestation_detects_implementation_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "box-release-attestation.json"
    _write_attestation(path)
    monkeypatch.setattr(
        release,
        "production_implementation_fingerprint",
        lambda: "0" * 64,
    )

    with pytest.raises(ValueError, match="实现代码漂移"):
        release.load_verified_box_release_attestation(path)


def test_old_v021_attestation_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "BOX-RELEASE-ATTESTATION-1.0",
                "created_at": "2026-01-01T00:00:00+00:00",
                "certification": {},
                "payload_digest": "0" * 64,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="schema"):
        release.load_verified_box_release_attestation(path)


def test_production_fingerprint_covers_internal_core_and_main_route_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[Path] = []

    def record(path: Path) -> str:
        observed.append(path.resolve())
        return "0" * 64

    monkeypatch.setattr(release, "_file_sha256", record)
    monkeypatch.setattr(
        release,
        "_load_build_contract_hashes",
        lambda _package_root: {
            "pyproject.toml": "0" * 64,
            "uv.lock": "0" * 64,
        },
    )
    payload = release.production_implementation_payload()
    paths = {entry["path"] for entry in payload["files"]}

    assert "src/steel_dxf_split/box/assembly.py" in paths
    assert "src/steel_dxf_split/box/compiler.py" in paths
    assert "src/steel_dxf_split/box/release.py" in paths
    assert "src/steel_dxf_split/pipeline.py" in paths
    assert "src/steel_dxf_split/cli.py" in paths
    assert "src/steel_dxf_split/paired_output.py" in paths
    assert "src/steel_dxf_split/profile_detection.py" in paths
    assert "src/steel_dxf_split/hole_color_policy.py" in paths
    assert "src/steel_dxf_split/part_mark_layout.py" in paths
    assert "pyproject.toml" in paths
    assert "uv.lock" in paths
    assert not any("box_supervision" in path for path in paths)
    assert not any("manual_reference" in path for path in paths)
    assert payload["core"] == {
        "version": BOX_CORE_VERSION,
        "tag": BOX_CORE_TAG,
        "commit": BOX_CORE_COMMIT,
    }
    assert observed


def test_production_fingerprint_is_stable_in_installed_package_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = release.production_implementation_fingerprint()
    installed_package = (
        tmp_path
        / "venv/lib/python3.12/site-packages/steel_dxf_split"
    )
    shutil.copytree(ROOT / "src/steel_dxf_split", installed_package)
    monkeypatch.setattr(
        release,
        "__file__",
        str(installed_package / "box/release.py"),
    )

    assert release.production_implementation_fingerprint() == expected


@pytest.mark.parametrize(
    ("pair_count", "calibration_count", "acceptance_count"),
    (
        (19, 10, 9),
        (20, 9, 11),
        (20, 11, 9),
        (21, 10, 10),
    ),
)
def test_release_writer_rejects_incomplete_or_inconsistent_counts(
    tmp_path: Path,
    pair_count: int,
    calibration_count: int,
    acceptance_count: int,
) -> None:
    with pytest.raises(ValueError, match="数量"):
        release.write_box_release_attestation(
            tmp_path / "invalid.json",
            pair_count=pair_count,
            calibration_count=calibration_count,
            acceptance_count=acceptance_count,
            manifest_fingerprint="1" * 64,
            gate_fingerprint="2" * 64,
        )
