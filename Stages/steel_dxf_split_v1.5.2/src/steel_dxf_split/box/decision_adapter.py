from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
from types import MappingProxyType

from steel_dxf_split.manufacturing_decision import (
    ConstraintOutcome,
    ConstraintStatus,
    DecisionDisposition,
    DecisionPolicy,
    DecisionRequest,
    DecisionResult,
    EquivalenceClaim,
    FactDisposition,
    Hypothesis,
    ManufacturingClaim,
    MaterializationPlan,
    ObservationAccount,
    ObservationDisposition,
    ObservationRef,
    PhysicalInstance,
    PruneCertificate,
    PruneReason,
    RepresentationReuseClaim,
    RoleGroupPolicy,
    RoleRequirement,
    SearchScope,
    SourceFactAccount,
    SourceFactRef,
    decide,
)

from .assembly import AssemblySearchResult, CompleteBoxHypothesis
from .equivalence import (
    BOX_DRAFTING_RESOLUTION_MM,
    plate_manufacturing_key,
    plates_manufacturing_equivalent,
    plates_equivalent_after_allowance,
)
from .manufacturing_ir import (
    EvidenceState,
    FeatureEvidence,
    PhysicalPlateIR,
    PhysicalPlateRole,
)
from .proofs import ProofDisposition, ProofStatus
from .source_ir import ObjectGroupIR, SourceDocumentIR, SourceEntityIR


_ROLE_ORDER = (
    PhysicalPlateRole.WEB_LEFT,
    PhysicalPlateRole.WEB_RIGHT,
    PhysicalPlateRole.FLANGE_TOP,
    PhysicalPlateRole.FLANGE_BOTTOM,
)
_ROLE_PAIRS = (
    (
        "web_pair",
        PhysicalPlateRole.WEB_LEFT,
        PhysicalPlateRole.WEB_RIGHT,
    ),
    (
        "flange_pair",
        PhysicalPlateRole.FLANGE_TOP,
        PhysicalPlateRole.FLANGE_BOTTOM,
    ),
)
_NATIVE_PRODUCTION_PROOF_IDS = (
    "BOX.PROOF.METADATA.UNIQUE",
    "BOX.PROOF.VIEW_ASSIGNMENT.SECTION_SPANS",
    "BOX.PROOF.ASSEMBLY.FOUR_PHYSICAL_ROLES",
    "BOX.PROOF.OPENINGS.CONTAINED",
    "BOX.PROOF.OPENINGS.REPRESENTATION_PAIR",
    "BOX.PROOF.VIEW.PART_MARK_H_ROLE",
    "BOX.PROOF.SEARCH.DIRECT_SOURCE_FACE_DOMAIN",
)
_NATIVE_REQUIRED_APPLICABLE_PROOF_IDS = tuple(
    proof_id
    for proof_id in _NATIVE_PRODUCTION_PROOF_IDS
    if proof_id
    not in {
        "BOX.PROOF.OPENINGS.REPRESENTATION_PAIR",
        "BOX.PROOF.VIEW.PART_MARK_H_ROLE",
    }
)
_SOURCE_CONTRACT_CONSTRAINT_ID = "BOX.ADAPTER.SOURCE_CONTRACT.CLOSED"


@dataclass(frozen=True, slots=True)
class BoxDecisionAdapterResult:
    request: DecisionRequest
    decision: DecisionResult
    native_hypotheses_by_id: Mapping[str, CompleteBoxHypothesis]

    @property
    def selected_native_hypothesis(self) -> CompleteBoxHypothesis | None:
        """Expose a native hypothesis for freezing only after auto-acceptance."""

        selected_id = self.decision.selected_hypothesis_id
        if (
            self.decision.disposition is not DecisionDisposition.AUTO_ACCEPT
            or selected_id is None
        ):
            return None
        return self.native_hypotheses_by_id[selected_id]

    @property
    def selected_review_hypothesis(self) -> CompleteBoxHypothesis | None:
        """Expose only a native review candidate that remains non-production."""

        selected_id = self.decision.selected_hypothesis_id
        if (
            self.decision.disposition is not DecisionDisposition.REVIEW_REQUIRED
            or selected_id is None
        ):
            return None
        selected = self.native_hypotheses_by_id[selected_id]
        if selected.proof_report.disposition is not ProofDisposition.REVIEW_REQUIRED:
            return None
        return selected


@dataclass(frozen=True, slots=True)
class _SourceIndex:
    entity_fact_id_by_source_id: Mapping[str, str]
    group_fact_id_by_group_id: Mapping[str, str]
    observation_id_by_fact_id: Mapping[str, str]

    def fact_ids_for_entities(self, source_ids: Iterable[str]) -> tuple[str, ...]:
        return tuple(sorted({self.entity_fact_id_by_source_id[item] for item in source_ids}))

    def fact_ids_for_groups(self, group_ids: Iterable[str]) -> tuple[str, ...]:
        return tuple(sorted({self.group_fact_id_by_group_id[item] for item in group_ids}))

    def fact_ids_for_raw_keys(self, source_keys: Iterable[str]) -> tuple[str, ...]:
        fact_ids: set[str] = set()
        for source_key in source_keys:
            entity_fact_id = self.entity_fact_id_by_source_id.get(source_key)
            group_fact_id = self.group_fact_id_by_group_id.get(source_key)
            if entity_fact_id is not None and group_fact_id is not None:
                raise ValueError(f"ambiguous BOX source key: {source_key}")
            if entity_fact_id is not None:
                fact_ids.add(entity_fact_id)
            elif group_fact_id is not None:
                fact_ids.add(group_fact_id)
        return tuple(sorted(fact_ids))

    def has_raw_key(self, source_key: str) -> bool:
        return (
            source_key in self.entity_fact_id_by_source_id
            or source_key in self.group_fact_id_by_group_id
        )

    def observation_ids_for_facts(self, fact_ids: Iterable[str]) -> tuple[str, ...]:
        return tuple(
            sorted(
                self.observation_id_by_fact_id[fact_id]
                for fact_id in fact_ids
            )
        )


def _stable_suffix(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:16]


def _group_members(source: SourceDocumentIR) -> dict[str, tuple[str, ...]]:
    return {
        group.group_id: tuple(sorted(group.source_ids)) for group in source.groups
    }


