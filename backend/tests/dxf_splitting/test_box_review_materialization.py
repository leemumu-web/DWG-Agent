from __future__ import annotations

from types import SimpleNamespace

import pytest
from steel_dxf_split.box import manufacturing as box_manufacturing
from steel_dxf_split.box.assembly import AssemblyResolutionError
from steel_dxf_split.box.decision_adapter import (
    BoxDecisionAdapterResult,
    _native_proof_search_is_complete,
)
from steel_dxf_split.box.proofs import (
    ProofObligation,
    ProofReport,
    ProofStatus,
)
from steel_dxf_split.manufacturing_decision import (
    DecisionDisposition,
    DecisionResult,
)

_REQUIRED_NATIVE_PROOFS = (
    "BOX.PROOF.METADATA.UNIQUE",
    "BOX.PROOF.VIEW_ASSIGNMENT.SECTION_SPANS",
    "BOX.PROOF.ASSEMBLY.FOUR_PHYSICAL_ROLES",
    "BOX.PROOF.OPENINGS.CONTAINED",
    "BOX.PROOF.VIEW.PART_MARK_H_ROLE",
    "BOX.PROOF.SEARCH.DIRECT_SOURCE_FACE_DOMAIN",
)


def _obligation(
    obligation_id: str,
    status: ProofStatus = ProofStatus.PASS,
) -> ProofObligation:
    return ProofObligation(
        obligation_id=obligation_id,
        status=status,
        critical=True,
        evidence=(),
    )


def _native_with_extra_proof(status: ProofStatus):
    report = ProofReport(
        obligations=(
            *(_obligation(proof_id) for proof_id in _REQUIRED_NATIVE_PROOFS),
            _obligation("BOX.PROOF.OPENINGS.REPRESENTATION_PAIR", status),
        ),
        search_complete=True,
    )
    return SimpleNamespace(proof_report=report)


def _adapter_result(
    disposition: DecisionDisposition,
    native: object,
) -> BoxDecisionAdapterResult:
    selected_id = (
        "box:hypothesis:review"
        if disposition is not DecisionDisposition.REJECTED
        else None
    )
    decision = DecisionResult(
        disposition=disposition,
        selected_hypothesis_id=selected_id,
        admissible_hypothesis_ids=(() if selected_id is None else (selected_id,)),
        authorized_merge_claim_ids=(),
        search_complete=disposition is not DecisionDisposition.REJECTED,
        issues=(),
        audit_digest="0" * 64,
    )
    return BoxDecisionAdapterResult(
        request=SimpleNamespace(),
        decision=decision,
        native_hypotheses_by_id=(
            {} if selected_id is None else {selected_id: native}
        ),
    )


def test_missing_proof_requires_review_without_reopening_completed_search() -> None:
    native = _native_with_extra_proof(ProofStatus.MISSING)

    assert native.proof_report.disposition.value == "review_required"
    assert _native_proof_search_is_complete(native) is True


def test_incomplete_proof_still_marks_native_search_unfinished() -> None:
    native = _native_with_extra_proof(ProofStatus.INCOMPLETE)

    assert _native_proof_search_is_complete(native) is False


def test_not_applicable_optional_pair_proof_keeps_search_complete() -> None:
    native = _native_with_extra_proof(ProofStatus.NOT_APPLICABLE)

    assert native.proof_report.disposition.value == "auto_accept"
    assert _native_proof_search_is_complete(native) is True


def test_freeze_materializes_selected_review_candidate_without_auto_accepting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mir = object()
    native = _native_with_extra_proof(ProofStatus.MISSING)
    native.mir = mir
    adapted = _adapter_result(DecisionDisposition.REVIEW_REQUIRED, native)
    monkeypatch.setattr(
        box_manufacturing,
        "adapt_box_decision",
        lambda _source, _search: adapted,
    )

    assert adapted.selected_native_hypothesis is None
    assert adapted.selected_review_hypothesis is native
    assert box_manufacturing.freeze_manufacturing(object(), object()) is mir


def test_review_decision_cannot_downgrade_auto_accepted_native_mir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = _native_with_extra_proof(ProofStatus.PASS)
    native.mir = object()
    adapted = _adapter_result(DecisionDisposition.REVIEW_REQUIRED, native)
    monkeypatch.setattr(
        box_manufacturing,
        "adapt_box_decision",
        lambda _source, _search: adapted,
    )

    assert adapted.selected_review_hypothesis is None
    with pytest.raises(AssemblyResolutionError, match="review_required"):
        box_manufacturing.freeze_manufacturing(object(), object())


def test_freeze_still_rejects_a_decision_without_materializable_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapted = _adapter_result(DecisionDisposition.REJECTED, object())
    monkeypatch.setattr(
        box_manufacturing,
        "adapt_box_decision",
        lambda _source, _search: adapted,
    )

    with pytest.raises(AssemblyResolutionError, match="rejected"):
        box_manufacturing.freeze_manufacturing(object(), object())
