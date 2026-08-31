from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import fields, is_dataclass
from hashlib import sha256
import json
from math import isfinite
from typing import TypeVar

from .errors import DecisionContractError
from .model import (
    ConstraintOutcome,
    ConstraintStatus,
    DecisionDisposition,
    DecisionIssue,
    DecisionPolicy,
    DecisionRequest,
    DecisionResult,
    FactDisposition,
    Hypothesis,
    ObservationDisposition,
    PruneReason,
)


T = TypeVar("T")


def _unique_by_id(
    items: Iterable[T], id_of: Callable[[T], str], kind: str
) -> dict[str, T]:
    table: dict[str, T] = {}
    for item in items:
        item_id = id_of(item)
        if item_id in table:
            raise DecisionContractError("DUPLICATE_ID", f"duplicate {kind}: {item_id}")
        table[item_id] = item
    return table


def _require_reference(reference: str, table: Mapping[str, object], kind: str) -> None:
    if reference not in table:
        raise DecisionContractError(
            "UNKNOWN_REFERENCE", f"unknown {kind}: {reference}"
        )


def _validate_request(request: DecisionRequest) -> None:
    role_requirements = _unique_by_id(
        request.policy.required_roles, lambda role: role.role_key, "role requirement"
    )
    for role in role_requirements.values():
        if role.count <= 0:
            raise DecisionContractError(
                "INVALID_CARDINALITY", f"invalid role cardinality: {role.role_key}"
            )
    role_groups = _unique_by_id(
        request.policy.role_groups, lambda group: group.group_id, "role group"
    )
    for group in role_groups.values():
        for role_key in group.role_keys:
            _require_reference(role_key, role_requirements, "role requirement")

    source_facts = _unique_by_id(
        request.source_facts, lambda fact: fact.source_fact_id, "source fact"
    )
    observations = _unique_by_id(
        request.observations, lambda observation: observation.observation_id, "observation"
    )
    representations = {
        observation.representation_id: observation for observation in observations.values()
    }
    hypotheses = _unique_by_id(
        request.hypotheses, lambda hypothesis: hypothesis.hypothesis_id, "hypothesis"
    )
    scopes = _unique_by_id(
        request.search_scopes, lambda scope: scope.scope_id, "search scope"
    )

    for observation in observations.values():
        for source_fact_id in observation.source_fact_ids:
            _require_reference(source_fact_id, source_facts, "source fact")

    for hypothesis in hypotheses.values():
        instances = _unique_by_id(
            hypothesis.instances, lambda instance: instance.instance_id, "instance"
        )
        claims = _unique_by_id(hypothesis.claims, lambda claim: claim.claim_id, "claim")
        source_fact_accounts = _unique_by_id(
            hypothesis.source_fact_accounts,
            lambda account: account.source_fact_id,
            "source fact account",
        )
        observation_accounts = _unique_by_id(
            hypothesis.observation_accounts,
            lambda account: account.observation_id,
            "observation account",
        )
        reuse_claims = _unique_by_id(
            hypothesis.representation_reuse_claims,
            lambda claim: claim.claim_id,
            "representation reuse claim",
        )
        equivalence_claims = _unique_by_id(
            hypothesis.equivalence_claims,
            lambda claim: claim.equivalence_id,
            "equivalence claim",
        )
        materializations = _unique_by_id(
            hypothesis.materializations,
            lambda materialization: materialization.output_id,
            "materialization",
        )
        constraints = _unique_by_id(
            hypothesis.constraints,
            lambda constraint: constraint.constraint_id,
            "constraint",
        )

        for instance in instances.values():
            _require_reference(instance.role_key, role_requirements, "role requirement")
            _require_reference(instance.root_claim_id, claims, "claim")
        for claim in claims.values():
            _require_reference(claim.owner_instance_id, instances, "instance")
        for account in source_fact_accounts.values():
            _require_reference(account.source_fact_id, source_facts, "source fact")
            if account.owner_claim_id is not None:
                _require_reference(account.owner_claim_id, claims, "claim")
            for constraint_id in account.authorization_constraint_ids:
                _require_reference(constraint_id, constraints, "constraint")
        for account in observation_accounts.values():
            _require_reference(account.observation_id, observations, "observation")
            for claim_id in account.consumer_claim_ids:
                _require_reference(claim_id, claims, "claim")
            for constraint_id in account.authorization_constraint_ids:
                _require_reference(constraint_id, constraints, "constraint")
            if account.equivalent_to_observation_id is not None:
                _require_reference(
                    account.equivalent_to_observation_id, observations, "observation"
                )
        for claim in reuse_claims.values():
            _require_reference(
                claim.representation_id, representations, "observation representation"
            )
            for instance_id in claim.member_instance_ids:
                _require_reference(instance_id, instances, "instance")
            for observation_id in claim.observation_ids:
                _require_reference(observation_id, observations, "observation")
            for constraint_id in claim.proof_constraint_ids:
                _require_reference(constraint_id, constraints, "constraint")
        for claim in equivalence_claims.values():
            for instance_id in claim.member_instance_ids:
                _require_reference(instance_id, instances, "instance")
            for constraint_id in claim.proof_constraint_ids:
                _require_reference(constraint_id, constraints, "constraint")
        for materialization in materializations.values():
            for instance_id in materialization.member_instance_ids:
                _require_reference(instance_id, instances, "instance")
            if materialization.equivalence_id is not None:
                _require_reference(
                    materialization.equivalence_id, equivalence_claims, "equivalence claim"
                )
        for constraint in constraints.values():
            for source_fact_id in constraint.evidence_source_fact_ids:
                _require_reference(source_fact_id, source_facts, "source fact")
            for observation_id in constraint.evidence_observation_ids:
                _require_reference(observation_id, observations, "observation")
        for rank in hypothesis.rank_key:
            if not isfinite(rank):
                raise DecisionContractError(
                    "NON_FINITE_RANK", f"non-finite hypothesis rank: {hypothesis.hypothesis_id}"
                )

    for scope in scopes.values():
        if scope.parent_scope_id is not None:
            _require_reference(scope.parent_scope_id, scopes, "search scope")
        generated = _unique_by_id(
            scope.generated_candidate_ids, lambda candidate_id: candidate_id, "generated candidate"
        )
        evaluated = _unique_by_id(
            scope.evaluated_candidate_ids, lambda candidate_id: candidate_id, "evaluated candidate"
        )
        certificates = _unique_by_id(
            scope.prune_certificates,
            lambda certificate: certificate.candidate_id,
            "prune certificate candidate",
        )
        for candidate_id in generated:
            _require_reference(candidate_id, hypotheses, "hypothesis")
        for candidate_id in evaluated:
            _require_reference(candidate_id, generated, "generated candidate")
        for candidate_id, certificate in certificates.items():
            _require_reference(candidate_id, generated, "generated candidate")
            if candidate_id in evaluated:
                raise DecisionContractError(
                    "INVALID_REFERENCE", f"pruned candidate is evaluated: {candidate_id}"
                )
            _require_reference(certificate.candidate_id, hypotheses, "hypothesis")
            if certificate.equivalent_to_hypothesis_id is not None:
                _require_reference(
                    certificate.equivalent_to_hypothesis_id, hypotheses, "hypothesis"
                )
            for constraint_id in certificate.proof_constraint_ids:
                if not any(
                    constraint_id == constraint.constraint_id
                    for hypothesis in hypotheses.values()
                    for constraint in hypothesis.constraints
                ):
                    raise DecisionContractError(
                        "UNKNOWN_REFERENCE", f"unknown constraint: {constraint_id}"
                    )


