"""Generic Redis cache with namespace-based key isolation.

All functions are safe to call when Redis is unavailable — they silently return
``None`` / ``0`` and log a warning through the Redis client layer.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "cache:"


def _make_key(namespace: str, cache_key: str) -> str:
    return f"{CACHE_KEY_PREFIX}{namespace}:{cache_key}"


def cache_get(namespace: str, cache_key: str) -> Any | None:
    """Return cached value, or ``None`` on miss / Redis-down."""
    r = get_redis()
    if r is None:
        return None

    raw = r.get(_make_key(namespace, cache_key))
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def cache_set(namespace: str, cache_key: str, value: Any, ttl: int = 3600) -> None:
    """Write *value* under *namespace*:*cache_key* with *ttl* seconds. No-op if Redis is down."""
    r = get_redis()
    if r is None:
        return
    r.set(
        _make_key(namespace, cache_key),
        json.dumps(value, ensure_ascii=False),
        ex=ttl,
    )


def cache_delete(namespace: str, cache_key: str) -> None:
    """Delete a single cache entry."""
    r = get_redis()
    if r is None:
        return
    r.delete(_make_key(namespace, cache_key))


def cache_get_or_set(
    namespace: str,
    cache_key: str,
    factory: Callable[[], Any],
    ttl: int = 3600,
) -> Any:
    """Get from cache; on miss, call *factory*, store result, and return it."""
    cached = cache_get(namespace, cache_key)
    if cached is not None:
        return cached
    value = factory()
    cache_set(namespace, cache_key, value, ttl=ttl)
    return value


def cache_clear_namespace(namespace: str) -> int:
    """Delete every key under a namespace. Returns count of deleted keys."""
    r = get_redis()
    if r is None:
        return 0
    pattern = f"{CACHE_KEY_PREFIX}{namespace}:*"
    keys = list(r.scan_iter(match=pattern))
    if not keys:
        return 0
    return r.delete(*keys)
