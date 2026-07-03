from __future__ import annotations

import logging
from datetime import UTC, datetime

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import ACTIVE
from app.core.redis_client import get_redis
from app.core.security import create_access_token, create_refresh_token, verify_password
from app.models.user import User

logger = logging.getLogger(__name__)


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username))
    if not user or user.status != ACTIVE:
        return None
    if not verify_password(password, user.password_hash):
        return None
    user.last_login_at = datetime.now(UTC)
    db.flush()
    return user


def build_login_token(user: User) -> str:
    return create_access_token(subject=str(user.id), extra_claims={"username": user.username})


def build_refresh_token(user: User) -> str:
    return create_refresh_token(subject=str(user.id), extra_claims={"username": user.username})


# ---------------------------------------------------------------------------
# Token blacklist — Redis-backed, TTL matches token expiry so keys self-clean
# ---------------------------------------------------------------------------
BLACKLIST_PREFIX = "blacklist:jti:"


class MissingJtiError(ValueError):
    """Token has no jti claim — cannot be blacklisted (pre-rollout token?)."""


def _extract_jti_exp(token: str) -> tuple[str, int]:
    """Decode token *without verification* to extract jti and exp.

    Raises MissingJtiError if the token lacks a jti claim (e.g. tokens
    issued before jti was added to create_access_token / create_refresh_token).
    """
    payload = jwt.decode(token, options={"verify_signature": False})
    jti = payload.get("jti")
    if not jti:
        raise MissingJtiError("Token has no jti claim.")
    return jti, payload["exp"]


def blacklist_access_token(token: str) -> None:
    """Store the token's jti in Redis with TTL = remaining lifetime.

    After TTL expires the key is automatically removed — no cleanup needed.
    If Redis is unavailable the blacklist is silently skipped (degraded mode).
    """
    client = get_redis()
    if client is None:
        logger.warning("Token blacklist skipped — Redis unavailable.")
        return

    try:
        jti, exp = _extract_jti_exp(token)
    except MissingJtiError:
        logger.warning("Cannot blacklist token — missing jti claim (pre-rollout token?).")
        return
    try:
        now_ts = int(datetime.now(UTC).timestamp())
        ttl = max(exp - now_ts, 1)
        client.setex(BLACKLIST_PREFIX + jti, ttl, "1")
        logger.info("Token blacklisted: jti=%s ttl=%ds", jti, ttl)
    except Exception:
        logger.exception("Failed to blacklist token.")


def is_token_blacklisted(jti: str) -> bool:
    """Return True if the jti is present in the Redis blacklist.

    If Redis is unavailable returns False (fail-open for availability).
    """
    client = get_redis()
    if client is None:
        return False
    try:
        return client.exists(BLACKLIST_PREFIX + jti) == 1
    except Exception:
        logger.exception("Token blacklist check failed — assuming not blacklisted.")
        return False