def _validate_source_group_contract(source: SourceDocumentIR) -> None:
    entities_by_id: dict[str, SourceEntityIR] = {}
    for entity in source.entities:
        if entity.source_id in entities_by_id:
            raise ValueError("BOX source contains duplicate entity source IDs")
        entities_by_id[entity.source_id] = entity

    groups_by_id: dict[str, ObjectGroupIR] = {}
    member_group_by_source_id: dict[str, str] = {}
    for group in source.groups:
        if group.group_id in groups_by_id:
            raise ValueError(f"duplicate BOX group ID: {group.group_id}")
        groups_by_id[group.group_id] = group
        if len(group.source_ids) != len(set(group.source_ids)):
            raise ValueError(f"BOX group {group.group_id} contains duplicate members")
        for source_id in group.source_ids:
            entity = entities_by_id.get(source_id)
            if entity is None:
                raise ValueError(
                    f"BOX group {group.group_id} references unknown source member: "
                    f"{source_id}"
                )
            previous_group_id = member_group_by_source_id.get(source_id)
            if previous_group_id is not None:
                raise ValueError(
                    f"BOX entity {source_id} belongs to multiple groups: "
                    f"{previous_group_id},{group.group_id}"
                )
            if entity.group_id != group.group_id:
                raise ValueError(
                    f"BOX group {group.group_id} contains entity {source_id} "
                    f"declared for group {entity.group_id}"
                )
            member_group_by_source_id[source_id] = group.group_id

    for entity in source.entities:
        if entity.group_id == "modelspace":
            continue
        if entity.group_id not in groups_by_id:
            raise ValueError(
                f"BOX entity {entity.source_id} references unknown group: "
                f"{entity.group_id}"
            )
        if member_group_by_source_id.get(entity.source_id) != entity.group_id:
            raise ValueError(
                f"BOX entity {entity.source_id} is missing from declared group "
                f"{entity.group_id}"
            )


def _resolve_raw_entity_ids(
    source: SourceDocumentIR,
    source_keys: Iterable[str],
) -> set[str]:
    entity_ids = {entity.source_id for entity in source.entities}
    group_members = _group_members(source)
    resolved: set[str] = set()
    for source_key in source_keys:
        entity_match = source_key in entity_ids
        group_match = source_key in group_members
        if entity_match and group_match:
            raise ValueError(f"ambiguous BOX source key: {source_key}")
        if entity_match:
            resolved.add(source_key)
        elif group_match:
            resolved.update(group_members[source_key])
    return resolved


def _resolve_group_entity_ids(
    source: SourceDocumentIR,
    group_ids: Iterable[str],
) -> set[str]:
    group_members = _group_members(source)
    return {
        source_id
        for group_id in group_ids
        for source_id in group_members[group_id]
    }


def _source_contract(
    source: SourceDocumentIR,
    scoped_source_ids: set[str],
    scoped_group_ids: set[str],
) -> tuple[tuple[SourceFactRef, ...], tuple[ObservationRef, ...], _SourceIndex]:
    all_entities_by_id = {entity.source_id: entity for entity in source.entities}
    if len(all_entities_by_id) != len(source.entities):
        raise ValueError("BOX source contains duplicate entity source IDs")
    entities_by_id = {
        source_id: entity
        for source_id, entity in all_entities_by_id.items()
        if source_id in scoped_source_ids
    }
    entity_fact_id_by_source_id = {
        source_id: f"box:fact:{source_id}" for source_id in entities_by_id
    }
    group_fact_id_by_group_id = {
        group_id: f"box:fact:group:{group_id}" for group_id in scoped_group_ids
    }
    observation_id_by_fact_id = {
        f"box:fact:{source_id}": f"box:observation:{source_id}"
        for source_id in entities_by_id
    }
    observation_id_by_fact_id.update(
        {
            f"box:fact:group:{group_id}": f"box:observation:group:{group_id}"
            for group_id in scoped_group_ids
        }
    )
    entity_facts = tuple(
        SourceFactRef(
            source_fact_id=f"box:fact:{entity.source_id}",
            channel=f"dxf.{entity.kind.casefold()}",
            source_key=entity.source_id,
            authority="box.source_document_ir",
        )
        for entity in sorted(entities_by_id.values(), key=lambda item: item.source_id)
    )
    group_facts = tuple(
        SourceFactRef(
            source_fact_id=f"box:fact:group:{group_id}",
            channel="dxf.insert_group",
            source_key=group_id,
            authority="box.source_document_ir",
        )
        for group_id in sorted(scoped_group_ids)
    )
    entity_observations = tuple(
        ObservationRef(
            observation_id=observation_id_by_fact_id[
                f"box:fact:{entity.source_id}"
            ],
            kind_key=f"box.source.{entity.kind.casefold()}",
            source_fact_ids=(f"box:fact:{entity.source_id}",),
            representation_id=f"box:representation:entity:{entity.source_id}",
            artifact_ref=entity.source_id,
        )
        for entity in sorted(entities_by_id.values(), key=lambda item: item.source_id)
    )
    group_observations = tuple(
        ObservationRef(
            observation_id=observation_id_by_fact_id[
                f"box:fact:group:{group_id}"
            ],
            kind_key="box.source.group",
            source_fact_ids=(f"box:fact:group:{group_id}",),
            representation_id=f"box:representation:{group_id}",
            artifact_ref=group_id,
        )
        for group_id in sorted(scoped_group_ids)
    )
    return (*entity_facts, *group_facts), (*entity_observations, *group_observations), _SourceIndex(
        entity_fact_id_by_source_id=MappingProxyType(entity_fact_id_by_source_id),
        group_fact_id_by_group_id=MappingProxyType(group_fact_id_by_group_id),
        observation_id_by_fact_id=MappingProxyType(observation_id_by_fact_id),
    )


def _plate_evidence(plate: PhysicalPlateIR) -> tuple[FeatureEvidence, ...]:
    return (
        plate.role_evidence,
        *(segment.evidence for segment in plate.outer_segments),
        *(cut.evidence for cut in plate.circular_cuts),
        *(contour.evidence for contour in plate.inner_contours),
        *(
            segment.evidence
            for contour in plate.inner_contours
            for segment in contour.segments
        ),
    )


def _plate_source_keys(plate: PhysicalPlateIR) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                source_id
                for evidence in _plate_evidence(plate)
                for source_id in evidence.source_ids
            }
        )
    )


