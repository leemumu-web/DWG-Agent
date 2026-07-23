from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Evidence:
    raw_text: str
    normalized_text: str
    entity_type: str
    layer: str
    block_path: list[str]
    x: float
    y: float
    handle: str | None


@dataclass(slots=True)
class Candidate:
    value: str
    evidence: list[Evidence] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ParseWarning:
    code: str
    message: str


@dataclass(slots=True)
class ParseResult:
    schema_version: str
    parser_version: str
    source_sha256: str
    material_candidates: list[Candidate]
    project_candidates: list[Candidate]
    part_candidates: list[Candidate]
    warnings: list[ParseWarning]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ParseError(RuntimeError):
    """Stable, client-safe failure raised when a DXF cannot be read."""
