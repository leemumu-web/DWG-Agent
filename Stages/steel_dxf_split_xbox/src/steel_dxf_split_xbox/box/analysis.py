from __future__ import annotations

from .metadata import BoxMetadata, resolve_box_metadata, resolve_xbox_metadata
from .source_ir import SourceDocumentIR


def run_analysis(source: SourceDocumentIR, *, family: str = "BOX") -> BoxMetadata:
    """Resolve Project 2 metadata without adding main-project heuristics."""

    if family == "BOX":
        return resolve_box_metadata(source)
    if family == "XBOX":
        return resolve_xbox_metadata(source)
    raise ValueError(f"unsupported closed-box family: {family}")
