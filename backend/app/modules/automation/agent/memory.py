"""Agent session memory backed by MySQL.

Key schema:  ``agent_memory`` table row per session_id → JSON messages list.

TTL and max-message cap come from ``app.platform.config.settings.settings``.
Expired rows are cleaned up on read.

At Stage 1 this module is infrastructure only — it is validated by tests but **not**
called by any runtime endpoint (agent-runs still returns 503).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.modules.automation.agent.models.memory import AgentMemory
from app.platform.config.settings import settings
from app.platform.time import as_business_time, business_now

logger = logging.getLogger(__name__)


def _is_expired(row: AgentMemory) -> bool:
    """Return True if the row's updated_at is older than agent_memory_ttl seconds."""
    age = (business_now() - as_business_time(row.updated_at)).total_seconds()
    return age > settings.agent_memory_ttl


def get_session_history(db: Session, session_id: str) -> list[dict[str, Any]]:
    """Return all stored messages for *session_id*, or ``[]`` on miss / expired."""
    row = db.get(AgentMemory, session_id)
    if row is None:
        return []
    if _is_expired(row):
        db.delete(row)
        db.flush()
        return []
    return row.messages


def save_session_history(db: Session, session_id: str, messages: list[dict[str, Any]]) -> None:
    """Truncate to ``agent_max_messages``, serialise, write (upsert)."""
    truncated = messages[-settings.agent_max_messages :]
    row = db.get(AgentMemory, session_id)
    if row is None:
        row = AgentMemory(session_id=session_id, messages=truncated)
        db.add(row)
    else:
        row.messages = truncated
        # updated_at auto-updates via onupdate
    db.flush()


def delete_session_history(db: Session, session_id: str) -> None:
    """Remove all stored messages for *session_id*."""
    row = db.get(AgentMemory, session_id)
    if row is not None:
        db.delete(row)
        db.flush()


def append_and_save(
    db: Session, session_id: str, new_messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Full flow per spec §11.5: read → append → truncate → save → return.

    This is the convenience entry-point that Agent code will call in Stage 2.
    """
    history = get_session_history(db, session_id)
    combined = history + new_messages
    save_session_history(db, session_id, combined)
    return combined[-settings.agent_max_messages :]
