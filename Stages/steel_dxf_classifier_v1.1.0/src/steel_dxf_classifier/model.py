from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Disposition(str, Enum):
    CLASSIFIED = "classified"
    REVIEW_REQUIRED = "review_required"
    UNREADABLE = "unreadable"


@dataclass(frozen=True, slots=True)
class TextFact:
    raw: str
    normalized: str
    x: float
    y: float
    height: float
    entity_type: str
    layer: str
    handle: str | None
    block_path: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "normalized": self.normalized,
            "position": [self.x, self.y],
            "height": self.height,
            "entity_type": self.entity_type,
            "layer": self.layer,
            "handle": self.handle,
            "block_path": list(self.block_path),
        }


@dataclass(frozen=True, slots=True)
class ProfileParse:
    raw: str
    normalized: str
    part_type: str
    catalog_status: str
    type_source: str

    def to_dict(self) -> dict[str, str]:
        return {
            "raw": self.raw,
            "normalized": self.normalized,
            "part_type": self.part_type,
            "catalog_status": self.catalog_status,
            "type_source": self.type_source,
        }


@dataclass(frozen=True, slots=True)
class TitleCandidate:
    label: TextFact
    value: TextFact
    profile: ProfileParse
    direction: str
    normalized_distance: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label.to_dict(),
            "value": self.value.to_dict(),
            "profile": self.profile.to_dict(),
            "direction": self.direction,
            "normalized_distance": self.normalized_distance,
        }


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    source_name: str
    disposition: Disposition
    part_type: str | None
    diagnostics: tuple[str, ...]
    candidates: tuple[TitleCandidate, ...] = ()
    source_metadata: dict[str, Any] = field(default_factory=dict)
    profile_raw: str | None = None
    profile_normalized: str | None = None
    type_source: str | None = None
    group_key: str = ""
    next_stage_eligible: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "disposition": self.disposition.value,
            "part_type": self.part_type,
            "diagnostics": list(self.diagnostics),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "source_metadata": self.source_metadata,
            "profile_raw": self.profile_raw,
            "profile_normalized": self.profile_normalized,
            "type_source": self.type_source,
            "group_key": self.group_key,
            "next_stage_eligible": self.next_stage_eligible,
        }


@dataclass(frozen=True, slots=True)
class BatchSummary:
    project_name: str
    input_directory: str
    input_count: int
    classified_count: int
    review_required_count: int
    unreadable_count: int
    type_counts: dict[str, int]
    output_directories: tuple[str, ...]
    elapsed_seconds: float
    results: tuple[ClassificationResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "input_directory": self.input_directory,
            "input_count": self.input_count,
            "classified_count": self.classified_count,
            "review_required_count": self.review_required_count,
            "unreadable_count": self.unreadable_count,
            "type_counts": dict(sorted(self.type_counts.items())),
            "output_directories": list(self.output_directories),
            "elapsed_seconds": self.elapsed_seconds,
        }
