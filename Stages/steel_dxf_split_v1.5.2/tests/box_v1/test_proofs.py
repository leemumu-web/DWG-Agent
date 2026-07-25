from __future__ import annotations

from steel_dxf_split.box.proofs import (
    ProofDisposition,
    ProofEvidence,
    ProofObligation,
    ProofReport,
    ProofStatus,
)


def _obligation(status: ProofStatus, *, critical: bool = True) -> ProofObligation:
    return ProofObligation(
        obligation_id=f"BOX.PROOF.{status.value.upper()}",
        status=status,
        critical=critical,
        evidence=(
            ProofEvidence(
                evidence_id=f"source:{status.value}",
                channel="source_geometry",
                source_ids=("insert:1/entity:2",),
                measured=1.0,
                expected=1.0,
                tolerance=0.01,
            ),
        ),
    )


def test_proof_report_fails_closed() -> None:
    assert (
        ProofReport((_obligation(ProofStatus.PASS),), True).disposition
        is ProofDisposition.AUTO_ACCEPT
    )
    assert (
        ProofReport((_obligation(ProofStatus.MISSING),), True).disposition
        is ProofDisposition.REVIEW_REQUIRED
    )
    assert (
        ProofReport((_obligation(ProofStatus.CONFLICT),), True).disposition
        is ProofDisposition.REJECTED
    )
    assert (
        ProofReport((_obligation(ProofStatus.INCOMPLETE),), True).disposition
        is ProofDisposition.REJECTED
    )
    assert (
        ProofReport((_obligation(ProofStatus.PASS),), False).disposition
        is ProofDisposition.REJECTED
    )
    assert ProofReport((), True).disposition is ProofDisposition.REJECTED


def test_noncritical_missing_proof_does_not_block_auto_accept() -> None:
    report = ProofReport(
        (
            _obligation(ProofStatus.PASS),
            _obligation(ProofStatus.MISSING, critical=False),
        ),
        True,
    )

    assert report.disposition is ProofDisposition.AUTO_ACCEPT
    assert report.independent_evidence_count == 2
