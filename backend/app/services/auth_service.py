from __future__ import annotations

import logging
from datetime import UTC, datetime

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import ACTIVE
from app.core.redis_client import get_redis
from app.core.security import create_access_token, create_refresh_token, verify_password
from app.models.user import User

logger = logging.getLogger(__name__)

# Dummy argon2id hash for constant-time comparison when the user does not exist.
# Uses the same parameters (m=65536, t=3, p=4) as PasswordHash.recommended()
# so that verify_password takes the same wall-clock time on both code paths.
_DUMMY_VERIFY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$"
    "c29tZXNhbHRzb21lc2FsdHNvbQ$"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    """Authenticate a user by username and password.

    The function always performs one argon2id verification so that the
    wall-clock time is indistinguishable for valid users, invalid
    passwords, and non-existent usernames — closing the timing
    side-channel that would otherwise allow user enumeration.
    """
    user = db.scalar(select(User).where(User.username == username))
    if not user or user.status != ACTIVE:
        # User does not exist or is not active — burn equivalent CPU time
        # on a dummy hash to prevent timing-based username enumeration.
        verify_password(password, _DUMMY_VERIFY_HASH)
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
# Password-change timestamp — Redis-backed so that existing tokens issued
# before the change are rejected without a DB migration.
# ---------------------------------------------------------------------------
PWD_CHANGE_PREFIX = "pwd_change:user:"


def record_password_change(user_id: int) -> None:
    """Store the timestamp of the last password change for *user_id* in Redis.

    Any access / refresh token issued before this timestamp is considered stale.
    """
    client = get_redis()
    if client is None:
        logger.warning("Password-change timestamp skipped — Redis unavailable.")
        return
    try:
        now_ts = int(datetime.now(UTC).timestamp())
        # Keep the key for twice the refresh-token lifetime so that even
        # long-lived refresh tokens are reliably rejected.
        ttl = settings.jwt_refresh_token_expire_days * 24 * 3600 * 2
        client.setex(PWD_CHANGE_PREFIX + str(user_id), ttl, str(now_ts))
        logger.info("Password-change recorded: user=%d ts=%d", user_id, now_ts)
    except Exception:
        logger.exception("Failed to record password-change timestamp.")


def is_token_stale_for_password_change(user_id: int, token_iat: int) -> bool:
    """Return True if the token (issued at *token_iat*) predates the last
    password change for *user_id*.

    If Redis is unavailable returns False (fail-open for availability).
    """
    client = get_redis()
    if client is None:
        return False
    try:
        stored = client.get(PWD_CHANGE_PREFIX + str(user_id))
        if stored is None:
            return False
        pwd_change_ts = int(stored)
        return token_iat <= pwd_change_ts
    except Exception:
        logger.exception("Password-change staleness check failed — assuming not stale.")
        return False


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
