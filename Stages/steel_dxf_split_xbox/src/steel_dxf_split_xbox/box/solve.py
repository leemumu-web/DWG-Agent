from __future__ import annotations

from .assembly import AssemblySearchResult, solve_complete_box
from .metadata import BoxMetadata
from .source_ir import SourceDocumentIR


def run_solve(
    source: SourceDocumentIR,
    metadata: BoxMetadata,
) -> AssemblySearchResult:
    """Run the exact Project 2 complete BOX hypothesis search."""

    return solve_complete_box(source, metadata)