def _issue(
    code: str,
    hypothesis: Hypothesis,
    *,
    source_fact_ids: tuple[str, ...] = (),
    observation_ids: tuple[str, ...] = (),
    constraint_ids: tuple[str, ...] = (),
) -> DecisionIssue:
    return DecisionIssue(
        code=code,
        critical=True,
        hypothesis_id=hypothesis.hypothesis_id,
        source_fact_ids=tuple(sorted(source_fact_ids)),
        observation_ids=tuple(sorted(observation_ids)),
        constraint_ids=tuple(sorted(constraint_ids)),
    )


def _passing_constraints(hypothesis: Hypothesis) -> dict[str, ConstraintOutcome]:
    return {
        constraint.constraint_id: constraint
        for constraint in hypothesis.constraints
        if constraint.status is ConstraintStatus.PASS
    }


def _has_source_evidence(constraint: ConstraintOutcome) -> bool:
    return bool(constraint.evidence_source_fact_ids)


def _has_evidence(constraint: ConstraintOutcome) -> bool:
    return bool(
        constraint.evidence_source_fact_ids or constraint.evidence_observation_ids
    )


def _all_source_proven(
    constraint_ids: tuple[str, ...], hypothesis: Hypothesis
) -> bool:
    constraints = {
        constraint.constraint_id: constraint for constraint in hypothesis.constraints
    }
    return bool(constraint_ids) and all(
        constraint_ids_item in constraints
        and constraints[constraint_ids_item].status is ConstraintStatus.PASS
        and _has_source_evidence(constraints[constraint_ids_item])
        for constraint_ids_item in constraint_ids
    )


