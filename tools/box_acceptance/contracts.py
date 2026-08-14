from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EvidenceLevel(StrEnum):
    COMPLETE_REFERENCE = "complete_reference"
    HUMAN_CONSTRAINT = "human_constraint"
    INTERNAL_DIAGNOSTIC = "internal_diagnostic"


class FinalStatus(StrEnum):
    PRODUCTION_PASS = "production_pass"
    PRODUCTION_FAIL = "production_fail"
    NO_OUTPUT = "no_output"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"


class ConstraintStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AcceptanceConstraint:
    key: str
    description: str


@dataclass(frozen=True, slots=True)
class ConstraintResult:
    key: str
    status: ConstraintStatus
    reason: str


@dataclass(frozen=True, slots=True)
class EvidenceFile:
    relative_path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class SampleContract:
    sample_id: str
    family: str
    source_sheet: str | None
    category: str | None
    evidence_level: EvidenceLevel
    original: EvidenceFile
    evidence_files: tuple[EvidenceFile, ...]
    constraints: tuple[AcceptanceConstraint, ...]
    human_wording: str | None


@dataclass(frozen=True, slots=True)
class SampleVerdict:
    sample_id: str
    status: FinalStatus
    reasons: tuple[str, ...]
    internal_disposition: str | None
    constraint_results: tuple[ConstraintResult, ...] = ()


def classify_verdict(
    *,
    output_available: bool,
    evidence_level: EvidenceLevel,
    complete_reference_passed: bool | None,
    constraint_results: tuple[ConstraintResult, ...],
    internal_disposition: str | None,
) -> FinalStatus:
    """Classify by external evidence; internal disposition is diagnostic only."""

    _ = internal_disposition
    if not output_available:
        return FinalStatus.NO_OUTPUT
    if any(result.status is ConstraintStatus.FAIL for result in constraint_results):
        return FinalStatus.PRODUCTION_FAIL
    if evidence_level is EvidenceLevel.COMPLETE_REFERENCE:
        if complete_reference_passed is True:
            return FinalStatus.PRODUCTION_PASS
        if complete_reference_passed is False:
            return FinalStatus.PRODUCTION_FAIL
    return FinalStatus.EVIDENCE_INSUFFICIENT
