from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
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


@dataclass(frozen=True, slots=True)
class StandardOffcut:
    block_type: str
    raw_specification: str
    thickness: Decimal
    length: Decimal
    width: Decimal
    material: str
    remnant_number: str


@dataclass(slots=True)
class ParseResult:
    schema_version: str
    parser_version: str
    source_sha256: str
    material_candidates: list[Candidate]
    project_candidates: list[Candidate]
    part_candidates: list[Candidate]
    warnings: list[ParseWarning]
    standard_offcut: StandardOffcut | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        if self.standard_offcut is not None:
            summary = result["standard_offcut"]
            for key in ("thickness", "length", "width"):
                summary[key] = format(summary[key], "f")
        return result


class ParseError(RuntimeError):
    """Stable, client-safe failure raised when a DXF cannot be read."""
