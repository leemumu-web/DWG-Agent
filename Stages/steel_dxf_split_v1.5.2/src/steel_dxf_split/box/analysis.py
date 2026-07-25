from __future__ import annotations

from .metadata import BoxMetadata, resolve_box_metadata
from .source_ir import SourceDocumentIR


def run_analysis(source: SourceDocumentIR) -> BoxMetadata:
    """Resolve Project 2 metadata without adding main-project heuristics."""

    return resolve_box_metadata(source)
