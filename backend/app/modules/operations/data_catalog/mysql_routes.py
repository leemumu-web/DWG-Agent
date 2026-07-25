"""Platform-authenticated entry point for CloudBeaver."""

from __future__ import annotations

import jwt
from fastapi import APIRouter, Request, Response, status

from app.modules.identity.interface import CurrentUser, User, is_admin
from app.modules.operations.data_catalog.mysql_gateway import (
    DBA_COOKIE_NAME,
    DBA_COOKIE_PATH,
    create_mysql_gateway_token,
    decode_mysql_gateway_token,
)
from app.platform.config.constants import ACTIVE
from app.platform.config.settings import settings
from app.platform.http.dependencies import DbSession
from app.platform.http.envelopes import ok
from app.platform.http.exceptions import AppHTTPException

router = APIRouter()


def _team(user: User) -> str:
    return "dba-admin" if is_admin(user) else "dba-reader"


@router.post("/mysql-sessions", status_code=status.HTTP_201_CREATED)
def create_mysql_session(
    request: Request,
    response: Response,
    current_user: CurrentUser,
):
    team = _team(current_user)
    token = create_mysql_gateway_token(
        user_id=current_user.id,
        username=current_user.username,
        team=team,
    )
    response.set_cookie(
        DBA_COOKIE_NAME,
        token,
        max_age=settings.dba_session_ttl_seconds,
        httponly=True,
        secure=settings.refresh_cookie_secure_enabled,
        samesite="lax",
        path=DBA_COOKIE_PATH,
    )
    return ok(
        {
            "team": team,
            "expires_in": settings.dba_session_ttl_seconds,
            "url": DBA_COOKIE_PATH,
        },
        request.state.request_id,
    )


@router.get("/mysql-session")
def validate_mysql_session(request: Request, response: Response, db: DbSession):
    token = request.cookies.get(DBA_COOKIE_NAME)
    try:
        if not token:
            raise ValueError("DBA gateway cookie is missing.")
        payload = decode_mysql_gateway_token(token)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise AppHTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "INVALID_DBA_SESSION",
            "Invalid or expired database console session.",
        ) from None

    user = db.get(User, user_id)
    if user is None or user.status != ACTIVE:
        raise AppHTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "INVALID_DBA_SESSION",
            "Database console user is not active.",
        )
    response.headers["X-User"] = user.username
    response.headers["X-Team"] = _team(user)
    return ok(
        {"username": user.username, "team": _team(user)},
        request.state.request_id,
    )


__all__ = ["router"]
