from __future__ import annotations

from pathlib import Path

from .model import ClassificationResult, Disposition, TextFact, TitleCandidate
from .reader import DXFReadError, read_text_facts
from .title_block import find_title_candidates


def _value_identity(candidate: TitleCandidate) -> tuple[str, float, float, tuple[str, ...]]:
    return (
        candidate.profile.normalized,
        candidate.value.x,
        candidate.value.y,
        candidate.value.block_path,
    )


def classify_facts(
    source_name: str,
    facts: list[TextFact],
    *,
    source_metadata: dict[str, object] | None = None,
) -> ClassificationResult:
    labels, candidates = find_title_candidates(facts)
    metadata = dict(source_metadata or {})
    metadata["title_label_count"] = len(labels)
    metadata["title_candidate_count"] = len(candidates)
    if not labels:
        return ClassificationResult(
            source_name,
            Disposition.REVIEW_REQUIRED,
            None,
            ("TITLE_FIELD_MISSING",),
            source_metadata=metadata,
        )
    if not candidates:
        return ClassificationResult(
            source_name,
            Disposition.REVIEW_REQUIRED,
            None,
            ("TITLE_VALUE_MISSING",),
            source_metadata=metadata,
        )

    unique_values = {_value_identity(candidate) for candidate in candidates}
    if len(unique_values) != 1:
        return ClassificationResult(
            source_name,
            Disposition.REVIEW_REQUIRED,
            None,
            ("TITLE_VALUE_CONFLICT",),
            tuple(candidates),
            metadata,
        )

    winner = candidates[0]
    diagnostics = ["TITLE_PROFILE_PROVED"]
    if winner.profile.catalog_status == "unregistered":
        diagnostics.append("PROFILE_TYPE_UNREGISTERED")
    return ClassificationResult(
        source_name,
        Disposition.CLASSIFIED,
        winner.profile.part_type,
        tuple(diagnostics),
        tuple(candidates),
        metadata,
    )


def classify_file(path: str | Path) -> ClassificationResult:
    source = Path(path)
    try:
        facts, metadata = read_text_facts(source)
    except DXFReadError as exc:
        return ClassificationResult(
            source.name,
            Disposition.UNREADABLE,
            None,
            ("DXF_READ_FAILED",),
            source_metadata={"error": str(exc)},
        )
    return classify_facts(source.name, facts, source_metadata=metadata)
