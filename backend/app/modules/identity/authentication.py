from __future__ import annotations

import logging
from datetime import UTC, datetime

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.identity.models.token_blacklist import TokenBlacklist
from app.modules.identity.models.user import User
from app.platform.config.constants import ACTIVE
from app.platform.security.tokens import create_access_token, create_refresh_token, verify_password

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
# Password-change timestamp — stored on the User model so existing tokens
# issued before the change are rejected.
# ---------------------------------------------------------------------------


def record_password_change(db: Session, user_id: int) -> None:
    """Store the timestamp of the last password change on the User row.

    Any access / refresh token issued before this timestamp is considered stale.
    """
    user = db.get(User, user_id)
    if user is None:
        return
    user.password_changed_at = datetime.now(UTC)
    db.flush()
    logger.info("Password-change recorded: user=%d ts=%s", user_id, user.password_changed_at)


def is_token_stale_for_password_change(db: Session, user_id: int, token_iat: float) -> bool:
    """Return True if the token (issued at *token_iat*) predates the last
    password change for *user_id*.
    """
    user = db.get(User, user_id)
    if user is None or user.password_changed_at is None:
        return False
    return token_iat <= user.password_changed_at.replace(tzinfo=UTC).timestamp()


# ---------------------------------------------------------------------------
# Token blacklist — MySQL-backed; expired rows are cleaned on each logout.
# ---------------------------------------------------------------------------


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


def blacklist_access_token(db: Session, token: str) -> None:
    """Store the token's jti in MySQL with expires_at = token expiry.

    Expired rows can be cleaned up periodically — no urgent need since
    the table stays small (one row per logout).
    """
    try:
        jti, exp = _extract_jti_exp(token)
    except (MissingJtiError, jwt.PyJWTError, KeyError, TypeError, ValueError):
        logger.warning("Cannot blacklist malformed token or token without a jti claim.")
        return

    cleanup_expired_blacklist(db)
    expires_at = datetime.fromtimestamp(exp, tz=UTC)
    existing = db.get(TokenBlacklist, jti)
    if existing:
        existing.expires_at = expires_at
    else:
        db.add(TokenBlacklist(jti=jti, expires_at=expires_at))
    db.flush()
    logger.info("Token blacklisted: jti=%s expires=%s", jti, expires_at)


def is_token_blacklisted(db: Session, jti: str) -> bool:
    """Return True if the jti is present in the MySQL blacklist and not expired."""
    row = db.get(TokenBlacklist, jti)
    if row is None:
        return False
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < datetime.now(UTC):
        return False
    return True


def cleanup_expired_blacklist(db: Session) -> int:
    """Delete expired blacklist rows. Returns count of deleted rows."""
    from sqlalchemy import delete as sa_delete

    result = db.execute(
        sa_delete(TokenBlacklist).where(TokenBlacklist.expires_at < datetime.now(UTC))
    )
    db.flush()
    return result.rowcount or 0