def _plate_claim_source_keys(plate: PhysicalPlateIR) -> tuple[str, ...]:
    """Return source representations materialized by this physical claim.

    Role evidence and inferred outer-course provenance prove how a plate was
    derived; they are not themselves material edges consumed by that plate.
    Openings remain manufacturing features even when their endpoints required
    a bounded snap, so their complete provenance stays claim-owned.
    """

    claim_evidence = (
        *(
            segment.evidence
            for segment in plate.outer_segments
            if segment.evidence.state is EvidenceState.DIRECT
        ),
        *(cut.evidence for cut in plate.circular_cuts),
        *(contour.evidence for contour in plate.inner_contours),
        *(
            segment.evidence
            for contour in plate.inner_contours
            for segment in contour.segments
        ),
    )
    return tuple(
        sorted(
            {
                source_id
                for evidence in claim_evidence
                for source_id in evidence.source_ids
            }
        )
    )


def _native_plate_source_keys(
    native: CompleteBoxHypothesis,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                source_id
                for plate in native.mir.physical_plates
                for source_id in _plate_source_keys(plate)
            }
        )
    )


def _native_proof_source_keys(
    native: CompleteBoxHypothesis,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                source_id
                for obligation in native.proof_report.obligations
                for evidence in obligation.evidence
                for source_id in evidence.source_ids
            }
        )
    )


def _native_raw_source_keys(
    native: CompleteBoxHypothesis,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                *_native_plate_source_keys(native),
                *_native_proof_source_keys(native),
            }
        )
    )


def _validate_native_proof_contract(native: CompleteBoxHypothesis) -> None:
    expected_disposition = native.proof_report.disposition.value
    if native.mir.proof_disposition != expected_disposition:
        raise ValueError(
            "BOX MIR proof disposition does not match native ProofReport"
        )
    expected_proof_ids = tuple(
        obligation.obligation_id for obligation in native.proof_report.obligations
    )
    if native.mir.proof_ids != expected_proof_ids:
        raise ValueError("BOX MIR proof IDs do not match native ProofReport")


def _native_proof_schema_errors(
    native: CompleteBoxHypothesis,
) -> tuple[frozenset[str], frozenset[str]]:
    obligations_by_id = {
        obligation.obligation_id: obligation
        for obligation in native.proof_report.obligations
    }
    missing = frozenset(
        set(_NATIVE_PRODUCTION_PROOF_IDS) - set(obligations_by_id)
    )
    invalid = {
        proof_id
        for proof_id in _NATIVE_PRODUCTION_PROOF_IDS
        if proof_id in obligations_by_id and not obligations_by_id[proof_id].critical
    }
    invalid.update(
        proof_id
        for proof_id in _NATIVE_REQUIRED_APPLICABLE_PROOF_IDS
        if proof_id in obligations_by_id
        and obligations_by_id[proof_id].status is ProofStatus.NOT_APPLICABLE
    )
    return missing, frozenset(invalid)


def _native_proof_search_is_complete(native: CompleteBoxHypothesis) -> bool:
    """Return whether the candidate domain was fully evaluated.

    A missing critical proof is a completed negative finding and therefore a
    review gate, not unfinished search.  ``INCOMPLETE`` retains its literal
    meaning and keeps the neutral decision kernel fail-closed.
    """

    missing_schema_ids, invalid_schema_ids = _native_proof_schema_errors(native)
    return (
        native.proof_report.search_complete
        and not missing_schema_ids
        and not invalid_schema_ids
        and not any(
            obligation.critical
            and obligation.status is ProofStatus.INCOMPLETE
            for obligation in native.proof_report.obligations
        )
    )


def _native_constraints(
    native: CompleteBoxHypothesis,
    source_index: _SourceIndex,
) -> tuple[ConstraintOutcome, ...]:
    return tuple(
        ConstraintOutcome(
            constraint_id=obligation.obligation_id,
            status=ConstraintStatus(obligation.status.value),
            critical=obligation.critical,
            evidence_source_fact_ids=(
                fact_ids := source_index.fact_ids_for_raw_keys(
                    source_id
                    for evidence in obligation.evidence
                    for source_id in evidence.source_ids
                )
            ),
            evidence_observation_ids=source_index.observation_ids_for_facts(
                fact_ids
            ),
            diagnostic_code=obligation.diagnostic_code,
        )
        for obligation in native.proof_report.obligations
    )


