from __future__ import annotations

import logging
from typing import Annotated

import jwt
from fastapi import Depends, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import ACTIVE, ROLE_SUPER_ADMIN
from app.core.exceptions import AppHTTPException, forbidden
from app.core.permissions import (  # noqa: F401 -- re-export for backward-compatible API-layer access
    get_project_membership,
    has_global_project_access,
    is_admin,
    require_active_project,
    require_project_member,
    require_project_role,
    user_role_codes,
)
from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import User

DbSession = Annotated[Session, Depends(get_db)]
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_v1_prefix}/auth/sessions", auto_error=False)


async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)], db: DbSession) -> User:
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise ValueError("Token type is not access.")
        user_id = int(payload.get("sub"))
    except (jwt.PyJWTError, TypeError, ValueError):
        raise AppHTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "INVALID_TOKEN",
            "Invalid access token.",
        ) from None

    # Check token blacklist (logout invalidation)
    from app.services.auth_service import is_token_blacklisted

    jti = payload.get("jti")
    if not jti:
        logging.getLogger(__name__).warning(
            "Token accepted without jti — cannot be revoked (pre-rollout token?)."
        )
    elif is_token_blacklisted(db, jti):
        raise AppHTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "TOKEN_REVOKED",
            "Access token has been revoked.",
        )

    user = db.scalar(select(User).where(User.id == user_id))
    if not user or user.status != ACTIVE:
        raise AppHTTPException(
            status.HTTP_401_UNAUTHORIZED, "USER_NOT_ACTIVE", "User is not active."
        )

    # Check whether the token was issued before the last password change.
    from app.services.auth_service import is_token_stale_for_password_change

    token_iat = float(payload.get("iat", 0))
    if token_iat and is_token_stale_for_password_change(db, user_id, token_iat):
        raise AppHTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "TOKEN_REVOKED",
            "Access token has been revoked (password changed).",
        )

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_user_from_query(
    request: Request,
    token: Annotated[str | None, Depends(oauth2_scheme)] = None,
    db: Session = Depends(get_db),
) -> User:
    """与 get_current_user 相同，但优先从 ?token= 查询参数取 JWT。

    EventSource / SSE 端点无法自定义请求头，通过 ?token=<jwt> 传递鉴权。
    Authorization header 仍然优先（curl / axios 等正常客户端）。
    """
    # 优先 Authorization header，其次 ?token= 查询参数
    resolved = token
    if not resolved:
        resolved = request.query_params.get("token")
    if not resolved:
        raise AppHTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "INVALID_TOKEN",
            "No access token provided (use Authorization header or ?token= query parameter).",
        )

    # 余下逻辑与 get_current_user 完全一致
    try:
        payload = decode_token(resolved)
        if payload.get("type") != "access":
            raise ValueError("Token type is not access.")
        user_id = int(payload.get("sub"))
    except (jwt.PyJWTError, TypeError, ValueError):
        raise AppHTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "INVALID_TOKEN",
            "Invalid access token.",
        ) from None

    from app.services.auth_service import is_token_blacklisted

    jti = payload.get("jti")
    if not jti:
        logging.getLogger(__name__).warning(
            "Token accepted without jti — cannot be revoked (pre-rollout token?)."
        )
    elif is_token_blacklisted(db, jti):
        raise AppHTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "TOKEN_REVOKED",
            "Access token has been revoked.",
        )

    user = db.scalar(select(User).where(User.id == user_id))
    if not user or user.status != ACTIVE:
        raise AppHTTPException(
            status.HTTP_401_UNAUTHORIZED, "USER_NOT_ACTIVE", "User is not active."
        )

    from app.services.auth_service import is_token_stale_for_password_change

    token_iat = float(payload.get("iat", 0))
    if token_iat and is_token_stale_for_password_change(db, user_id, token_iat):
        raise AppHTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "TOKEN_REVOKED",
            "Access token has been revoked (password changed).",
        )

    return user


# EventSource / SSE 兼容的 CurrentUser — 支持 Authorization header 和 ?token= 查询参数
CurrentUserOrQuery = Annotated[User, Depends(get_current_user_from_query)]


async def get_raw_access_token(token: Annotated[str, Depends(oauth2_scheme)]) -> str:
    """Return the raw JWT access token string — for logout blacklisting."""
    return token


def require_roles(*allowed_roles: str):
    def dependency(current_user: CurrentUser) -> User:
        role_codes = user_role_codes(current_user)
        if ROLE_SUPER_ADMIN in role_codes or set(allowed_roles).intersection(role_codes):
            return current_user
        raise forbidden()

    return dependency
