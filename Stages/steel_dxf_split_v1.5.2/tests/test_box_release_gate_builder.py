from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from scripts.verify_box_v1_fusion import (
    build_box_release_gate,
    build_parser,
    write_box_release_artifacts,
)
from steel_dxf_split.box.provenance import (
    BOX_CORE_COMMIT,
    BOX_CORE_TAG,
    BOX_CORE_VERSION,
)
from steel_dxf_split.box.release import load_verified_box_release_attestation


def _digest(content: bytes) -> str:
    return sha256(content).hexdigest()


def _write_fixture(tmp_path: Path) -> tuple[dict[str, object], Path, Path, Path]:
    inputs = tmp_path / "inputs"
    references = tmp_path / "references"
    inputs.mkdir()
    references.mkdir()
    entries: list[dict[str, object]] = []
    samples: list[dict[str, object]] = []

    for index in range(20):
        member = f"member-{index:02d}"
        before_name = f"{member}_拆板前.dxf"
        after_name = f"{member}_拆板后.dxf"
        before_content = f"before:{member}\n".encode()
        after_content = f"after:{member}\n".encode()
        (inputs / before_name).write_bytes(before_content)
        (references / after_name).write_bytes(after_content)
        before_digest = _digest(before_content)
        after_digest = _digest(after_content)
        entries.append(
            {
                "pair_id": f"BOX_拆板_dxf/{member}",
                "before_path": f"BOX_拆板前_dxf/{before_name}",
                "after_path": f"BOX_拆板后_dxf/{after_name}",
                "before_sha256": before_digest,
                "after_sha256": after_digest,
                "partition": "calibration" if index < 10 else "acceptance",
            }
        )
        samples.append(
            {
                "member": member,
                "source_file_sha256": before_digest,
                "source_geometry_fingerprint": f"{index + 1:064x}",
                "manufacturing_fingerprint": f"{index + 101:064x}",
                "proof_disposition": "auto_accept",
                "proof_report": {
                    "disposition": "auto_accept",
                    "search_complete": True,
                    "blocking_obligation_ids": [],
                },
                "search_status": {"search_complete": True},
                "ground_truth_used_for_decision": False,
                "comparisons": [{"ok": True}],
                "saved_dxf": {"ok": True},
                "checks": {
                    "proof_auto_accept": True,
                    "search_complete": True,
                    "ground_truth_firewall": True,
                },
                "ok": True,
            }
        )

    manifest_path = tmp_path / "box_supervised_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "BOX-SUPERVISED-MANIFEST-1.0",
                "status": "frozen",
                "approved_by": "test owner",
                "approved_at": "2026-07-22T00:00:00+00:00",
                "thresholds": {
                    "contour_hausdorff_mm": 3.1,
                    "symmetric_difference_fraction": 0.002,
                    "area_relative": 0.002,
                    "zero_area_overlay_fraction": 0.0001,
                    "hole_center_mm": 0.1,
                    "hole_radius_mm": 0.01,
                    "basis": "frozen owner-approved paired DXF corpus",
                },
                "entries": entries,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report: dict[str, object] = {
        "schema": "BOX-V1-FUSION-ACCEPTANCE-1.0",
        "core": {
            "version": BOX_CORE_VERSION,
            "tag": BOX_CORE_TAG,
            "commit": BOX_CORE_COMMIT,
        },
        "compiler_imports_manual_reference": False,
        "ground_truth_used_for_decision": False,
        "missing_inputs": [],
        "missing_references": [],
        "sample_count": 20,
        "passed": 20,
        "failed": 0,
        "all_passed": True,
        "samples": samples,
        "read_only_corpus": {
            "input_file_count": 20,
            "reference_file_count": 20,
            "inputs_unchanged": True,
            "references_unchanged": True,
        },
    }
    return report, manifest_path, inputs, references


def test_release_gate_binds_frozen_manifest_and_all_twenty_verdicts(
    tmp_path: Path,
) -> None:
    report, manifest_path, inputs, references = _write_fixture(tmp_path)

    first = build_box_release_gate(report, manifest_path, inputs, references)
    second = build_box_release_gate(report, manifest_path, inputs, references)

    assert first == second
    assert first["schema"] == "BOX-V1-RELEASE-GATE-1.0"
    assert first["passed"] is True
    assert first["pair_count"] == 20
    assert first["calibration_count"] == 10
    assert first["acceptance_count"] == 10
    assert len(first["verdicts"]) == 20
    assert all(first["checks"].values())
    assert len(first["manifest_fingerprint"]) == 64
    assert len(first["gate_fingerprint"]) == 64


def test_release_gate_accepts_native_comparison_tuples(tmp_path: Path) -> None:
    report, manifest_path, inputs, references = _write_fixture(tmp_path)
    samples = report["samples"]
    assert isinstance(samples, list)
    for sample in samples:
        sample["comparisons"] = tuple(sample["comparisons"])

    gate = build_box_release_gate(report, manifest_path, inputs, references)

    assert gate["passed"] is True
    assert len(gate["verdicts"]) == 20


def test_release_gate_rejects_manifest_file_hash_drift(tmp_path: Path) -> None:
    report, manifest_path, inputs, references = _write_fixture(tmp_path)
    reference = references / "member-19_拆板后.dxf"
    reference.write_bytes(b"tampered\n")

    with pytest.raises(ValueError, match="manifest.*hash|哈希"):
        build_box_release_gate(report, manifest_path, inputs, references)


def test_release_gate_rejects_ground_truth_in_production_decision(
    tmp_path: Path,
) -> None:
    report, manifest_path, inputs, references = _write_fixture(tmp_path)
    samples = report["samples"]
    assert isinstance(samples, list)
    samples[0]["ground_truth_used_for_decision"] = True

    with pytest.raises(ValueError, match="ground.?truth|人工"):
        build_box_release_gate(report, manifest_path, inputs, references)


def test_release_gate_and_attestation_are_emitted_as_one_verified_pair(
    tmp_path: Path,
) -> None:
    report, manifest_path, inputs, references = _write_fixture(tmp_path)
    gate_path = tmp_path / "release" / "box-release-gate.json"
    attestation_path = tmp_path / "release" / "box-release-attestation.json"

    gate = write_box_release_artifacts(
        report,
        manifest_path=manifest_path,
        inputs=inputs,
        references=references,
        gate_path=gate_path,
        attestation_path=attestation_path,
    )

    assert json.loads(gate_path.read_text(encoding="utf-8")) == gate
    verified = load_verified_box_release_attestation(attestation_path)
    assert verified.pair_count == 20
    assert verified.calibration_count == 10
    assert verified.acceptance_count == 10
    assert verified.manifest_fingerprint == gate["manifest_fingerprint"]
    assert verified.gate_fingerprint == gate["gate_fingerprint"]


def test_release_cli_exposes_explicit_manifest_and_two_sidecar_paths() -> None:
    args = build_parser().parse_args(
        [
            "--inputs",
            "inputs",
            "--references",
            "references",
            "--manifest",
            "manifest.json",
            "--release-gate-output",
            "gate.json",
            "--emit-release-attestation",
            "attestation.json",
        ]
    )

    assert args.manifest == Path("manifest.json")
    assert args.release_gate_output == Path("gate.json")
    assert args.emit_release_attestation == Path("attestation.json")