def _role_issues(policy: DecisionPolicy, hypothesis: Hypothesis) -> tuple[DecisionIssue, ...]:
    counts: dict[str, int] = {}
    for instance in hypothesis.instances:
        counts[instance.role_key] = counts.get(instance.role_key, 0) + 1
    return tuple(
        _issue("ROLE_CARDINALITY_CONFLICT", hypothesis)
        for requirement in policy.required_roles
        if counts.get(requirement.role_key, 0) != requirement.count
    )


def _source_fact_issues(
    request: DecisionRequest, hypothesis: Hypothesis
) -> tuple[DecisionIssue, ...]:
    accounts = {
        account.source_fact_id: account for account in hypothesis.source_fact_accounts
    }
    claims = {claim.claim_id for claim in hypothesis.claims}
    passing = _passing_constraints(hypothesis)
    issues: list[DecisionIssue] = []
    for fact in request.source_facts:
        account = accounts.get(fact.source_fact_id)
        if account is None:
            issues.append(
                _issue(
                    "SOURCE_FACT_UNACCOUNTED",
                    hypothesis,
                    source_fact_ids=(fact.source_fact_id,),
                )
            )
        elif account.disposition is FactDisposition.OWNED and account.owner_claim_id not in claims:
            issues.append(
                _issue(
                    "SOURCE_FACT_OWNERSHIP_INVALID",
                    hypothesis,
                    source_fact_ids=(fact.source_fact_id,),
                )
            )
        elif account.disposition is FactDisposition.AUXILIARY:
            authorized = any(
                constraint_id in passing and _has_evidence(passing[constraint_id])
                for constraint_id in account.authorization_constraint_ids
            )
            if not authorized:
                issues.append(_issue(
                    "AUXILIARY_CLASSIFICATION_UNPROVEN", hypothesis,
                    source_fact_ids=(fact.source_fact_id,),
                    constraint_ids=account.authorization_constraint_ids,
                ))
        elif account.disposition is FactDisposition.CONFLICT:
            issues.append(
                _issue(
                    "SOURCE_FACT_CONFLICT",
                    hypothesis,
                    source_fact_ids=(fact.source_fact_id,),
                )
            )
    return tuple(issues)


