from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class ProofStatus(str, Enum):
    PASS = "pass"
    MISSING = "missing"
    CONFLICT = "conflict"
    INCOMPLETE = "incomplete"
    NOT_APPLICABLE = "not_applicable"


class ProofDisposition(str, Enum):
    AUTO_ACCEPT = "auto_accept"
    REVIEW_REQUIRED = "review_required"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ProofEvidence:
    evidence_id: str
    channel: str
    source_ids: tuple[str, ...]
    measured: float | str | None
    expected: float | str | None
    tolerance: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProofObligation:
    obligation_id: str
    status: ProofStatus
    critical: bool
    evidence: tuple[ProofEvidence, ...]
    diagnostic_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "status": self.status.value,
            "critical": self.critical,
            "evidence": [item.to_dict() for item in self.evidence],
            "diagnostic_code": self.diagnostic_code,
        }


@dataclass(frozen=True, slots=True)
class ProofReport:
    obligations: tuple[ProofObligation, ...]
    search_complete: bool

    @property
    def disposition(self) -> ProofDisposition:
        critical = tuple(item for item in self.obligations if item.critical)
        if not self.search_complete or not critical:
            return ProofDisposition.REJECTED
        if any(
            item.status in {ProofStatus.CONFLICT, ProofStatus.INCOMPLETE}
            for item in critical
        ):
            return ProofDisposition.REJECTED
        if any(item.status == ProofStatus.MISSING for item in critical):
            return ProofDisposition.REVIEW_REQUIRED
        return ProofDisposition.AUTO_ACCEPT

    @property
    def independent_evidence_count(self) -> int:
        return len(
            {
                evidence.evidence_id
                for obligation in self.obligations
                for evidence in obligation.evidence
            }
        )

    @property
    def blocking_obligation_ids(self) -> tuple[str, ...]:
        blockers = [
            item.obligation_id
            for item in self.obligations
            if item.critical
            and item.status
            in {ProofStatus.MISSING, ProofStatus.CONFLICT, ProofStatus.INCOMPLETE}
        ]
        if not self.search_complete:
            blockers.append("BH.PROOF.SEARCH.COMPLETE")
        if not any(item.critical for item in self.obligations):
            blockers.append("BH.PROOF.SET.NONEMPTY")
        return tuple(sorted(set(blockers)))

    def to_dict(self) -> dict[str, Any]:
        status_counts = {
            status.value: sum(item.status == status for item in self.obligations)
            for status in ProofStatus
        }
        return {
            "disposition": self.disposition.value,
            "search_complete": self.search_complete,
            "independent_evidence_count": self.independent_evidence_count,
            "blocking_obligation_ids": list(self.blocking_obligation_ids),
            "status_counts": status_counts,
            "obligations": [item.to_dict() for item in self.obligations],
        }
