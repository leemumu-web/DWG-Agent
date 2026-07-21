from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class AutomationCapabilityContract(BaseModel):
    """Machine-readable status without pretending an execution adapter exists."""

    model_config = ConfigDict(frozen=True)

    code: Literal["agent_runtime", "mcp_cad", "zwcad_worker"]
    status: Literal["disabled", "not_implemented", "external_not_implemented"]
    delivered: tuple[str, ...] = ()
    not_available: tuple[str, ...]
    activation_note: str