def _observation_issues(
    request: DecisionRequest, hypothesis: Hypothesis
) -> tuple[DecisionIssue, ...]:
    accounts = {
        account.observation_id: account for account in hypothesis.observation_accounts
    }
    passing = _passing_constraints(hypothesis)
    issues: list[DecisionIssue] = []
    for observation in request.observations:
        account = accounts.get(observation.observation_id)
        if account is None:
            issues.append(
                _issue(
                    "OBSERVATION_UNACCOUNTED",
                    hypothesis,
                    observation_ids=(observation.observation_id,),
                )
            )
        elif account.disposition is ObservationDisposition.CONSUMED:
            if not account.consumer_claim_ids:
                issues.append(
                    _issue(
                        "OBSERVATION_CONSUMER_MISSING",
                        hypothesis,
                        observation_ids=(observation.observation_id,),
                    )
                )
        elif account.disposition is ObservationDisposition.AUXILIARY:
            authorized = any(
                constraint_id in passing and _has_evidence(passing[constraint_id])
                for constraint_id in account.authorization_constraint_ids
            )
            if not authorized:
                issues.append(
                    _issue(
                        "AUXILIARY_CLASSIFICATION_UNPROVEN",
                        hypothesis,
                        observation_ids=(observation.observation_id,),
                        constraint_ids=account.authorization_constraint_ids,
                    )
                )
        elif account.disposition is ObservationDisposition.CANONICAL_DUPLICATE:
            target = accounts.get(account.equivalent_to_observation_id or "")
            target_is_consumed = (
                target is not None
                and target.disposition is ObservationDisposition.CONSUMED
                and bool(target.consumer_claim_ids)
            )
            authorized = any(
                constraint_id in passing and _has_evidence(passing[constraint_id])
                for constraint_id in account.authorization_constraint_ids
            )
            if (
                account.equivalent_to_observation_id == observation.observation_id
                or not target_is_consumed
                or not authorized
            ):
                issues.append(
                    _issue(
                        "OBSERVATION_DUPLICATE_UNPROVEN",
                        hypothesis,
                        observation_ids=(observation.observation_id,),
                        constraint_ids=account.authorization_constraint_ids,
                    )
                )
        elif account.disposition is ObservationDisposition.CONFLICT:
            issues.append(
                _issue(
                    "OBSERVATION_CONFLICT",
                    hypothesis,
                    observation_ids=(observation.observation_id,),
                )
            )
    return tuple(issues)


def _reuse_issues(
    request: DecisionRequest, policy: DecisionPolicy, hypothesis: Hypothesis
) -> tuple[DecisionIssue, ...]:
    observations = {
        observation.observation_id: observation for observation in request.observations
    }
    claim_owners = {
        claim.claim_id: claim.owner_instance_id for claim in hypothesis.claims
    }
    instances = {instance.instance_id: instance for instance in hypothesis.instances}
    owners_by_representation: dict[str, set[str]] = {}
    observation_ids_by_representation: dict[str, set[str]] = {}
    for account in hypothesis.observation_accounts:
        if account.disposition is not ObservationDisposition.CONSUMED:
            continue
        observation = observations[account.observation_id]
        owners = owners_by_representation.setdefault(observation.representation_id, set())
        observation_ids_by_representation.setdefault(
            observation.representation_id, set()
        ).add(observation.observation_id)
        owners.update(claim_owners[claim_id] for claim_id in account.consumer_claim_ids)
    issues: list[DecisionIssue] = []
    for representation_id, owners in owners_by_representation.items():
        if len(owners) < 2:
            continue
        owner_roles = {instances[owner].role_key for owner in owners}
        allowed_groups = [
            group for group in policy.role_groups
            if group.allow_representation_reuse
            and owner_roles.issubset(set(group.role_keys))
        ]
        claim_is_proven = any(
            claim.representation_id == representation_id
            and len(claim.member_instance_ids) == len(set(claim.member_instance_ids))
            and set(claim.member_instance_ids) == owners
            and len(claim.observation_ids) == len(set(claim.observation_ids))
            and set(claim.observation_ids)
            == observation_ids_by_representation[representation_id]
            and _all_source_proven(claim.proof_constraint_ids, hypothesis)
            for claim in hypothesis.representation_reuse_claims
        )
        if not allowed_groups or not claim_is_proven:
            issues.append(_issue(
                "REPRESENTATION_REUSE_UNPROVEN",
                hypothesis,
                observation_ids=tuple(observation_ids_by_representation[representation_id]),
            ))
    return tuple(issues)


