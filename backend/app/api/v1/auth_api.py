from __future__ import annotations

import jwt
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_db
from app.core.config import settings
from app.core.constants import ACTIVE
from app.core.exceptions import AppHTTPException
from app.core.security import decode_token, hash_password, verify_password
from app.models.user import User
from app.schemas.auth_schema import ChangePasswordRequest, LoginRequest, LoginResponse
from app.schemas.common import ok
from app.schemas.user_schema import UserRead
from app.services.audit_service import write_audit_log
from app.services.auth_service import authenticate_user, build_login_token, build_refresh_token

router = APIRouter()
REFRESH_COOKIE_NAME = "dwg_refresh_token"
REFRESH_COOKIE_PATH = f"{settings.api_v1_prefix}/auth"


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        token,
        max_age=settings.jwt_refresh_token_expire_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_session(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, payload.username, payload.password)
    if not user:
        raise AppHTTPException(
            status.HTTP_401_UNAUTHORIZED, "INVALID_CREDENTIALS", "Invalid username or password."
        )
    token = build_login_token(user)
    _set_refresh_cookie(response, build_refresh_token(user))
    write_audit_log(
        db, actor_user_id=user.id, action="auth.login", resource_type="user", resource_id=user.id
    )
    db.commit()
    data = LoginResponse(
        access_token=token,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        user=UserRead.model_validate(user),
    )
    return ok(data, request.state.request_id)


@router.delete("/sessions/current", status_code=status.HTTP_204_NO_CONTENT)
def delete_current_session(
    response: Response, current_user: CurrentUser, db: Session = Depends(get_db)
):
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="auth.logout",
        resource_type="user",
        resource_id=current_user.id,
    )
    db.commit()
    _clear_refresh_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return None


@router.post("/tokens/refresh")
def refresh_token(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not token:
        raise AppHTTPException(
            status.HTTP_401_UNAUTHORIZED, "INVALID_TOKEN", "Refresh token is missing."
        )
    try:
        payload = decode_token(token)
        if payload.get("type") != "refresh":
            raise ValueError("Token type is not refresh.")
        user_id = int(payload.get("sub"))
    except (jwt.PyJWTError, TypeError, ValueError):
        raise AppHTTPException(
            status.HTTP_401_UNAUTHORIZED, "INVALID_TOKEN", "Invalid refresh token."
        ) from None

    user = db.scalar(select(User).where(User.id == user_id))
    if not user or user.status != ACTIVE:
        raise AppHTTPException(
            status.HTTP_401_UNAUTHORIZED, "USER_NOT_ACTIVE", "User is not active."
        )

    data = LoginResponse(
        access_token=build_login_token(user),
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        user=UserRead.model_validate(user),
    )
    return ok(data, request.state.request_id)


@router.get("/me")
def get_me(request: Request, current_user: CurrentUser):
    return ok(UserRead.model_validate(current_user), request.state.request_id)


@router.patch("/password")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, current_user.password_hash):
        raise AppHTTPException(
            status.HTTP_400_BAD_REQUEST,
            "INVALID_CURRENT_PASSWORD",
            "Current password is incorrect.",
        )
    current_user.password_hash = hash_password(payload.new_password)
    current_user.password_algo = "argon2id"
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="auth.password_change",
        resource_type="user",
        resource_id=current_user.id,
    )
    db.commit()
    return ok({"changed": True}, request.state.request_id)
