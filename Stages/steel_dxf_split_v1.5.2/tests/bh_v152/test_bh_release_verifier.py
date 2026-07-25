from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

import pytest

import scripts.bh.build_bh_release_verification as verifier
from scripts.bh.build_bh_release_verification import build_verification, main


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / "samples" / "bh_pairs"
REFERENCE_DIR = ROOT / "samples" / "bh_pairs"


def test_production_source_contains_no_corpus_part_number_branches() -> None:
    stems = {
        path.name.removesuffix("_拆板前.dxf")
        for path in SOURCE_DIR.glob("*_拆板前.dxf")
    }
    leaked: dict[str, list[str]] = {}
    for path in (ROOT / "src" / "steel_dxf_split").glob("bh_*.py"):
        text = path.read_text(encoding="utf-8")
        matches = sorted(stem for stem in stems if stem in text)
        if matches:
            leaked[path.relative_to(ROOT).as_posix()] = matches

    assert leaked == {}


def _two_pair_corpus(
    tmp_path: Path,
    stems: tuple[str, str] = ("2b1-cb-18", "2b1-cb-26"),
) -> tuple[Path, Path]:
    source_dir = tmp_path / "sources"
    reference_dir = tmp_path / "manual-references"
    source_dir.mkdir()
    reference_dir.mkdir()
    for stem in stems:
        source_name = f"{stem}_拆板前.dxf"
        manual_name = f"{stem}_拆板后.dxf"
        shutil.copy2(SOURCE_DIR / source_name, source_dir / source_name)
        shutil.copy2(
            REFERENCE_DIR / manual_name,
            reference_dir / manual_name,
        )
    return source_dir, reference_dir