def _meaning_key(plates_by_role: Mapping[PhysicalPlateRole, PhysicalPlateIR]) -> str:
    payload = {
        role.value: plate_manufacturing_key(
            plates_by_role[role],
            tolerance=BOX_DRAFTING_RESOLUTION_MM,
        )
        for role in _ROLE_ORDER
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _plates_by_role(
    native: CompleteBoxHypothesis,
) -> dict[PhysicalPlateRole, PhysicalPlateIR]:
    return {plate.role: plate for plate in native.mir.physical_plates}


def _hypotheses_manufacturing_equivalent(
    first: CompleteBoxHypothesis,
    second: CompleteBoxHypothesis,
) -> bool:
    first_by_role = _plates_by_role(first)
    second_by_role = _plates_by_role(second)
    return set(first_by_role) == set(_ROLE_ORDER) and set(second_by_role) == set(
        _ROLE_ORDER
    ) and all(
        plates_manufacturing_equivalent(
            first_by_role[role],
            second_by_role[role],
            tolerance=BOX_DRAFTING_RESOLUTION_MM,
        )
        for role in _ROLE_ORDER
    )


def _manufacturing_meaning_keys(
    natives: tuple[CompleteBoxHypothesis, ...],
) -> tuple[str, ...]:
    """Cluster complete candidates by actual geometry, never hash bucket proximity."""

    keys: list[str | None] = [None] * len(natives)
    representatives: list[int] = []
    order = sorted(
        range(len(natives)),
        key=lambda index: (
            natives[index].rank_key,
            natives[index].mir.fingerprint,
            index,
        ),
    )
    for index in order:
        representative = next(
            (
                candidate
                for candidate in representatives
                if _hypotheses_manufacturing_equivalent(
                    natives[index],
                    natives[candidate],
                )
            ),
            None,
        )
        if representative is None:
            representatives.append(index)
            representative = index
        keys[index] = _meaning_key(_plates_by_role(natives[representative]))
    return tuple(str(key) for key in keys)


def _adapt_hypothesis(
    hypothesis_id: str,
    native: CompleteBoxHypothesis,
    source_facts: tuple[SourceFactRef, ...],
    observations: tuple[ObservationRef, ...],
    source_index: _SourceIndex,
    current_scope_fact_ids: set[str],
    all_candidate_view_fact_ids: set[str],
    meaning_key: str,
) -> Hypothesis:
    physical_plates = native.mir.physical_plates
    roles = tuple(plate.role for plate in physical_plates)
    if (
        len(physical_plates) != 4
        or len(set(roles)) != 4
        or set(roles) != set(_ROLE_ORDER)
    ):
        raise ValueError(
            "BOX decision adapter requires exactly four unique physical roles"
        )
    plates_by_role = {plate.role: plate for plate in physical_plates}

    instances: list[PhysicalInstance] = []
    claims: list[ManufacturingClaim] = []
    claim_id_by_role: dict[PhysicalPlateRole, str] = {}
    instance_id_by_role: dict[PhysicalPlateRole, str] = {}
    fact_ids_by_claim: dict[str, set[str]] = {}
    unresolved_source_keys: set[str] = set()
    for role in _ROLE_ORDER:
        plate = plates_by_role[role]
        instance_id = f"{hypothesis_id}:instance:{role.value}"
        claim_id = f"{hypothesis_id}:claim:{role.value}"
        instance_id_by_role[role] = instance_id
        claim_id_by_role[role] = claim_id
        instances.append(PhysicalInstance(instance_id, role.value, claim_id))
        claims.append(
            ManufacturingClaim(
                claim_id=claim_id,
                owner_instance_id=instance_id,
                kind_key="box.physical_plate",
                artifact_ref=plate.plate_id,
            )
        )
        source_keys = _plate_claim_source_keys(plate)
        resolved_fact_ids = tuple(
            fact_id
            for fact_id in source_index.fact_ids_for_raw_keys(source_keys)
            if not fact_id.startswith("box:fact:group:")
        )
        fact_ids_by_claim[claim_id] = set(resolved_fact_ids)
        unresolved_source_keys.update(
            source_key
            for source_key in source_keys
            if not source_index.has_raw_key(source_key)
        )

    consumer_claim_ids_by_fact_id: dict[str, set[str]] = {
        source_fact.source_fact_id: set() for source_fact in source_facts
    }
    for claim_id, fact_ids in fact_ids_by_claim.items():
        for fact_id in fact_ids:
            consumer_claim_ids_by_fact_id[fact_id].add(claim_id)

    native_constraints = _native_constraints(native, source_index)
    constraints = list(native_constraints)
    constraints.append(
        ConstraintOutcome(
            constraint_id=_SOURCE_CONTRACT_CONSTRAINT_ID,
            status=ConstraintStatus.PASS,
            critical=True,
            evidence_source_fact_ids=tuple(
                source_fact.source_fact_id for source_fact in source_facts
            ),
            evidence_observation_ids=tuple(
                observation.observation_id for observation in observations
            ),
            diagnostic_code=None,
        )
    )
    missing_schema_ids, invalid_schema_ids = _native_proof_schema_errors(native)
    if missing_schema_ids or invalid_schema_ids:
        candidate_gate_status = ConstraintStatus.CONFLICT
        candidate_gate_diagnostic = "BOX.ADAPTER.NATIVE_PROOF_SCHEMA_INCOMPLETE"
    elif not native.proof_report.search_complete:
        candidate_gate_status = ConstraintStatus.INCOMPLETE
        candidate_gate_diagnostic = "BOX.ADAPTER.NATIVE_SEARCH_INCOMPLETE"
    elif native.proof_report.disposition is ProofDisposition.REJECTED:
        candidate_gate_status = ConstraintStatus.CONFLICT
        candidate_gate_diagnostic = "BOX.ADAPTER.NATIVE_PROOF_REJECTED"
    else:
        candidate_gate_status = ConstraintStatus.PASS
        candidate_gate_diagnostic = None
    constraints.append(
        ConstraintOutcome(
            constraint_id=f"BOX.ADAPTER.NATIVE_CANDIDATE_GATE:{hypothesis_id}",
            status=candidate_gate_status,
            critical=True,
            evidence_source_fact_ids=tuple(
                sorted(
                    {
                        fact_id
                        for constraint in native_constraints
                        for fact_id in constraint.evidence_source_fact_ids
                    }
                )
            ),
            evidence_observation_ids=tuple(
                sorted(
                    {
                        observation_id
                        for constraint in native_constraints
                        for observation_id in constraint.evidence_observation_ids
                    }
                )
            ),
            diagnostic_code=candidate_gate_diagnostic,
        )
    )
    passing_native_constraints_by_fact_id: dict[str, set[str]] = {}
    for constraint in native_constraints:
        if constraint.status is not ConstraintStatus.PASS:
            continue
        for fact_id in constraint.evidence_source_fact_ids:
            passing_native_constraints_by_fact_id.setdefault(
                fact_id,
                set(),
            ).add(constraint.constraint_id)
    native_proven_auxiliary_fact_ids = {
        fact_id
        for fact_id, consumer_ids in consumer_claim_ids_by_fact_id.items()
        if not consumer_ids
        and fact_id in passing_native_constraints_by_fact_id
    }
    selected_group_auxiliary_fact_ids = {
        fact_id
        for fact_id in source_index.fact_ids_for_groups(
            (
                native.assignment.h_view.group_id,
                native.assignment.b_view.group_id,
            )
        )
        if not consumer_claim_ids_by_fact_id[fact_id]
        and fact_id not in native_proven_auxiliary_fact_ids
    }
    conflict_fact_ids = {
        fact_id
        for fact_id, consumer_ids in consumer_claim_ids_by_fact_id.items()
        if not consumer_ids
        and fact_id in current_scope_fact_ids
        and fact_id not in native_proven_auxiliary_fact_ids
        and fact_id not in selected_group_auxiliary_fact_ids
    }
    other_view_auxiliary_fact_ids = {
        fact_id
        for fact_id, consumer_ids in consumer_claim_ids_by_fact_id.items()
        if not consumer_ids
        and fact_id in all_candidate_view_fact_ids
        and fact_id not in current_scope_fact_ids
    }
    other_hypothesis_auxiliary_fact_ids = {
        fact_id
        for fact_id, consumer_ids in consumer_claim_ids_by_fact_id.items()
        if not consumer_ids
        and fact_id not in native_proven_auxiliary_fact_ids
        and fact_id not in selected_group_auxiliary_fact_ids
        and fact_id not in conflict_fact_ids
        and fact_id not in other_view_auxiliary_fact_ids
    }
    other_view_constraint_id: str | None = None
    if other_view_auxiliary_fact_ids:
        other_view_constraint_id = (
            f"BOX.ADAPTER.NOT_SELECTED_VIEW:{hypothesis_id}"
        )
        constraints.append(
            ConstraintOutcome(
                constraint_id=other_view_constraint_id,
                status=ConstraintStatus.PASS,
                critical=False,
                evidence_source_fact_ids=tuple(
                    sorted(other_view_auxiliary_fact_ids)
                ),
                evidence_observation_ids=tuple(
                    source_index.observation_id_by_fact_id[fact_id]
                    for fact_id in sorted(other_view_auxiliary_fact_ids)
                ),
                diagnostic_code="BOX.ADAPTER.OTHER_CANDIDATE_VIEW_SCOPE",
            )
        )
    selected_group_constraint_id: str | None = None
    if selected_group_auxiliary_fact_ids:
        selected_group_constraint_id = (
            f"BOX.ADAPTER.SELECTED_VIEW_SCOPE:{hypothesis_id}"
        )
        constraints.append(
            ConstraintOutcome(
                constraint_id=selected_group_constraint_id,
                status=ConstraintStatus.PASS,
                critical=False,
                evidence_source_fact_ids=tuple(
                    sorted(selected_group_auxiliary_fact_ids)
                ),
                evidence_observation_ids=tuple(
                    source_index.observation_id_by_fact_id[fact_id]
                    for fact_id in sorted(selected_group_auxiliary_fact_ids)
                ),
                diagnostic_code="BOX.ADAPTER.SELECTED_VIEW_SCOPE",
            )
        )
    other_hypothesis_constraint_id: str | None = None
    if other_hypothesis_auxiliary_fact_ids:
        other_hypothesis_constraint_id = (
            f"BOX.ADAPTER.OTHER_HYPOTHESIS_SCOPE:{hypothesis_id}"
        )
        constraints.append(
            ConstraintOutcome(
                constraint_id=other_hypothesis_constraint_id,
                status=ConstraintStatus.PASS,
                critical=False,
                evidence_source_fact_ids=tuple(
                    sorted(other_hypothesis_auxiliary_fact_ids)
                ),
                evidence_observation_ids=tuple(
                    source_index.observation_id_by_fact_id[fact_id]
                    for fact_id in sorted(other_hypothesis_auxiliary_fact_ids)
                ),
                diagnostic_code="BOX.ADAPTER.OTHER_HYPOTHESIS_SCOPE",
            )
        )
    if unresolved_source_keys:
        constraints.append(
            ConstraintOutcome(
                constraint_id=f"BOX.ADAPTER.SOURCE_REFERENCE_CLOSED:{hypothesis_id}",
                status=ConstraintStatus.CONFLICT,
                critical=True,
                evidence_source_fact_ids=(),
                evidence_observation_ids=(),
                diagnostic_code="BOX.ADAPTER.UNKNOWN_SOURCE_REFERENCE",
            )
        )

    def fact_disposition(fact_id: str) -> FactDisposition:
        if consumer_claim_ids_by_fact_id[fact_id]:
            return FactDisposition.OWNED
        if fact_id in conflict_fact_ids:
            return FactDisposition.CONFLICT
        return FactDisposition.AUXILIARY

    def authorization_constraint_ids(fact_id: str) -> tuple[str, ...]:
        if consumer_claim_ids_by_fact_id[fact_id] or fact_id in conflict_fact_ids:
            return ()
        if fact_id in native_proven_auxiliary_fact_ids:
            return tuple(sorted(passing_native_constraints_by_fact_id[fact_id]))
        if fact_id in selected_group_auxiliary_fact_ids:
            assert selected_group_constraint_id is not None
            return (selected_group_constraint_id,)
        if fact_id in other_view_auxiliary_fact_ids:
            assert other_view_constraint_id is not None
            return (other_view_constraint_id,)
        assert other_hypothesis_constraint_id is not None
        return (other_hypothesis_constraint_id,)

    source_fact_accounts = tuple(
        SourceFactAccount(
            source_fact_id=source_fact.source_fact_id,
            disposition=fact_disposition(source_fact.source_fact_id),
            owner_claim_id=(
                min(consumer_claim_ids_by_fact_id[source_fact.source_fact_id])
                if consumer_claim_ids_by_fact_id[source_fact.source_fact_id]
                else None
            ),
            authorization_constraint_ids=authorization_constraint_ids(
                source_fact.source_fact_id
            ),
        )
        for source_fact in source_facts
    )
    observation_disposition = {
        FactDisposition.OWNED: ObservationDisposition.CONSUMED,
        FactDisposition.AUXILIARY: ObservationDisposition.AUXILIARY,
        FactDisposition.CONFLICT: ObservationDisposition.CONFLICT,
    }
    observation_accounts = tuple(
        ObservationAccount(
            observation_id=observation.observation_id,
            disposition=observation_disposition[
                fact_disposition(observation.source_fact_ids[0])
            ],
            consumer_claim_ids=tuple(
                sorted(
                    consumer_claim_ids_by_fact_id[observation.source_fact_ids[0]]
                )
            ),
            authorization_constraint_ids=authorization_constraint_ids(
                observation.source_fact_ids[0]
            ),
            equivalent_to_observation_id=None,
        )
        for observation in observations
    )

    claim_owner = {claim.claim_id: claim.owner_instance_id for claim in claims}
    role_by_instance_id = {
        instance_id_by_role[role]: role for role in _ROLE_ORDER
    }
    source_fact_by_id = {
        source_fact.source_fact_id: source_fact for source_fact in source_facts
    }
    observation_by_id = {
        observation.observation_id: observation for observation in observations
    }
    accounts_by_representation: dict[str, list[ObservationAccount]] = {}
    for account in observation_accounts:
        if account.disposition is ObservationDisposition.CONSUMED:
            representation_id = observation_by_id[
                account.observation_id
            ].representation_id
            accounts_by_representation.setdefault(representation_id, []).append(account)
    reuse_claims: list[RepresentationReuseClaim] = []
    for representation_id, representation_accounts in sorted(
        accounts_by_representation.items()
    ):
        member_ids = tuple(
            sorted(
                {
                    claim_owner[claim_id]
                    for account in representation_accounts
                    for claim_id in account.consumer_claim_ids
                }
            )
        )
        if len(member_ids) < 2:
            continue
        observation_ids = tuple(
            sorted(account.observation_id for account in representation_accounts)
        )
        suffix = _stable_suffix(representation_id)
        evidence_source_fact_ids = tuple(
            sorted(
                {
                    source_fact_id
                    for observation_id in observation_ids
                    for source_fact_id in observation_by_id[
                        observation_id
                    ].source_fact_ids
                }
            )
        )
        proof_constraint_ids = tuple(
            sorted(
                constraint.constraint_id
                for constraint in constraints
                if constraint.constraint_id.startswith(
                    "BOX.PROOF.REPRESENTATION_REUSE."
                )
                and constraint.status is ConstraintStatus.PASS
                and set(constraint.evidence_source_fact_ids)
                == set(evidence_source_fact_ids)
                and set(constraint.evidence_observation_ids)
                == set(observation_ids)
            )
        )
        if not proof_constraint_ids:
            member_roles = {
                role_by_instance_id[member_id] for member_id in member_ids
            }
            pair_candidates = None
            if member_roles == {
                PhysicalPlateRole.WEB_LEFT,
                PhysicalPlateRole.WEB_RIGHT,
            } and len(native.web_candidates) == 2:
                pair_candidates = native.web_candidates
            elif member_roles == {
                PhysicalPlateRole.FLANGE_TOP,
                PhysicalPlateRole.FLANGE_BOTTOM,
            } and len(native.flange_candidates) == 2:
                pair_candidates = native.flange_candidates
            source_keys = {
                source_fact_by_id[source_fact_id].source_key
                for source_fact_id in evidence_source_fact_ids
            }
            four_role_proof_passed = any(
                constraint.constraint_id
                == "BOX.PROOF.ASSEMBLY.FOUR_PHYSICAL_ROLES"
                and constraint.status is ConstraintStatus.PASS
                for constraint in native_constraints
            )

            def shared_circular_opening_is_proven(source_key: str) -> bool:
                if len(member_roles) != 2:
                    return False
                first_role, second_role = tuple(
                    sorted(member_roles, key=lambda role: role.value)
                )
                first_cuts = tuple(
                    cut
                    for cut in plates_by_role[first_role].circular_cuts
                    if source_key in cut.evidence.source_ids
                    and "BOX.OPENING.BOLT_CIRCLE_CONTAINMENT"
                    in cut.evidence.rule_ids
                )
                second_cuts = tuple(
                    cut
                    for cut in plates_by_role[second_role].circular_cuts
                    if source_key in cut.evidence.source_ids
                    and "BOX.OPENING.BOLT_CIRCLE_CONTAINMENT"
                    in cut.evidence.rule_ids
                )
                return bool(first_cuts) and bool(second_cuts) and all(
                    any(
                        abs(first.center[0] - second.center[0]) <= 1e-5
                        and abs(first.center[1] - second.center[1]) <= 1e-5
                        and abs(first.radius_mm - second.radius_mm) <= 1e-5
                        for second in second_cuts
                    )
                    for first in first_cuts
                )

            def shared_outer_segment_is_proven(source_key: str) -> bool:
                if len(member_roles) != 2:
                    return False
                first_role, second_role = tuple(
                    sorted(member_roles, key=lambda role: role.value)
                )
                first_segments = tuple(
                    segment
                    for segment in plates_by_role[first_role].outer_segments
                    if source_key in segment.evidence.source_ids
                    and "BOX.LOWER.SOURCE_LINE" in segment.evidence.rule_ids
                )
                second_segments = tuple(
                    segment
                    for segment in plates_by_role[second_role].outer_segments
                    if source_key in segment.evidence.source_ids
                    and "BOX.LOWER.SOURCE_LINE" in segment.evidence.rule_ids
                )

                # BOX.LOWER.SOURCE_LINE is emitted only when the complete
                # lowered segment lies on the named raw LINE.  Two physical
                # courses may legitimately clip different subsegments of that
                # one projection, so equality of the clipped lengths is not a
                # reuse requirement; exact raw-source containment is.
                return bool(first_segments) and bool(second_segments)

            def shared_inner_contour_is_proven(source_key: str) -> bool:
                if len(member_roles) != 2:
                    return False
                first_role, second_role = tuple(
                    sorted(member_roles, key=lambda role: role.value)
                )
                shared_rule = (
                    "BOX.OPENING.ROLE_AWARE_SHARED_ON_COINCIDENT_PAIR"
                )
                return all(
                    any(
                        source_key in contour.evidence.source_ids
                        and shared_rule in contour.evidence.rule_ids
                        for contour in plates_by_role[role].inner_contours
                    )
                    for role in (first_role, second_role)
                )

            if (
                pair_candidates is not None
                and source_keys
                and four_role_proof_passed
                and all(
                    (
                        all(
                            source_key in candidate.source_ids
                            for candidate in pair_candidates
                        )
                        or shared_circular_opening_is_proven(source_key)
                        or shared_outer_segment_is_proven(source_key)
                        or shared_inner_contour_is_proven(source_key)
                    )
                    for source_key in source_keys
                )
            ):
                reuse_constraint_id = (
                    f"BOX.PROOF.REPRESENTATION_REUSE.{hypothesis_id}:{suffix}"
                )
                constraints.append(
                    ConstraintOutcome(
                        constraint_id=reuse_constraint_id,
                        status=ConstraintStatus.PASS,
                        critical=True,
                        evidence_source_fact_ids=evidence_source_fact_ids,
                        evidence_observation_ids=observation_ids,
                        diagnostic_code=None,
                    )
                )
                proof_constraint_ids = (reuse_constraint_id,)
        reuse_claims.append(
            RepresentationReuseClaim(
                claim_id=f"{hypothesis_id}:reuse:{suffix}",
                representation_id=representation_id,
                member_instance_ids=member_ids,
                observation_ids=observation_ids,
                proof_constraint_ids=proof_constraint_ids,
            )
        )

    equivalence_claims: list[EquivalenceClaim] = []
    materializations: list[MaterializationPlan] = []
    for pair_name, first_role, second_role in _ROLE_PAIRS:
        first = plates_by_role[first_role]
        second = plates_by_role[second_role]
        first_key = plate_manufacturing_key(first)
        second_key = plate_manufacturing_key(second)
        member_ids = (
            instance_id_by_role[first_role],
            instance_id_by_role[second_role],
        )
        if first_key == second_key and plates_equivalent_after_allowance(
            first, second, tolerance=1e-5
        ):
            equivalence_id = f"{hypothesis_id}:equivalence:{pair_name}"
            constraint_id = f"BOX.ADAPTER.EQUIVALENCE:{hypothesis_id}:{pair_name}"
            evidence_source_fact_ids = tuple(
                fact_id
                for fact_id in source_index.fact_ids_for_raw_keys(
                    {
                        *_plate_source_keys(first),
                        *_plate_source_keys(second),
                    }
                )
                if not fact_id.startswith("box:fact:group:")
            )
            evidence_observation_ids = tuple(
                sorted(
                    source_index.observation_id_by_fact_id[source_fact_id]
                    for source_fact_id in evidence_source_fact_ids
                )
            )
            constraints.append(
                ConstraintOutcome(
                    constraint_id=constraint_id,
                    status=ConstraintStatus.PASS,
                    critical=True,
                    evidence_source_fact_ids=evidence_source_fact_ids,
                    evidence_observation_ids=evidence_observation_ids,
                    diagnostic_code=None,
                )
            )
            equivalence_claims.append(
                EquivalenceClaim(
                    equivalence_id=equivalence_id,
                    member_instance_ids=member_ids,
                    manufacturing_key=first_key,
                    proof_constraint_ids=(constraint_id,),
                )
            )
            materializations.append(
                MaterializationPlan(
                    output_id=f"{hypothesis_id}:output:{pair_name}",
                    member_instance_ids=member_ids,
                    manufacturing_key=first_key,
                    quantity=2,
                    equivalence_id=equivalence_id,
                )
            )
            continue
        for role, plate_key in (
            (first_role, first_key),
            (second_role, second_key),
        ):
            materializations.append(
                MaterializationPlan(
                    output_id=f"{hypothesis_id}:output:{role.value}",
                    member_instance_ids=(instance_id_by_role[role],),
                    manufacturing_key=plate_key,
                    quantity=1,
                    equivalence_id=None,
                )
            )

    return Hypothesis(
        hypothesis_id=hypothesis_id,
        meaning_key=meaning_key,
        rank_key=native.rank_key,
        instances=tuple(instances),
        claims=tuple(claims),
        source_fact_accounts=source_fact_accounts,
        observation_accounts=observation_accounts,
        representation_reuse_claims=tuple(reuse_claims),
        equivalence_claims=tuple(equivalence_claims),
        materializations=tuple(materializations),
        constraints=tuple(constraints),
    )


def _required_constraint_ids(
    _natives: tuple[CompleteBoxHypothesis, ...],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            (
                *_NATIVE_REQUIRED_APPLICABLE_PROOF_IDS,
                _SOURCE_CONTRACT_CONSTRAINT_ID,
            )
        )
    )


