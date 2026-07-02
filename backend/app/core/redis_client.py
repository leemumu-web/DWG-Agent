from __future__ import annotations

import logging

import redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None
_redis_available: bool | None = None  # None = not checked, True = connected, False = unavailable


def get_redis() -> redis.Redis | None:
    """Return the shared Redis client, or None if unavailable.

    Lazy-initialises on first call. Once marked unavailable the cached ``False`` is
    returned on every subsequent call — no retry loop, no startup crash.
    """
    global _redis_client, _redis_available

    if _redis_available is False:
        return None

    if _redis_client is not None:
        return _redis_client

    try:
        _redis_client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        _redis_client.ping()
        _redis_available = True
        logger.info("Redis connected: %s", settings.redis_url)
    except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
        _redis_available = False
        _redis_client = None
        logger.warning(
            "Redis unavailable at %s: %s. Running without cache/memory.",
            settings.redis_url,
            exc,
        )

    return _redis_client


def redis_health() -> dict[str, str]:
    """Return health status for the Redis connection."""
    client = get_redis()
    if client is None:
        return {"status": "unavailable", "message": "Redis not connected."}
    try:
        client.ping()
        return {"status": "ok", "message": "Redis is reachable."}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def close_redis() -> None:
    """Close the Redis connection pool. Safe to call multiple times."""
    global _redis_client, _redis_available
    if _redis_client is not None:
        try:
            _redis_client.close()
        except Exception:
            pass
        _redis_client = None
    _redis_available = None