@pytest.mark.skipif(os.name == "nt", reason="BH release verification is Linux-only")
def test_verifier_is_source_first_deterministic_and_explicit_about_capabilities(
    tmp_path: Path,
) -> None:
    source_dir, reference_dir = _two_pair_corpus(tmp_path)
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    mutations = (
        "translate",
        "mirror_x",
        "mirror_y",
        "uppercase_layers",
        "explode",
        "rotate90",
    )

    first = build_verification(
        source_dir,
        reference_dir,
        first_dir,
        mutations=mutations,
        repeat_count=2,
        continue_on_failure=False,
    )
    second = build_verification(
        source_dir,
        reference_dir,
        second_dir,
        mutations=mutations,
        repeat_count=2,
        continue_on_failure=False,
    )

    assert first == second
    assert str(tmp_path) not in json.dumps(first, ensure_ascii=False)
    assert first["schema"] == "BH-RELEASE-VERIFICATION-1.0"
    assert first["corpus_manifest_schema"] == "BH-CORPUS-1.0"
    assert first["corpus_manifest"] == "bh_corpus.json"
    assert first["compiler_version"] == "1.5.2"
    assert first["pair_count"] == 2
    assert first["all_originals_compiled"] is True
    assert first["all_original_supervised_ok"] is True
    assert first["all_auto_accepted_supervised_ok"] is True
    assert first["all_repeat_deterministic"] is True
    assert first["all_strict_mutations_equivalent"] is True
    assert first["all_writer_outputs_deterministic"] is True
    assert first["all_physical_routing_valid"] is True
    assert first["all_expected_routes_match"] is True
    assert first["required_release_mutations_present"] is True
    assert first["auto_supervision_gate_status"] == "passed"
    assert first["all_passed"] is True
    assert first["verification_policy"]["ground_truth_used_for_decision"] is False
    assert first["verification_policy"]["manual_read_phase"] == (
        "after_baseline_repeats_mutations_and_writer_routes_frozen"
    )

    persisted = json.loads((first_dir / "summary.json").read_text(encoding="utf-8"))
    assert persisted == first
    assert (first_dir / "summary.md").exists()
    capabilities = json.loads(
        (first_dir / "capability_matrix.json").read_text(encoding="utf-8")
    )
    assert capabilities["ground_truth_firewall"]["used_for_decision"] is False
    assert capabilities["source_contract"] | {
        "release_evidence": None,
    } == {
        "source_system": "tekla_structures",
        "drawing_kind": "single_part_drawing",
        "member_family": "welded_bh",
        "export_profile": "project_tekla_bh_dxf_v1",
        "authority": "workflow_supplied_not_inferred_from_dxf",
        "explicit_authorization_required": True,
        "runtime_enforced": True,
        "dialect_profile_must_match_contract": True,
        "verified_release_profile_ids": ["project_tekla_bh_dxf_v1"],
        "release_profile_verified": True,
        "release_evidence": None,
    }
    assert capabilities["source_contract"]["release_evidence"][
        "capability_artifact_sha256"
    ] == "243fa7d095cf9c402ffcb62ad03634b0e25b895c2fa3ea6af6004b1d5fdc2e34"
    assert "multi_member_drawings" in capabilities["unsupported_or_unverified"]
    assert "other_dxf_exporters" in capabilities["unsupported_or_unverified"]
    assert capabilities["diagnostic_only"]["arbitrary_rotation"]

    for item in first["samples"]:
        assert len(item["source_sha256"]) == 64
        assert len(item["manual_sha256"]) == 64
        assert len(item["manufacturing_fingerprint"]) == 64
        assert item["proof_disposition"] in {"auto_accept", "review_required"}
        assert isinstance(item["diagnostic_codes"], list)
        assert item["repeat_deterministic"] is True
        assert item["strict_mutations_equivalent"] is True
        assert item["writer_output_bytes_deterministic"] is True
        assert item["physical_routing_valid"] is True
        assert item["route_matches_manifest"] is True
        assert len(item["writer_output_sha256"]) == 64
        sample = json.loads(
            (first_dir / item["report_path"]).read_text(encoding="utf-8")
        )
        snapshot = sample["production_baseline"]["snapshot"]
        assert snapshot["source_contract"] == {
            "source_system": "tekla_structures",
            "drawing_kind": "single_part_drawing",
            "member_family": "welded_bh",
            "export_profile": "project_tekla_bh_dxf_v1",
        }
        assert snapshot["source_contract_enforcement"][
            "validated_before_source_ir"
        ] is True
        writer = sample["integrated_writer_verification"]
        assert writer["output_bytes_deterministic"] is True
        assert writer["physical_routing_valid"] is True
        assert writer["preview_pair_valid"] is True
        assert len({run["output_sha256"] for run in writer["runs"]}) == 1
        assert sample["post_hoc_supervision"]["used_for_decision"] is False
        assert sample["post_hoc_supervision"]["gate_policy"] == "auto_accept_only"
        assert sample["post_hoc_supervision"]["verification_gate_applicable"] == (
            item["proof_disposition"] == "auto_accept"
        )
        assert sample["post_hoc_supervision"]["comparison"]["ok"] is True
        assert sample["post_hoc_supervision"]["comparison"]["values"][
            "manual_reference"
        ] == sample["manual"]["name"]
        assert str(tmp_path) not in json.dumps(sample, ensure_ascii=False)
        by_name = {mutation["name"]: mutation for mutation in sample["mutations"]}
        for name in ("translate", "mirror_x", "uppercase_layers", "explode"):
            assert by_name[name]["contract"] == "strict_representation_invariant"
            assert by_name[name]["manufacturing_fingerprint_equal"] is True
            assert by_name[name]["proof_disposition_equal"] is True
            assert by_name[name]["source_information_semantics_equal"] is True
        assert by_name["rotate90"]["contract"] == "diagnostic_only"
        assert "diagnostic_compile" in by_name["rotate90"]


@pytest.mark.skipif(os.name == "nt", reason="BH release verification is Linux-only")
def test_verifier_cli_writes_the_stable_artifact_set(tmp_path: Path) -> None:
    source_dir, reference_dir = _two_pair_corpus(tmp_path)
    output = tmp_path / "verification"

    code = main(
        [
            "--source-dir",
            str(source_dir),
            "--reference-dir",
            str(reference_dir),
            "--output-dir",
            str(output),
            "--mutations",
            "translate,mirror_x,mirror_y,uppercase_layers,explode",
            "--repeat-count",
            "2",
            "--continue-on-failure",
        ]
    )

    assert code == 0
    assert {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()} == {
        "capability_matrix.json",
        "samples/2b1-cb-18.json",
        "samples/2b1-cb-26.json",
        "summary.json",
        "summary.md",
    }