def _materialization_issues(
    policy: DecisionPolicy, hypothesis: Hypothesis
) -> tuple[DecisionIssue, ...]:
    instances = {instance.instance_id: instance for instance in hypothesis.instances}
    equivalences = {claim.equivalence_id: claim for claim in hypothesis.equivalence_claims}
    output_for_member: dict[str, str] = {}
    membership_counts: dict[str, int] = {}
    issues: list[DecisionIssue] = []
    for materialization in hypothesis.materializations:
        members = materialization.member_instance_ids
        if not members:
            issues.append(_issue("MATERIALIZATION_EMPTY_OUTPUT", hypothesis))
        if materialization.quantity != len(members):
            issues.append(_issue("MATERIALIZATION_QUANTITY_MISMATCH", hypothesis))
        for member_id in members:
            membership_counts[member_id] = membership_counts.get(member_id, 0) + 1
            previous_output = output_for_member.setdefault(member_id, materialization.output_id)
            if previous_output != materialization.output_id or members.count(member_id) > 1:
                issues.append(_issue("MATERIALIZATION_MEMBER_REUSED", hypothesis))
        if len(members) <= 1:
            continue
        roles = {instances[member_id].role_key for member_id in members}
        allowed = any(
            group.allow_output_merge and roles.issubset(set(group.role_keys))
            for group in policy.role_groups
        )
        equivalence = (
            equivalences.get(materialization.equivalence_id)
            if materialization.equivalence_id is not None
            else None
        )
        if equivalence is None:
            issues.append(_issue("MERGE_EQUIVALENCE_MISSING", hypothesis))
            continue
        proof_is_passing = _all_source_proven(
            equivalence.proof_constraint_ids, hypothesis
        )
        keys_match = materialization.manufacturing_key == equivalence.manufacturing_key
        members_match = (
            len(equivalence.member_instance_ids)
            == len(set(equivalence.member_instance_ids))
            and set(members) == set(equivalence.member_instance_ids)
        )
        if not keys_match:
            issues.append(_issue("MERGE_MANUFACTURING_KEY_CONFLICT", hypothesis))
        if not allowed or not members_match or not proof_is_passing:
            issues.append(
                _issue(
                    "MERGE_EQUIVALENCE_UNPROVEN",
                    hypothesis,
                    constraint_ids=equivalence.proof_constraint_ids,
                )
            )
    for instance_id in instances:
        if membership_counts.get(instance_id, 0) == 0:
            issues.append(
                _issue(
                    "MATERIALIZATION_INSTANCE_UNACCOUNTED",
                    hypothesis,
                )
            )
    return tuple(issues)


def _critical_evidence_issues(hypothesis: Hypothesis) -> tuple[DecisionIssue, ...]:
    return tuple(
        _issue(
            "CRITICAL_EVIDENCE_MISSING",
            hypothesis,
            constraint_ids=(constraint.constraint_id,),
        )
        for constraint in hypothesis.constraints
        if constraint.critical
        and constraint.status is ConstraintStatus.PASS
        and not constraint.evidence_source_fact_ids
        and not constraint.evidence_observation_ids
    )


def _constraint_issues(
    policy: DecisionPolicy, hypothesis: Hypothesis
) -> tuple[DecisionIssue, ...]:
    outcomes = {constraint.constraint_id: constraint for constraint in hypothesis.constraints}
    issues: list[DecisionIssue] = []
    for constraint_id in policy.required_constraint_ids:
        outcome = outcomes.get(constraint_id)
        if outcome is None:
            issues.append(_issue(
                "REQUIRED_CONSTRAINT_MISSING", hypothesis, constraint_ids=(constraint_id,)
            ))
        elif not outcome.critical:
            issues.append(_issue(
                "REQUIRED_CONSTRAINT_NOT_CRITICAL", hypothesis, constraint_ids=(constraint_id,)
            ))
        elif outcome.status is ConstraintStatus.NOT_APPLICABLE:
            issues.append(_issue(
                "REQUIRED_CONSTRAINT_NOT_APPLICABLE", hypothesis, constraint_ids=(constraint_id,)
            ))
    for constraint in hypothesis.constraints:
        if not constraint.critical:
            continue
        code = {
            ConstraintStatus.MISSING: "CRITICAL_CONSTRAINT_MISSING",
            ConstraintStatus.CONFLICT: "CRITICAL_CONSTRAINT_CONFLICT",
            ConstraintStatus.INCOMPLETE: "CRITICAL_CONSTRAINT_INCOMPLETE",
        }.get(constraint.status)
        if code is not None:
            issues.append(_issue(code, hypothesis, constraint_ids=(constraint.constraint_id,)))
    return tuple(issues)


