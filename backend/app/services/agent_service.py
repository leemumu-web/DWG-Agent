from __future__ import annotations

"""Agent orchestration service — Stage 2 placeholder.

When the Agent subsystem is enabled (AGENT_ENABLED=true), this service will:
- Validate agent-run requests (session_id, task, file_id, context)
- Create agent_run DB records with status="queued"
- Dispatch Celery tasks to the agent queue
- Provide query methods for agent-run detail and steps

See docs/architecture/implementation-status.md for the current placeholder boundary.
"""


def create_agent_run(*, db, user_id: int, session_id: str, task: str, file_id: int | None = None, context: dict | None = None):
    """Placeholder — raises 503 until AGENT_ENABLED=true."""
    raise NotImplementedError("Agent service is not available in Stage 1 — AGENT_ENABLED=false.")
