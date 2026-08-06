from __future__ import annotations

from .assembly import AssemblyResolutionError, AssemblySearchResult
from .decision_adapter import adapt_box_decision
from .manufacturing_ir import BoxManufacturingIR
from .source_ir import SourceDocumentIR


def freeze_manufacturing(
    source: SourceDocumentIR,
    search: AssemblySearchResult,
) -> BoxManufacturingIR:
    """Freeze native MIR only after the neutral kernel auto-accepts one meaning."""

    adapted = adapt_box_decision(source, search)
    selected = adapted.selected_native_hypothesis
    if selected is None:
        issue_codes = ",".join(sorted({issue.code for issue in adapted.decision.issues}))
        raise AssemblyResolutionError(
            "BOX manufacturing decision did not auto-accept a unique meaning: "
            f"{adapted.decision.disposition.value}:{issue_codes}"
        )
    return selected.mir
