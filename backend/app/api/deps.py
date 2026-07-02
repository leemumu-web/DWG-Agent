from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import ACTIVE, ROLE_SUPER_ADMIN
from app.core.exceptions import AppHTTPException, forbidden
from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import User

DbSession = Annotated[Session, Depends(get_db)]
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_v1_prefix}/auth/sessions")


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)], db: DbSession) -> User:
    try:
        payload = decode_token(token)
        user_id = int(payload.get("sub"))
    except (jwt.PyJWTError, TypeError, ValueError):
        raise AppHTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "INVALID_TOKEN",
            "Invalid access token.",
        ) from None

    user = db.scalar(select(User).where(User.id == user_id))
    if not user or user.status != ACTIVE:
        raise AppHTTPException(status.HTTP_401_UNAUTHORIZED, "USER_NOT_ACTIVE", "User is not active.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def user_role_codes(user: User) -> set[str]:
    return {role.code for role in user.roles}


def require_roles(*allowed_roles: str):
    def dependency(current_user: CurrentUser) -> User:
        role_codes = user_role_codes(current_user)
        if ROLE_SUPER_ADMIN in role_codes or set(allowed_roles).intersection(role_codes):
            return current_user
        raise forbidden()

    return dependency
