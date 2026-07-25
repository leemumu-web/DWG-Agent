from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import hashlib
from importlib.resources import files
import json
import re
from typing import Any

from . import __version__
from .bh_dialect import BHDialectProfile
from .bh_knowledge import BHSourceContract


@dataclass(frozen=True, slots=True)
class BHReleaseEvidence:
    profile_id: str
    compiler_version: str
    ontology_version: str
    dialect_fingerprint: str
    source_corpus_sha256: str
    source_count: int
    capability_artifact: str
    capability_artifact_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_TRUSTED_ARTIFACTS = {
    "project_tekla_bh_dxf_v1": (
        "release_evidence/project_tekla_bh_dxf_v1.json",
        "243fa7d095cf9c402ffcb62ad03634b0e25b895c2fa3ea6af6004b1d5fdc2e34",
    ),
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_STRICT_MUTATIONS = frozenset(
    {"translate", "mirror_x", "mirror_y", "uppercase_layers", "explode"}
)
_DECLARED_PRIOR_ONTOLOGY = {"BH-MFG-3.1": "BH-MFG-3.0"}


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def dialect_fingerprint(dialect: BHDialectProfile) -> str:
    payload = {
        "profile_id": dialect.profile_id,
        "dimension_origin_offset_text_ratio": dialect.dimension_origin_offset_text_ratio,
        "dimension_origin_offset_tolerance_mm": dialect.dimension_origin_offset_tolerance_mm,
        "hidden_projection_linetypes": sorted({
            item.strip().casefold()
            for item in dialect.hidden_projection_linetypes
        }),
        "rules": [
            {
                "role": rule.role.value,
                "layers": list(rule.layers),
                "entity_types": list(rule.entity_types),
            }
            for rule in dialect.rules
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def trusted_release_profile_ids() -> tuple[str, ...]:
    return tuple(sorted(_TRUSTED_ARTIFACTS))


def _resolve_pinned_release_evidence(
    contract: BHSourceContract,
    dialect: BHDialectProfile,
    ontology_version: str,
    *,
    require_current_compiler: bool,
) -> BHReleaseEvidence | None:
    registered = _TRUSTED_ARTIFACTS.get(contract.export_profile)
    if registered is None:
        return None
    resource_name, expected_digest = registered
    artifact = files("steel_dxf_split").joinpath(resource_name)
    try:
        content = artifact.read_bytes()
    except OSError:
        return None
    actual_digest = hashlib.sha256(content).hexdigest()
    if actual_digest != expected_digest:
        return None
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    expected_contract = {
        "source_system": contract.source_system,
        "drawing_kind": contract.drawing_kind,
        "member_family": contract.member_family,
        "export_profile": contract.export_profile,
    }
    if (
        payload.get("schema") != "BH-RELEASE-CAPABILITY-1.0"
        or payload.get("source_contract") != expected_contract
        or payload.get("profile_id") != dialect.profile_id
        or payload.get("dialect_fingerprint") != dialect_fingerprint(dialect)
        or payload.get("ontology_version") != ontology_version
    ):
        return None
    compiler_version = payload.get("compiler_version")
    if not isinstance(compiler_version, str):
        return None
    if require_current_compiler and compiler_version != __version__:
        return None
    corpus = payload.get("source_corpus")
    if not isinstance(corpus, dict):
        return None
    source_digest = corpus.get("source_set_sha256")
    source_count = corpus.get("source_count")
    capabilities = payload.get("verified_capabilities")
    if (
        not _is_sha256(source_digest)
        or type(source_count) is not int
        or source_count <= 0
        or corpus.get("manual_result_used_for_decision") is not False
        or not isinstance(capabilities, dict)
        or capabilities.get("production_member_axis") != "horizontal_x"
        or capabilities.get("automation_policy")
        != "all_applicable_critical_proofs_pass"
        or capabilities.get("unresolved_inputs_are_quarantined") is not True
    ):
        return None
    strict_mutations = capabilities.get("strict_representation_invariants")
    routes = capabilities.get("source_only_routes")
    if (
        not isinstance(strict_mutations, list)
        or not all(isinstance(item, str) for item in strict_mutations)
        or len(strict_mutations) != len(_REQUIRED_STRICT_MUTATIONS)
        or set(strict_mutations) != _REQUIRED_STRICT_MUTATIONS
        or not isinstance(routes, dict)
        or set(routes) != {"auto_accept", "review_required", "rejected"}
        or not all(type(value) is int and value >= 0 for value in routes.values())
        or sum(routes.values()) != source_count
        or routes.get("rejected") != 0
    ):
        return None
    return BHReleaseEvidence(
        profile_id=dialect.profile_id,
        compiler_version=compiler_version,
        ontology_version=ontology_version,
        dialect_fingerprint=dialect_fingerprint(dialect),
        source_corpus_sha256=source_digest,
        source_count=source_count,
        capability_artifact=resource_name,
        capability_artifact_sha256=actual_digest,
    )


def resolve_release_evidence(
    contract: BHSourceContract,
    dialect: BHDialectProfile,
    ontology_version: str,
) -> BHReleaseEvidence | None:
    """Resolve current code-pinned evidence; configuration cannot extend trust."""

    return _resolve_pinned_release_evidence(
        contract,
        dialect,
        ontology_version,
        require_current_compiler=True,
    )


def resolve_prior_release_evidence_for_candidate(
    contract: BHSourceContract,
    dialect: BHDialectProfile,
    ontology_version: str,
) -> BHReleaseEvidence | None:
    """Resolve the declared prior ontology for an isolated candidate run.

    Only an explicitly registered ontology transition may use this path.  The
    pinned artifact digest, source contract, dialect fingerprint and source
    corpus binding remain mandatory.  Production proof construction never
    calls this resolver.
    """

    prior_ontology = _DECLARED_PRIOR_ONTOLOGY.get(ontology_version)
    if prior_ontology is None:
        return None

    return _resolve_pinned_release_evidence(
        contract,
        dialect,
        prior_ontology,
        require_current_compiler=False,
    )


def build_release_capability_payload(
    summary: Mapping[str, Any],
    *,
    contract: BHSourceContract,
    dialect: BHDialectProfile,
    ontology_version: str,
) -> dict[str, Any]:
    """Promote a complete candidate summary into a code-pinnable trust artifact."""

    contract.validate(dialect)

    required_true = (
        "all_passed",
        "all_originals_compiled",
        "all_auto_accepted_supervised_ok",
        "all_repeat_deterministic",
        "all_strict_mutations_equivalent",
        "all_writer_outputs_deterministic",
        "all_physical_routing_valid",
        "all_expected_routes_match",
        "required_release_mutations_present",
    )
    failed = [name for name in required_true if summary.get(name) is not True]
    policy = summary.get("verification_policy")
    counts = summary.get("disposition_counts")
    samples = summary.get("samples")
    if (
        summary.get("schema") != "BH-RELEASE-VERIFICATION-1.0"
        or summary.get("compiler_version") != __version__
        or summary.get("pair_count") != 20
        or failed
        or not isinstance(policy, Mapping)
        or policy.get("release_trust_mode") != "prior_version_candidate"
        or policy.get("release_evidence_corpus_match") is not True
        or policy.get("ground_truth_used_for_decision") is not False
        or policy.get("manual_read_phase")
        != "after_baseline_repeats_mutations_and_writer_routes_frozen"
        or not isinstance(counts, Mapping)
        or counts.get("rejected_or_error") != 0
        or not isinstance(samples, list)
        or len(samples) != 20
    ):
        detail = ", ".join(failed) if failed else "summary contract mismatch"
        raise ValueError(
            "Release evidence requires the complete 20-source candidate gate: "
            + detail
        )
    source_digest = policy.get("current_source_corpus_sha256")
    strict_mutations = policy.get("strict_mutations")
    if (
        not _is_sha256(source_digest)
        or not isinstance(strict_mutations, list)
        or not all(isinstance(item, str) for item in strict_mutations)
    ):
        raise ValueError("Release summary has invalid corpus or mutation evidence.")
    if (
        len(strict_mutations) != len(_REQUIRED_STRICT_MUTATIONS)
        or set(strict_mutations) != _REQUIRED_STRICT_MUTATIONS
    ):
        raise ValueError(
            "Release summary is missing required strict representation mutations."
        )
    route_counts = {
        "auto_accept": counts.get("auto_accept"),
        "review_required": counts.get("review_required"),
        "rejected": counts.get("rejected_or_error"),
    }
    if (
        not all(type(value) is int and value >= 0 for value in route_counts.values())
        or sum(route_counts.values()) != 20
    ):
        raise ValueError("Release summary has invalid source-only route counts.")
    if not all(isinstance(item, Mapping) for item in samples):
        raise ValueError("Release summary samples must be objects.")
    sample_stems = [item.get("stem") for item in samples]
    sample_source_hashes = [item.get("source_sha256") for item in samples]
    if (
        not all(isinstance(item, str) and item for item in sample_stems)
        or len(set(sample_stems)) != 20
        or not all(_is_sha256(item) for item in sample_source_hashes)
        or len(set(sample_source_hashes)) != 20
    ):
        raise ValueError("Release summary must bind 20 distinct source hashes.")
    sample_manual_hashes = [item.get("manual_sha256") for item in samples]
    if not all(_is_sha256(item) for item in sample_manual_hashes):
        raise ValueError("Release summary must bind 20 post-hoc manual hashes.")
    sample_dispositions = [item.get("proof_disposition") for item in samples]
    if (
        not all(item.get("route_matches_manifest") is True for item in samples)
        or any(
            disposition not in {"auto_accept", "review_required"}
            for disposition in sample_dispositions
        )
        or any(
            item.get("supervision_gate_applicable")
            is not (item.get("proof_disposition") == "auto_accept")
            for item in samples
        )
        or any(
            item.get("supervised_ok") is not True
            for item in samples
            if item.get("proof_disposition") == "auto_accept"
        )
        or sample_dispositions.count("auto_accept") != route_counts["auto_accept"]
        or sample_dispositions.count("review_required")
        != route_counts["review_required"]
    ):
        raise ValueError("Release summary has inconsistent per-sample route gates.")
    return {
        "schema": "BH-RELEASE-CAPABILITY-1.0",
        "profile_id": dialect.profile_id,
        "compiler_version": __version__,
        "ontology_version": ontology_version,
        "dialect_fingerprint": dialect_fingerprint(dialect),
        "source_contract": {
            "source_system": contract.source_system,
            "drawing_kind": contract.drawing_kind,
            "member_family": contract.member_family,
            "export_profile": contract.export_profile,
        },
        "source_corpus": {
            "source_count": 20,
            "digest_algorithm": "sha256-canonical-name-and-content-sha256-v1",
            "source_set_sha256": source_digest,
            "manual_result_used_for_decision": False,
        },
        "verified_capabilities": {
            "production_member_axis": policy.get("production_member_axis"),
            "strict_representation_invariants": strict_mutations,
            "source_only_routes": route_counts,
            "automation_policy": "all_applicable_critical_proofs_pass",
            "unresolved_inputs_are_quarantined": True,
        },
        "evidence_note": (
            "Generated only from a complete source-first candidate summary; "
            "manual split drawings were post-hoc diagnostics. The artifact's "
            "exact SHA-256 is pinned in compiler code."
        ),
    }