def _hypothesis_disposition(
    hypothesis: Hypothesis, core_issues: tuple[DecisionIssue, ...]
) -> DecisionDisposition:
    if any(
        issue.code not in {"CRITICAL_CONSTRAINT_MISSING", "REQUIRED_CONSTRAINT_MISSING"}
        for issue in core_issues
    ):
        return DecisionDisposition.REJECTED
    if any(
        issue.code in {"CRITICAL_CONSTRAINT_CONFLICT", "CRITICAL_CONSTRAINT_INCOMPLETE"}
        for issue in core_issues
    ):
        return DecisionDisposition.REJECTED
    if any(
        issue.code in {"CRITICAL_CONSTRAINT_MISSING", "REQUIRED_CONSTRAINT_MISSING"}
        for issue in core_issues
    ):
        return DecisionDisposition.REVIEW_REQUIRED
    return DecisionDisposition.AUTO_ACCEPT


def _certificate_is_sufficient(
    certificate: object, request: DecisionRequest, evaluated_ids: set[str]
) -> bool:
    from .model import PruneCertificate

    if not isinstance(certificate, PruneCertificate):
        return False
    hypotheses = {hypothesis.hypothesis_id: hypothesis for hypothesis in request.hypotheses}
    candidate = hypotheses[certificate.candidate_id]
    if certificate.reason is PruneReason.HARD_CONFLICT:
        conflicts = {
            outcome.constraint_id
            for outcome in candidate.constraints
            if outcome.critical and outcome.status is ConstraintStatus.CONFLICT
        }
        return (
            certificate.equivalent_to_hypothesis_id is None
            and bool(certificate.proof_constraint_ids)
            and set(certificate.proof_constraint_ids).issubset(conflicts)
        )
    target_id = certificate.equivalent_to_hypothesis_id
    if target_id is None or target_id == candidate.hypothesis_id or target_id not in evaluated_ids:
        return False
    target = hypotheses[target_id]
    if candidate.meaning_key != target.meaning_key:
        return False
    return _all_source_proven(certificate.proof_constraint_ids, candidate)


def _search_issues(request: DecisionRequest) -> tuple[DecisionIssue, ...]:
    issues: list[DecisionIssue] = []
    generated_ids = {
        candidate_id
        for scope in request.search_scopes
        for candidate_id in scope.generated_candidate_ids
    }
    if not request.search_scopes or any(
        hypothesis.hypothesis_id not in generated_ids for hypothesis in request.hypotheses
    ):
        issues.append(DecisionIssue(
            code="SEARCH_INCOMPLETE",
            critical=True,
            hypothesis_id=None,
            source_fact_ids=(),
            observation_ids=(),
            constraint_ids=(),
        ))
    for scope in request.search_scopes:
        certificates = {
            certificate.candidate_id: certificate for certificate in scope.prune_certificates
        }
        covered = set(scope.evaluated_candidate_ids)
        covered.update(
            candidate_id
            for candidate_id, certificate in certificates.items()
            if _certificate_is_sufficient(
                certificate, request, set(scope.evaluated_candidate_ids)
            )
        )
        if (
            not scope.enumerator_exhausted
            or scope.budget_exhausted
            or not set(scope.generated_candidate_ids).issubset(covered)
        ):
            issues.append(DecisionIssue(
                code="SEARCH_INCOMPLETE",
                critical=True,
                hypothesis_id=None,
                source_fact_ids=(),
                observation_ids=(),
                constraint_ids=(),
            ))
    return _sort_issues(issues)