@pytest.mark.skipif(os.name == "nt", reason="BH release verification is Linux-only")
def test_post_hoc_manual_mismatch_does_not_change_route_but_fails_release_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Manual drawings never change routing, but they can block certification."""

    class _Comparison:
        def __init__(self, ok: bool):
            self.ok = ok

        def to_dict(self) -> dict[str, object]:
            return {"ok": self.ok, "values": {"synthetic_audit": True}}

    def _compare(_assembly, manual_path: Path) -> _Comparison:
        return _Comparison("2b1-cb-40" not in manual_path.name)

    monkeypatch.setattr(verifier, "compare_bh_to_manual", _compare)
    source_dir, reference_dir = _two_pair_corpus(
        tmp_path,
        ("2b1-cb-40", "2b1-cb-26"),
    )
    summary = build_verification(
        source_dir,
        reference_dir,
        tmp_path / "verification",
        mutations=(),
        repeat_count=1,
        continue_on_failure=False,
        release_profile=False,
    )

    by_stem = {item["stem"]: item for item in summary["samples"]}
    assert by_stem["2b1-cb-40"]["proof_disposition"] == "auto_accept"
    assert by_stem["2b1-cb-40"]["supervised_ok"] is False
    assert by_stem["2b1-cb-40"]["supervision_gate_applicable"] is True
    assert by_stem["2b1-cb-26"]["proof_disposition"] == "auto_accept"
    assert by_stem["2b1-cb-26"]["supervised_ok"] is True
    assert by_stem["2b1-cb-26"]["supervision_gate_applicable"] is True
    assert summary["all_original_supervised_ok"] is False
    assert summary["all_auto_accepted_supervised_ok"] is False
    assert summary["auto_supervision_gate_status"] == "failed"
    assert summary["all_passed"] is False


def test_release_profile_rejects_missing_required_mutations(tmp_path: Path) -> None:
    source_dir, reference_dir = _two_pair_corpus(tmp_path)
    with pytest.raises(ValueError, match="Missing required release mutations"):
        build_verification(
            source_dir,
            reference_dir,
            tmp_path / "verification",
            mutations=("translate", "mirror_x"),
            repeat_count=1,
            continue_on_failure=False,
        )


@pytest.mark.skipif(os.name == "nt", reason="BH release verification is Linux-only")
def test_empty_auto_supervision_gate_is_not_reported_as_passed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _synthetic_review_sample(source_path: Path, **_kwargs) -> dict[str, object]:
        stem = source_path.name.removesuffix("_拆板前.dxf")
        return {
            "stem": stem,
            "source": {"name": source_path.name, "sha256": "a" * 64},
            "manual": {"name": f"{stem}_拆板后.dxf", "sha256": "b" * 64},
            "production_baseline": {
                "snapshot": {
                    "proof_disposition": "review_required",
                    "blocking_obligation_ids": [
                        "BH.PROOF.MANUFACTURING_IR.PROVENANCE"
                    ],
                    "manufacturing_fingerprint": "c" * 64,
                    "diagnostic_codes": [],
                }
            },
            "baseline_compiled": True,
            "repeat_deterministic": True,
            "strict_mutations_equivalent": True,
            "integrated_writer_verification": {
                "output_bytes_deterministic": True,
                "physical_routing_valid": True,
                "output_sha256": "d" * 64,
            },
            "post_hoc_supervision": {
                "comparison": {"ok": False, "values": {"synthetic": True}}
            },
        }

    monkeypatch.setattr(verifier, "_verify_sample", _synthetic_review_sample)
    source_dir, reference_dir = _two_pair_corpus(
        tmp_path,
        ("2b1-cb-40", "2b1-cb-40"),
    )
    summary = build_verification(
        source_dir,
        reference_dir,
        tmp_path / "verification",
        mutations=(),
        repeat_count=1,
        continue_on_failure=False,
        release_profile=False,
    )

    assert summary["disposition_counts"]["auto_accept"] == 0
    assert summary["all_auto_accepted_supervised_ok"] is None
    assert summary["auto_supervision_gate_status"] == "not_applicable"
    assert summary["all_passed"] is False


def test_integrated_writer_verification_detects_byte_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    records = iter(
        (
            {
                "route": "production",
                "output_sha256": "a" * 64,
                "saved_dxf_ok": True,
                "physical_routing_valid": True,
            },
            {
                "route": "production",
                "output_sha256": "b" * 64,
                "saved_dxf_ok": True,
                "physical_routing_valid": True,
            },
        )
    )
    monkeypatch.setattr(
        verifier,
        "_integrated_route_once",
        lambda _source, _output: next(records),
    )

    result = verifier._verify_integrated_writer(
        SOURCE_DIR / "2b1-cb-26_拆板前.dxf",
        1,
    )

    assert result["output_bytes_deterministic"] is False
    assert result["physical_routing_valid"] is True


def test_integrated_writer_verification_detects_production_route_leak(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        verifier,
        "_integrated_route_once",
        lambda _source, _output: {
            "route": "review_required",
            "output_sha256": "a" * 64,
            "saved_dxf_ok": True,
            "physical_routing_valid": False,
        },
    )

    result = verifier._verify_integrated_writer(
        SOURCE_DIR / "2b1-cb-40_拆板前.dxf",
        1,
    )

    assert result["output_bytes_deterministic"] is True
    assert result["physical_routing_valid"] is False
