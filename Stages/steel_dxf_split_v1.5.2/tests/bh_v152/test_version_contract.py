from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import steel_dxf_split.bh_release_evidence as release_evidence_module
from steel_dxf_split import __version__
from steel_dxf_split.bh_knowledge import (
    DEFAULT_BH_KNOWLEDGE,
    DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
)
from steel_dxf_split.bh_release_evidence import (
    BHReleaseEvidence,
    build_release_capability_payload,
    dialect_fingerprint,
    resolve_release_evidence,
    resolve_prior_release_evidence_for_candidate,
)


ROOT = Path(__file__).resolve().parents[2]


def test_version_sources_are_identical() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert (
        pyproject["project"]["version"]
        == __version__
        == (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        == "1.5.2"
    )


def test_release_evidence_binds_current_compiler_and_twenty_sources() -> None:
    assert DEFAULT_BH_KNOWLEDGE.ontology_version == "BH-MFG-3.1"
    evidence = resolve_release_evidence(
        DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        DEFAULT_BH_KNOWLEDGE.dialect,
        DEFAULT_BH_KNOWLEDGE.ontology_version,
    )

    assert evidence is not None
    assert evidence.compiler_version == __version__
    assert evidence.source_count == 20


def test_candidate_bootstrap_allows_only_the_declared_prior_ontology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []
    prior = BHReleaseEvidence(
        profile_id=DEFAULT_BH_KNOWLEDGE.dialect.profile_id,
        compiler_version=__version__,
        ontology_version="BH-MFG-3.0",
        dialect_fingerprint="a" * 64,
        source_corpus_sha256="b" * 64,
        source_count=20,
        capability_artifact="release_evidence/prior.json",
        capability_artifact_sha256="c" * 64,
    )

    def resolve_prior(
        _contract,
        _dialect,
        ontology_version: str,
        *,
        require_current_compiler: bool,
    ) -> BHReleaseEvidence | None:
        calls.append((ontology_version, require_current_compiler))
        return prior if ontology_version == "BH-MFG-3.0" else None

    monkeypatch.setattr(
        release_evidence_module,
        "_resolve_pinned_release_evidence",
        resolve_prior,
    )

    assert (
        resolve_prior_release_evidence_for_candidate(
            DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
            DEFAULT_BH_KNOWLEDGE.dialect,
            "BH-MFG-3.1",
        )
        is prior
    )
    assert (
        resolve_prior_release_evidence_for_candidate(
            DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
            DEFAULT_BH_KNOWLEDGE.dialect,
            "BH-MFG-9.9",
        )
        is None
    )
    assert calls == [("BH-MFG-3.0", False)]


def test_production_resolver_never_bootstraps_from_prior_ontology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []

    def resolve_pinned(
        _contract,
        _dialect,
        ontology_version: str,
        *,
        require_current_compiler: bool,
    ) -> BHReleaseEvidence | None:
        calls.append((ontology_version, require_current_compiler))
        return None

    monkeypatch.setattr(
        release_evidence_module,
        "_resolve_pinned_release_evidence",
        resolve_pinned,
    )

    assert (
        resolve_release_evidence(
            DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
            DEFAULT_BH_KNOWLEDGE.dialect,
            "BH-MFG-3.1",
        )
        is None
    )
    assert calls == [("BH-MFG-3.1", True)]


def test_dialect_fingerprint_binds_hidden_projection_semantics() -> None:
    changed = DEFAULT_BH_KNOWLEDGE.dialect.__class__(
        profile_id=DEFAULT_BH_KNOWLEDGE.dialect.profile_id,
        rules=DEFAULT_BH_KNOWLEDGE.dialect.rules,
        hidden_projection_linetypes=("XKITLINE04",),
        dimension_origin_offset_text_ratio=(
            DEFAULT_BH_KNOWLEDGE.dialect.dimension_origin_offset_text_ratio
        ),
        dimension_origin_offset_tolerance_mm=(
            DEFAULT_BH_KNOWLEDGE.dialect.dimension_origin_offset_tolerance_mm
        ),
    )

    assert dialect_fingerprint(changed) != dialect_fingerprint(
        DEFAULT_BH_KNOWLEDGE.dialect
    )


def _complete_candidate_summary() -> dict[str, object]:
    return {
        "schema": "BH-RELEASE-VERIFICATION-1.0",
        "compiler_version": "1.5.2",
        "pair_count": 20,
        "all_passed": True,
        "all_originals_compiled": True,
        "all_auto_accepted_supervised_ok": True,
        "all_repeat_deterministic": True,
        "all_strict_mutations_equivalent": True,
        "all_writer_outputs_deterministic": True,
        "all_physical_routing_valid": True,
        "all_expected_routes_match": True,
        "required_release_mutations_present": True,
        "disposition_counts": {
            "auto_accept": 20,
            "review_required": 0,
            "rejected_or_error": 0,
        },
        "verification_policy": {
            "release_trust_mode": "prior_version_candidate",
            "release_evidence_corpus_match": True,
            "ground_truth_used_for_decision": False,
            "manual_read_phase": (
                "after_baseline_repeats_mutations_and_writer_routes_frozen"
            ),
            "current_source_corpus_sha256": "a" * 64,
            "production_member_axis": "horizontal_x",
            "strict_mutations": [
                "explode",
                "mirror_x",
                "mirror_y",
                "translate",
                "uppercase_layers",
            ],
        },
        "samples": [
            {
                "stem": f"sample-{index:02d}",
                "source_sha256": f"{index:064x}",
                "manual_sha256": f"{index + 20:064x}",
                "proof_disposition": "auto_accept",
                "supervised_ok": True,
                "supervision_gate_applicable": True,
                "route_matches_manifest": True,
            }
            for index in range(20)
        ],
    }


def test_release_payload_is_derived_from_a_complete_candidate_summary() -> None:
    payload = build_release_capability_payload(
        _complete_candidate_summary(),
        contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        dialect=DEFAULT_BH_KNOWLEDGE.dialect,
        ontology_version=DEFAULT_BH_KNOWLEDGE.ontology_version,
    )

    assert payload["compiler_version"] == "1.5.2"
    assert payload["source_corpus"]["source_count"] == 20
    assert payload["source_corpus"]["manual_result_used_for_decision"] is False
    assert payload["verified_capabilities"]["source_only_routes"] == {
        "auto_accept": 20,
        "review_required": 0,
        "rejected": 0,
    }


def test_release_payload_rejects_an_incomplete_candidate_gate() -> None:
    summary = _complete_candidate_summary()
    summary["all_writer_outputs_deterministic"] = False

    with pytest.raises(ValueError, match="complete 20-source candidate gate"):
        build_release_capability_payload(
            summary,
            contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
            dialect=DEFAULT_BH_KNOWLEDGE.dialect,
            ontology_version=DEFAULT_BH_KNOWLEDGE.ontology_version,
        )


def test_release_payload_recomputes_the_required_mutation_gate() -> None:
    summary = _complete_candidate_summary()
    policy = summary["verification_policy"]
    assert isinstance(policy, dict)
    policy["strict_mutations"] = [
        "translate",
        "mirror_x",
        "mirror_y",
        "uppercase_layers",
    ]

    with pytest.raises(ValueError, match="strict representation mutations"):
        build_release_capability_payload(
            summary,
            contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
            dialect=DEFAULT_BH_KNOWLEDGE.dialect,
            ontology_version=DEFAULT_BH_KNOWLEDGE.ontology_version,
        )