def _canonical(value: object, *, field_name: str | None = None) -> object:
    if isinstance(value, str):
        return value
    if is_dataclass(value):
        return {
            field.name: _canonical(getattr(value, field.name), field_name=field.name)
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, tuple):
        items = [_canonical(item) for item in value]
        if field_name != "rank_key":
            items.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
        return items
    return value


def _audit_digest(request: DecisionRequest, result_fields: Mapping[str, object]) -> str:
    payload = {
        "request": _canonical(request),
        "result": _canonical(result_fields),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _sort_issues(issues: Iterable[DecisionIssue]) -> tuple[DecisionIssue, ...]:
    return tuple(
        sorted(
            issues,
            key=lambda issue: (
                issue.hypothesis_id or "",
                issue.code,
                issue.source_fact_ids,
                issue.observation_ids,
                issue.constraint_ids,
            ),
        )
    )


def decide(request: DecisionRequest) -> DecisionResult:
    _validate_request(request)
    issues: list[DecisionIssue] = list(_search_issues(request))
    accepted: list[Hypothesis] = []
    previews: list[Hypothesis] = []
    for hypothesis in request.hypotheses:
        hypothesis_issues = _sort_issues(
            (
                *_role_issues(request.policy, hypothesis),
                *_source_fact_issues(request, hypothesis),
                *_observation_issues(request, hypothesis),
                *_reuse_issues(request, request.policy, hypothesis),
                *_materialization_issues(request.policy, hypothesis),
                *_critical_evidence_issues(hypothesis),
                *_constraint_issues(request.policy, hypothesis),
            )
        )
        issues.extend(hypothesis_issues)
        disposition = _hypothesis_disposition(hypothesis, hypothesis_issues)
        if disposition is DecisionDisposition.AUTO_ACCEPT:
            accepted.append(hypothesis)
        elif disposition is DecisionDisposition.REVIEW_REQUIRED:
            previews.append(hypothesis)
    ordered_issues = _sort_issues(issues)
    candidates = accepted if accepted else previews
    admissible = tuple(sorted(
        (hypothesis.hypothesis_id for hypothesis in (*accepted, *previews))
    ))
    selected = min(candidates, key=lambda hypothesis: (hypothesis.rank_key, hypothesis.hypothesis_id)) if candidates else None
    meanings = {hypothesis.meaning_key for hypothesis in (*accepted, *previews)}
    search_complete = not any(issue.code == "SEARCH_INCOMPLETE" for issue in ordered_issues)
    if not search_complete or selected is None:
        disposition = DecisionDisposition.REJECTED
        selected_hypothesis_id = None
    elif len(meanings) > 1:
        ambiguity = DecisionIssue(
            "AMBIGUOUS_MANUFACTURING_MEANING", True, None, (), (), ()
        )
        ordered_issues = _sort_issues((*ordered_issues, ambiguity))
        disposition = DecisionDisposition.REVIEW_REQUIRED
        selected_hypothesis_id = selected.hypothesis_id
    elif accepted:
        disposition = DecisionDisposition.AUTO_ACCEPT
        selected_hypothesis_id = selected.hypothesis_id
    else:
        disposition = DecisionDisposition.REVIEW_REQUIRED
        selected_hypothesis_id = selected.hypothesis_id
    authorized_merges = ()
    if disposition is DecisionDisposition.AUTO_ACCEPT and selected is not None:
        authorized_merges = tuple(sorted(
            materialization.equivalence_id
            for materialization in selected.materializations
            if materialization.equivalence_id is not None
        ))
    result_fields: Mapping[str, object] = {
        "disposition": disposition,
        "selected_hypothesis_id": selected_hypothesis_id,
        "admissible_hypothesis_ids": admissible,
        "authorized_merge_claim_ids": authorized_merges,
        "search_complete": search_complete,
        "issues": ordered_issues,
    }
    return DecisionResult(
        disposition=disposition,
        selected_hypothesis_id=selected_hypothesis_id,
        admissible_hypothesis_ids=admissible,
        authorized_merge_claim_ids=authorized_merges,
        search_complete=search_complete,
        issues=ordered_issues,
        audit_digest=_audit_digest(request, result_fields),
    )
