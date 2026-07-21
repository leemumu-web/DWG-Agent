from __future__ import annotations

from typing import Any

from app.modules.automation.contracts.schemas import AutomationCapabilityContract


def automation_capability_contracts() -> tuple[AutomationCapabilityContract, ...]:
    """Describe current capability truth; this function performs no execution."""
    return (
        AutomationCapabilityContract(
            code="agent_runtime",
            status="disabled",
            delivered=(
                "mysql session memory",
                "run and step read models",
                "disabled HTTP boundary",
            ),
            not_available=("LLM execution", "LangGraph orchestration", "Celery Agent task"),
            activation_note="AGENT_ENABLED alone cannot activate a missing executor.",
        ),
        AutomationCapabilityContract(
            code="mcp_cad",
            status="not_implemented",
            not_available=("MCP process client", "tool registry", "tool-to-Agent adapter"),
            activation_note="Command settings are reserved contract fields only.",
        ),
        AutomationCapabilityContract(
            code="zwcad_worker",
            status="external_not_implemented",
            not_available=("authenticated client", "job lease", "fencing token", "CAD execution"),
            activation_note="The Windows execution plane requires a separately delivered service.",
        ),
    )


def windows_node_contract() -> dict[str, Any]:
    """Return the existing draft control-plane contract without an implementation claim."""
    return {
        "version": "v1-draft",
        "status": "pending",
        "transport": "HTTPS agent registration and heartbeat (not implemented)",
        "endpoints": [
            {
                "method": "POST",
                "path": "/nodes/register",
                "purpose": "node identity and capabilities",
            },
            {
                "method": "POST",
                "path": "/nodes/{node_id}/heartbeat",
                "purpose": "lease/activity renewal",
            },
            {
                "method": "POST",
                "path": "/nodes/{node_id}/events",
                "purpose": "agent-to-control event envelope",
            },
        ],
        "not_available": [
            "agent authentication",
            "lease fencing",
            "Named Pipe CAD runner",
            "command delivery",
        ],
    }


__all__ = ["automation_capability_contracts", "windows_node_contract"]
