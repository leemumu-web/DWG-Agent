from __future__ import annotations

import hashlib
from pathlib import Path

from .classifier import classify
from .models import Candidate, Evidence, ParseError, ParseResult, ParseWarning, StandardOffcut
from .reader import read_evidence, read_standard_offcut

__version__ = "0.4.0"


def parse_dxf(path: str | Path) -> ParseResult:
    source = Path(path)
    try:
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
    except OSError as exc:
        raise ParseError("REMNANT_DXF_UNREADABLE") from exc
    evidence, has_structure_anomaly = read_evidence(source)
    materials, projects, parts, warnings = classify(evidence)
    standard_offcut, standard_warnings = read_standard_offcut(source)
    warnings.extend(standard_warnings)
    if has_structure_anomaly:
        warnings.append(ParseWarning("STRUCTURE_ANOMALY", "图纸中存在已跳过的异常实体或块结构"))
    return ParseResult(
        "1.1",
        __version__,
        digest,
        materials,
        projects,
        parts,
        warnings,
        standard_offcut,
    )


__all__ = [
    "Candidate",
    "Evidence",
    "ParseError",
    "ParseResult",
    "ParseWarning",
    "StandardOffcut",
    "parse_dxf",
]
