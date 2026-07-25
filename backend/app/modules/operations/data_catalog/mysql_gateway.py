"""Short-lived platform identity bridge for the embedded MySQL console."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.platform.config.settings import settings

DBA_COOKIE_NAME = "dwg_dba_token"
DBA_COOKIE_PATH = "/dba/mysql/"


def create_mysql_gateway_token(*, user_id: int, username: str, team: str) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "username": username,
        "team": team,
        "iat": now.timestamp(),
        "exp": int((now + timedelta(seconds=settings.dba_session_ttl_seconds)).timestamp()),
        "type": "dba_gateway",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_mysql_gateway_token(token: str) -> dict[str, Any]:
    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )
    if payload.get("type") != "dba_gateway":
        raise jwt.InvalidTokenError("Token type is not dba_gateway.")
    return payload


__all__ = [
    "DBA_COOKIE_NAME",
    "DBA_COOKIE_PATH",
    "create_mysql_gateway_token",
    "decode_mysql_gateway_token",
]
