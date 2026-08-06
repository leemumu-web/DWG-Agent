from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ConstraintStatus(StrEnum):
    PASS = "pass"
    MISSING = "missing"
    CONFLICT = "conflict"
    INCOMPLETE = "incomplete"
    NOT_APPLICABLE = "not_applicable"


class DecisionDisposition(StrEnum):
    AUTO_ACCEPT = "auto_accept"
    REVIEW_REQUIRED = "review_required"
    REJECTED = "rejected"


class FactDisposition(StrEnum):
    OWNED = "owned"
    AUXILIARY = "auxiliary"
    CONFLICT = "conflict"


class ObservationDisposition(StrEnum):
    CONSUMED = "consumed"
    CANONICAL_DUPLICATE = "canonical_duplicate"
    AUXILIARY = "auxiliary"
    CONFLICT = "conflict"


class PruneReason(StrEnum):
    HARD_CONFLICT = "hard_conflict"
    CANONICAL_DUPLICATE = "canonical_duplicate"
    PROVEN_SYMMETRY_DUPLICATE = "proven_symmetry_duplicate"


@dataclass(frozen=True, slots=True)
class RoleRequirement:
    role_key: str
    count: int


@dataclass(frozen=True, slots=True)
class RoleGroupPolicy:
    group_id: str
    role_keys: tuple[str, ...]
    allow_representation_reuse: bool
    allow_output_merge: bool


@dataclass(frozen=True, slots=True)
class DecisionPolicy:
    required_roles: tuple[RoleRequirement, ...]
    required_constraint_ids: tuple[str, ...]
    role_groups: tuple[RoleGroupPolicy, ...]


@dataclass(frozen=True, slots=True)
class SourceFactRef:
    source_fact_id: str
    channel: str
    source_key: str
    authority: str


@dataclass(frozen=True, slots=True)
class ObservationRef:
    observation_id: str
    kind_key: str
    source_fact_ids: tuple[str, ...]
    representation_id: str
    artifact_ref: str


@dataclass(frozen=True, slots=True)
class PhysicalInstance:
    instance_id: str
    role_key: str
    root_claim_id: str


@dataclass(frozen=True, slots=True)
class ManufacturingClaim:
    claim_id: str
    owner_instance_id: str
    kind_key: str
    artifact_ref: str


@dataclass(frozen=True, slots=True)
class SourceFactAccount:
    source_fact_id: str
    disposition: FactDisposition
    owner_claim_id: str | None
    authorization_constraint_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ObservationAccount:
    observation_id: str
    disposition: ObservationDisposition
    consumer_claim_ids: tuple[str, ...]
    authorization_constraint_ids: tuple[str, ...]
    equivalent_to_observation_id: str | None


@dataclass(frozen=True, slots=True)
class RepresentationReuseClaim:
    claim_id: str
    representation_id: str
    member_instance_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    proof_constraint_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EquivalenceClaim:
    equivalence_id: str
    member_instance_ids: tuple[str, ...]
    manufacturing_key: str
    proof_constraint_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MaterializationPlan:
    output_id: str
    member_instance_ids: tuple[str, ...]
    manufacturing_key: str
    quantity: int
    equivalence_id: str | None


@dataclass(frozen=True, slots=True)
class ConstraintOutcome:
    constraint_id: str
    status: ConstraintStatus
    critical: bool
    evidence_source_fact_ids: tuple[str, ...]
    evidence_observation_ids: tuple[str, ...]
    diagnostic_code: str | None


@dataclass(frozen=True, slots=True)
class PruneCertificate:
    candidate_id: str
    reason: PruneReason
    proof_constraint_ids: tuple[str, ...]
    equivalent_to_hypothesis_id: str | None


@dataclass(frozen=True, slots=True)
class SearchScope:
    scope_id: str
    parent_scope_id: str | None
    generated_candidate_ids: tuple[str, ...]
    evaluated_candidate_ids: tuple[str, ...]
    prune_certificates: tuple[PruneCertificate, ...]
    enumerator_exhausted: bool
    budget_exhausted: bool


@dataclass(frozen=True, slots=True)
class Hypothesis:
    hypothesis_id: str
    meaning_key: str
    rank_key: tuple[float, ...]
    instances: tuple[PhysicalInstance, ...]
    claims: tuple[ManufacturingClaim, ...]
    source_fact_accounts: tuple[SourceFactAccount, ...]
    observation_accounts: tuple[ObservationAccount, ...]
    representation_reuse_claims: tuple[RepresentationReuseClaim, ...]
    equivalence_claims: tuple[EquivalenceClaim, ...]
    materializations: tuple[MaterializationPlan, ...]
    constraints: tuple[ConstraintOutcome, ...]


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    policy: DecisionPolicy
    source_facts: tuple[SourceFactRef, ...]
    observations: tuple[ObservationRef, ...]
    hypotheses: tuple[Hypothesis, ...]
    search_scopes: tuple[SearchScope, ...]


@dataclass(frozen=True, slots=True)
class DecisionIssue:
    code: str
    critical: bool
    hypothesis_id: str | None
    source_fact_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    constraint_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DecisionResult:
    disposition: DecisionDisposition
    selected_hypothesis_id: str | None
    admissible_hypothesis_ids: tuple[str, ...]
    authorized_merge_claim_ids: tuple[str, ...]
    search_complete: bool
    issues: tuple[DecisionIssue, ...]
    audit_digest: str