def adapt_box_decision(
    source: SourceDocumentIR,
    search: CompleteBoxHypothesis | AssemblySearchResult,
) -> BoxDecisionAdapterResult:
    """Translate evaluated BOX candidates and decide without freezing native MIR."""

    _validate_source_group_contract(source)
    if isinstance(search, CompleteBoxHypothesis):
        native_hypotheses = (search,)
        enumeration_complete = search.proof_report.search_complete
    else:
        native_hypotheses = search.hypotheses
        enumeration_complete = search.enumeration_complete
    for native in native_hypotheses:
        _validate_native_proof_contract(native)
    entity_source_ids = {entity.source_id for entity in source.entities}
    known_group_ids = {group.group_id for group in source.groups}
    for native in native_hypotheses:
        for group_id in (
            native.assignment.h_view.group_id,
            native.assignment.b_view.group_id,
        ):
            if group_id not in known_group_ids:
                raise ValueError(f"unknown BOX assignment view group: {group_id}")
    ambiguous_raw_source_keys = {
        source_key
        for native in native_hypotheses
        for source_key in _native_raw_source_keys(native)
        if source_key in entity_source_ids and source_key in known_group_ids
    }
    if ambiguous_raw_source_keys:
        raise ValueError(
            "ambiguous BOX source key: "
            + ",".join(sorted(ambiguous_raw_source_keys))
        )
    known_raw_source_keys = entity_source_ids | known_group_ids
    for native in native_hypotheses:
        unknown_proof_keys = sorted(
            set(_native_proof_source_keys(native)) - known_raw_source_keys
        )
        if unknown_proof_keys:
            raise ValueError(
                "unknown BOX proof source key: " + ",".join(unknown_proof_keys)
            )
        unknown_plate_keys = sorted(
            set(_native_plate_source_keys(native)) - known_raw_source_keys
        )
        if unknown_plate_keys:
            raise ValueError(
                "unknown BOX plate source key: " + ",".join(unknown_plate_keys)
            )
    native_raw_source_keys = tuple(
        _native_raw_source_keys(native) for native in native_hypotheses
    )
    native_view_group_ids = tuple(
        (
            native.assignment.h_view.group_id,
            native.assignment.b_view.group_id,
        )
        for native in native_hypotheses
    )
    native_scoped_entity_ids = tuple(
        _resolve_raw_entity_ids(source, raw_source_keys)
        | _resolve_group_entity_ids(source, view_group_ids)
        for raw_source_keys, view_group_ids in zip(
            native_raw_source_keys,
            native_view_group_ids,
            strict=True,
        )
    )
    native_scoped_group_ids = tuple(
        set(view_group_ids)
        | (set(raw_source_keys) & known_group_ids)
        for raw_source_keys, view_group_ids in zip(
            native_raw_source_keys,
            native_view_group_ids,
            strict=True,
        )
    )
    scoped_source_ids = {
        source_id
        for source_ids in native_scoped_entity_ids
        for source_id in source_ids
    }
    scoped_group_ids = {
        group_id
        for group_ids in native_scoped_group_ids
        for group_id in group_ids
    }
    source_facts, observations, source_index = _source_contract(
        source,
        scoped_source_ids,
        scoped_group_ids,
    )
    all_candidate_view_group_ids = {
        group_id
        for view_group_ids in native_view_group_ids
        for group_id in view_group_ids
    }
    all_candidate_view_entity_ids = _resolve_group_entity_ids(
        source,
        all_candidate_view_group_ids,
    )
    all_candidate_view_fact_ids = (
        set(source_index.fact_ids_for_groups(all_candidate_view_group_ids))
        | set(source_index.fact_ids_for_entities(all_candidate_view_entity_ids))
    )
    native_by_id: dict[str, CompleteBoxHypothesis] = {}
    hypotheses: list[Hypothesis] = []
    meaning_keys = _manufacturing_meaning_keys(native_hypotheses)
    for index, native in enumerate(native_hypotheses):
        hypothesis_id = (
            f"box:hypothesis:{index}:"
            f"{_stable_suffix(native.mir.fingerprint)}"
        )
        native_by_id[hypothesis_id] = native
        hypotheses.append(
            _adapt_hypothesis(
                hypothesis_id,
                native,
                source_facts,
                observations,
                source_index,
                set(
                    source_index.fact_ids_for_raw_keys(
                        native_raw_source_keys[index]
                    )
                )
                | set(
                    source_index.fact_ids_for_entities(
                        native_scoped_entity_ids[index]
                    )
                )
                | set(
                    source_index.fact_ids_for_groups(
                        native_scoped_group_ids[index]
                    )
                ),
                all_candidate_view_fact_ids,
                meaning_keys[index],
            )
        )
    candidate_ids = tuple(hypothesis.hypothesis_id for hypothesis in hypotheses)
    complete_hypothesis_ids = {
        hypothesis.hypothesis_id
        for hypothesis, native in zip(hypotheses, native_hypotheses, strict=True)
        if _native_proof_search_is_complete(native)
    }
    proven_dominance_ids = {
        hypothesis.hypothesis_id
        for hypothesis, native in zip(hypotheses, native_hypotheses, strict=True)
        if any(
            obligation.obligation_id
            == "BOX.PROOF.SEARCH.DIRECT_SOURCE_FACE_DOMAIN"
            and obligation.status is ProofStatus.PASS
            and obligation.diagnostic_code
            in {
                "BOX.SEARCH.EXACT_H_COURSE_MAXIMAL_FLANGE_DOMINATES",
                "BOX.SEARCH.INDEPENDENT_SOURCE_TOPOLOGY_DOMINATES",
            }
            for obligation in native.proof_report.obligations
        )
    }
    prune_certificates: list[PruneCertificate] = []
    pruned_candidate_ids: set[str] = set()
    for hypothesis, native in zip(hypotheses, native_hypotheses, strict=True):
        hard_conflict_ids = tuple(
            sorted(
                obligation.obligation_id
                for obligation in native.proof_report.obligations
                if obligation.critical
                and obligation.status is ProofStatus.CONFLICT
            )
        )
        if not hard_conflict_ids:
            continue
        prune_certificates.append(
            PruneCertificate(
                candidate_id=hypothesis.hypothesis_id,
                reason=PruneReason.HARD_CONFLICT,
                proof_constraint_ids=hard_conflict_ids,
                equivalent_to_hypothesis_id=None,
            )
        )
        pruned_candidate_ids.add(hypothesis.hypothesis_id)
    hypotheses_by_meaning: dict[str, list[Hypothesis]] = {}
    for hypothesis in hypotheses:
        hypotheses_by_meaning.setdefault(hypothesis.meaning_key, []).append(hypothesis)
    for hypothesis, native in zip(hypotheses, native_hypotheses, strict=True):
        if (
            hypothesis.hypothesis_id in complete_hypothesis_ids
            or hypothesis.hypothesis_id in pruned_candidate_ids
        ):
            continue
        incomplete_source_search = any(
            obligation.obligation_id
            == "BOX.PROOF.SEARCH.DIRECT_SOURCE_FACE_DOMAIN"
            and obligation.status is ProofStatus.INCOMPLETE
            and obligation.diagnostic_code
            == "BOX.SEARCH.DIRECT_SOURCE_FACE_SUBSEARCH_INCOMPLETE"
            for obligation in native.proof_report.obligations
        )
        if not incomplete_source_search:
            continue
        targets = tuple(
            candidate
            for candidate in hypotheses_by_meaning[hypothesis.meaning_key]
            if candidate.hypothesis_id in proven_dominance_ids
        )
        if not targets:
            continue
        target = min(
            targets,
            key=lambda candidate: (candidate.rank_key, candidate.hypothesis_id),
        )
        prune_certificates.append(
            PruneCertificate(
                candidate_id=hypothesis.hypothesis_id,
                reason=PruneReason.CANONICAL_DUPLICATE,
                proof_constraint_ids=(
                    "BOX.PROOF.ASSEMBLY.FOUR_PHYSICAL_ROLES",
                ),
                equivalent_to_hypothesis_id=target.hypothesis_id,
            )
        )
        pruned_candidate_ids.add(hypothesis.hypothesis_id)
    unresolved_incomplete_ids = {
        hypothesis.hypothesis_id
        for hypothesis, native in zip(hypotheses, native_hypotheses, strict=True)
        if not _native_proof_search_is_complete(native)
        and hypothesis.hypothesis_id not in pruned_candidate_ids
    }
    search_complete = (
        bool(hypotheses)
        and enumeration_complete
        and not unresolved_incomplete_ids
    )
    policy = DecisionPolicy(
        required_roles=tuple(
            RoleRequirement(role.value, 1) for role in _ROLE_ORDER
        ),
        required_constraint_ids=_required_constraint_ids(native_hypotheses),
        role_groups=tuple(
            RoleGroupPolicy(
                group_id=f"box.{pair_name}",
                role_keys=(first_role.value, second_role.value),
                allow_representation_reuse=True,
                allow_output_merge=True,
            )
            for pair_name, first_role, second_role in _ROLE_PAIRS
        ),
    )
    request = DecisionRequest(
        policy=policy,
        source_facts=source_facts,
        observations=observations,
        hypotheses=tuple(hypotheses),
        search_scopes=(
            SearchScope(
                scope_id="box:search:root",
                parent_scope_id=None,
                generated_candidate_ids=candidate_ids,
                evaluated_candidate_ids=tuple(
                    candidate_id
                    for candidate_id in candidate_ids
                    if candidate_id not in pruned_candidate_ids
                ),
                prune_certificates=tuple(prune_certificates),
                enumerator_exhausted=search_complete,
                budget_exhausted=bool(unresolved_incomplete_ids),
            ),
        ),
    )
    decision = decide(request)
    return BoxDecisionAdapterResult(
        request=request,
        decision=decision,
        native_hypotheses_by_id=MappingProxyType(native_by_id),
    )


__all__ = (
    "BoxDecisionAdapterResult",
    "adapt_box_decision",
)
