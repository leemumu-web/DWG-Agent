"""Password hashing and JWT signing primitives.

Token claim contract:

- Every issued token carries ``sub`` (integer user id as string), ``jti``
  (unique id), ``iat`` (fractional NumericDate), ``exp`` and ``type``
  (``access`` vs ``refresh``).
- ``decode_token`` only verifies signature and expiry — it does NOT check
  ``type``. Callers (identity dependencies/authentication/sessions) must
  validate ``payload["type"]`` themselves, and revocation is enforced by
  writing the ``jti`` into the identity blacklist table plus the
  ``password_changed_at`` timestamp check. Do not assume decode implies
  authorization.
- ``sub`` is the integer user id; extra_claims may add domain-specific
  claims (e.g. cookie flags) but must never override the core claims above.
- Passwords use Argon2id via pwdlib ``PasswordHash.recommended()``; the
  stored ``password_algo`` column must stay consistent with this choice.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import jwt
from pwdlib import PasswordHash

from app.platform.config.settings import settings
from app.platform.time import business_now

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Hash a plaintext password (Argon2id, pwdlib recommended)."""
    return password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a stored hash."""
    return password_hash.verify(plain_password, hashed_password)


def create_access_token(subject: str, extra_claims: dict[str, Any] | None = None) -> str:
    """Issue a short-lived access token (minutes; see module claim contract)."""
    now = business_now()
    expire = now + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "jti": str(uuid.uuid4()),
        # JWT NumericDate permits fractional seconds. Keeping the fraction avoids
        # treating a fresh token issued in the same second as a password change
        # as stale while still accepting older integer-iat tokens.
        "iat": now.timestamp(),
        "exp": int(expire.timestamp()),
        "type": "access",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str, extra_claims: dict[str, Any] | None = None) -> str:
    """Issue a long-lived refresh token (days; see module claim contract)."""
    now = business_now()
    expire = now + timedelta(days=settings.jwt_refresh_token_expire_days)
    payload: dict[str, Any] = {
        "sub": subject,
        "jti": str(uuid.uuid4()),
        "iat": now.timestamp(),
        "exp": int(expire.timestamp()),
        "type": "refresh",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and verify signature/expiry only (does NOT validate ``type``)."""
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
