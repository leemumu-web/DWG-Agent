"""Redis session memory per spec §11.5.

Key schema:  ``agent:memory:{session_id}`` → JSON list of message dicts.
TTL and max-message cap come from ``app.core.config.settings``.

At Stage 1 this module is infrastructure only — it is validated by tests but **not**
called by any runtime endpoint (agent-runs still returns 503).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.config import settings
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

MEMORY_KEY_PREFIX = "agent:memory:"


def _make_key(session_id: str) -> str:
    return f"{MEMORY_KEY_PREFIX}{session_id}"


def get_session_history(session_id: str) -> list[dict[str, Any]]:
    """Return all stored messages for *session_id*, or ``[]`` on miss / Redis-down."""
    r = get_redis()
    if r is None:
        logger.warning("Redis unavailable; returning empty history for session %s.", session_id)
        return []

    raw = r.get(_make_key(session_id))
    if raw is None:
        return []

    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Corrupted memory for session %s; resetting.", session_id)
        r.delete(_make_key(session_id))
        return []


def save_session_history(session_id: str, messages: list[dict[str, Any]]) -> None:
    """Truncate to ``redis_max_messages``, serialise, write with TTL."""
    r = get_redis()
    if r is None:
        logger.warning("Redis unavailable; cannot save history for session %s.", session_id)
        return

    truncated = messages[-settings.redis_max_messages :]
    r.set(
        _make_key(session_id),
        json.dumps(truncated, ensure_ascii=False),
        ex=settings.redis_memory_ttl,
    )


def delete_session_history(session_id: str) -> None:
    """Remove all stored messages for *session_id*."""
    r = get_redis()
    if r is None:
        return
    r.delete(_make_key(session_id))


def append_and_save(
    session_id: str, new_messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Full flow per spec §11.5: read → append → truncate → save → return.

    This is the convenience entry-point that Agent code will call in Stage 2.
    """
    history = get_session_history(session_id)
    combined = history + new_messages
    save_session_history(session_id, combined)
    return combined[-settings.redis_max_messages :]
