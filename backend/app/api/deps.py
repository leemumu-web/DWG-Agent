from __future__ import annotations

import logging
from typing import Annotated

import jwt
from fastapi import Depends, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import ACTIVE, ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.core.exceptions import AppHTTPException, forbidden, not_found
from app.core.security import decode_token
from app.db.session import get_db
from app.models.project import Project, ProjectMember
from app.models.user import User

DbSession = Annotated[Session, Depends(get_db)]
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_v1_prefix}/auth/sessions")


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
    elif is_token_blacklisted(jti):
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

    token_iat = int(payload.get("iat", 0))
    if token_iat and is_token_stale_for_password_change(user_id, token_iat):
        raise AppHTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "TOKEN_REVOKED",
            "Access token has been revoked (password changed).",
        )

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_raw_access_token(token: Annotated[str, Depends(oauth2_scheme)]) -> str:
    """Return the raw JWT access token string — for logout blacklisting."""
    return token


def user_role_codes(user: User) -> set[str]:
    return {role.code for role in user.roles}


def has_global_project_access(user: User) -> bool:
    return bool({ROLE_SUPER_ADMIN, ROLE_ADMIN}.intersection(user_role_codes(user)))


def is_admin(user: User) -> bool:
    """Return True if the user holds admin or super_admin global permissions (§8.3)."""
    return has_global_project_access(user)


def get_project_membership(db: Session, user: User, project_id: int) -> ProjectMember | None:
    return db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
        )
    )


def require_project_member(db: Session, user: User, project_id: int) -> ProjectMember | None:
    if has_global_project_access(user):
        return None
    require_active_project(db, project_id)
    member = get_project_membership(db, user, project_id)
    if not member:
        raise forbidden("Project membership is required.")
    return member


def require_active_project(db: Session, project_id: int) -> None:
    """Raise 404 if the project does not exist or has been soft-deleted."""
    project = db.get(Project, project_id)
    if not project or project.status == "deleted":
        raise not_found("Project")


def require_project_role(
    db: Session,
    user: User,
    project_id: int,
    allowed_project_roles: set[str],
) -> ProjectMember | None:
    if has_global_project_access(user):
        return None
    member = require_project_member(db, user, project_id)
    if member and member.project_role in allowed_project_roles:
        return member
    raise forbidden("Project role is not allowed for this action.")


def require_roles(*allowed_roles: str):
    def dependency(current_user: CurrentUser) -> User:
        role_codes = user_role_codes(current_user)
        if ROLE_SUPER_ADMIN in role_codes or set(allowed_roles).intersection(role_codes):
            return current_user
        raise forbidden()

    return dependency
