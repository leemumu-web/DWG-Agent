from __future__ import annotations

from pathlib import Path

from .model import ClassificationResult, Disposition, TextFact, TitleCandidate
from .reader import DXFReadError, read_text_facts
from .title_block import find_title_candidates


def _value_identity(candidate: TitleCandidate) -> str:
    # Duplicated title-block text in several anonymous blocks is redundant
    # references to the same profile, not a conflicting value. Only the parsed
    # profile decides uniqueness; geometry/blocks must not split one value.
    return candidate.profile.normalized


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
            group_key="status:review_required",
        )
    if not candidates:
        return ClassificationResult(
            source_name,
            Disposition.REVIEW_REQUIRED,
            None,
            ("TITLE_VALUE_MISSING",),
            source_metadata=metadata,
            group_key="status:review_required",
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
            group_key="status:review_required",
        )

    winner = candidates[0]
    diagnostics = ["TITLE_PROFILE_PROVED"]
    if winner.profile.type_source == "auto_discovered":
        diagnostics.append("PROFILE_TYPE_AUTO_DISCOVERED")
    return ClassificationResult(
        source_name,
        Disposition.CLASSIFIED,
        winner.profile.part_type,
        tuple(diagnostics),
        tuple(candidates),
        metadata,
        profile_raw=winner.value.raw,
        profile_normalized=winner.profile.normalized,
        type_source=winner.profile.type_source,
        profile_source_dialect=winner.profile.profile_source_dialect,
        profile_extra=winner.profile.profile_extra,
        group_key=f"type:{winner.profile.part_type}",
        next_stage_eligible=True,
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
            group_key="status:unreadable",
        )
    return classify_facts(source.name, facts, source_metadata=metadata)
