from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from backend.app.api.deps import CurrentUser, get_db
from backend.app.core.config import settings
from backend.app.core.exceptions import AppHTTPException
from backend.app.schemas.auth_schema import LoginRequest, LoginResponse
from backend.app.schemas.common import ok
from backend.app.schemas.user_schema import UserRead
from backend.app.services.audit_service import write_audit_log
from backend.app.services.auth_service import authenticate_user, build_login_token

router = APIRouter()


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_session(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = authenticate_user(db, payload.username, payload.password)
    if not user:
        raise AppHTTPException(status.HTTP_401_UNAUTHORIZED, "INVALID_CREDENTIALS", "Invalid username or password.")
    token = build_login_token(user)
    write_audit_log(db, actor_user_id=user.id, action="auth.login", resource_type="user", resource_id=user.id)
    db.commit()
    data = LoginResponse(
        access_token=token,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        user=UserRead.model_validate(user),
    )
    return ok(data, request.state.request_id)


@router.delete("/sessions/current", status_code=status.HTTP_204_NO_CONTENT)
def delete_current_session(response: Response, current_user: CurrentUser, db: Session = Depends(get_db)):
    write_audit_log(db, actor_user_id=current_user.id, action="auth.logout", resource_type="user", resource_id=current_user.id)
    db.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return None


@router.post("/tokens/refresh")
def refresh_token(request: Request):
    raise AppHTTPException(status.HTTP_501_NOT_IMPLEMENTED, "REFRESH_TOKEN_NOT_IMPLEMENTED", "Refresh token is not implemented in local skeleton.")


@router.get("/me")
def get_me(request: Request, current_user: CurrentUser):
    return ok(UserRead.model_validate(current_user), request.state.request_id)


@router.patch("/password")
def change_password(request: Request):
    raise AppHTTPException(status.HTTP_501_NOT_IMPLEMENTED, "PASSWORD_CHANGE_NOT_IMPLEMENTED", "Password change will be implemented after account policy is finalized.")
