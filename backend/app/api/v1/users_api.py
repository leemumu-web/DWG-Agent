from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_roles, user_role_codes
from app.core.constants import ACTIVE, DELETED, DISABLED, ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.core.exceptions import AppHTTPException, forbidden, not_found
from app.core.security import hash_password
from app.models.role import Role
from app.models.user import User
from app.schemas.common import ok, page_from_list
from app.schemas.user_schema import AssignRoleRequest, UserCreate, UserRead, UserUpdate
from app.services.audit_service import write_audit_log
from app.services.user_service import create_user, get_user_or_404, update_user

router = APIRouter()


def _require_super_admin_role_manager(current_user: User, role_code: str) -> None:
    if role_code == ROLE_SUPER_ADMIN and ROLE_SUPER_ADMIN not in user_role_codes(current_user):
        raise forbidden("Only super_admin can manage the super_admin role.")


@router.get("")
def list_users(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(ROLE_ADMIN)),
):
    users = list(
        db.scalars(select(User).where(User.status != DELETED).order_by(User.id.desc())).all()
    )
    return page_from_list(
        [UserRead.model_validate(u) for u in users], page, page_size, request.state.request_id
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_user_api(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(ROLE_ADMIN)),
):
    for role_code in payload.role_codes:
        _require_super_admin_role_manager(current_user, role_code)
    user = create_user(db, payload)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="users.create",
        resource_type="user",
        resource_id=user.id,
        after_json={"username": user.username},
    )
    db.commit()
    return ok(UserRead.model_validate(user), request.state.request_id)


@router.get("/{user_id}")
def get_user_api(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(ROLE_ADMIN)),
):
    return ok(UserRead.model_validate(get_user_or_404(db, user_id)), request.state.request_id)


@router.patch("/{user_id}")
def update_user_api(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(ROLE_ADMIN)),
):
    user = get_user_or_404(db, user_id)
    if user.id == current_user.id and payload.status is not None and payload.status != ACTIVE:
        raise AppHTTPException(
            400, "CANNOT_DISABLE_SELF", "Admin cannot disable their own account."
        )
    before = {"real_name": user.real_name, "email": user.email, "status": user.status}
    update_user(db, user, payload)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="users.update",
        resource_type="user",
        resource_id=user.id,
        before_json=before,
        after_json=payload.model_dump(exclude_unset=True),
    )
    db.commit()
    return ok(UserRead.model_validate(user), request.state.request_id)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_api(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(ROLE_ADMIN)),
):
    user = get_user_or_404(db, user_id)
    if user.id == current_user.id:
        raise AppHTTPException(400, "CANNOT_DELETE_SELF", "Admin cannot delete their own account.")
    user.status = DELETED
    user.deleted_at = datetime.now(UTC)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="users.delete",
        resource_type="user",
        resource_id=user.id,
    )
    db.commit()
    return None


@router.post("/{user_id}/roles", status_code=status.HTTP_201_CREATED)
def assign_role(
    user_id: int,
    payload: AssignRoleRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(ROLE_ADMIN)),
):
    user = get_user_or_404(db, user_id)
    role = db.scalar(select(Role).where(Role.code == payload.role_code))
    if not role:
        raise not_found("Role")
    _require_super_admin_role_manager(current_user, role.code)
    if role not in user.roles:
        user.roles.append(role)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="users.roles.add",
        resource_type="user",
        resource_id=user.id,
        after_json={"role_code": role.code},
    )
    db.commit()
    return ok(UserRead.model_validate(user), request.state.request_id)


@router.delete("/{user_id}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_role(
    user_id: int,
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(ROLE_ADMIN)),
):
    user = get_user_or_404(db, user_id)
    role = db.get(Role, role_id)
    if not role:
        raise not_found("Role")
    _require_super_admin_role_manager(current_user, role.code)
    if user.id == current_user.id:
        raise AppHTTPException(
            400, "CANNOT_REMOVE_OWN_ROLE", "Admin cannot remove roles from their own account."
        )
    if role in user.roles:
        user.roles.remove(role)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="users.roles.remove",
        resource_type="user",
        resource_id=user.id,
        after_json={"role_id": role_id},
    )
    db.commit()
    return None


@router.post("/{user_id}/password-reset-requests", status_code=status.HTTP_200_OK)
def reset_user_password(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(ROLE_ADMIN)),
):
    """Admin-initiated password reset. Sets a temporary password that user must change."""
    user = get_user_or_404(db, user_id)
    if user.status == DELETED:
        raise AppHTTPException(400, "USER_DELETED", "Cannot reset password for a deleted user.")
    temp_password = f"temp-{uuid4().hex[:12]}"
    user.password_hash = hash_password(temp_password)
    user.password_algo = "argon2id"
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="users.password_reset",
        resource_type="user",
        resource_id=user.id,
    )
    db.commit()
    return ok(
        {
            "user_id": user.id,
            "temp_password": temp_password,
            "message": "Password has been reset. User must change on next login.",
        },
        request.state.request_id,
    )


@router.post("/{user_id}/disable-requests", status_code=status.HTTP_200_OK)
def disable_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(ROLE_ADMIN)),
):
    """Disable a user account. Disabled users cannot authenticate."""
    user = get_user_or_404(db, user_id)
    if user.id == current_user.id:
        raise AppHTTPException(
            400, "CANNOT_DISABLE_SELF", "Admin cannot disable their own account."
        )
    if user.status == DELETED:
        raise AppHTTPException(400, "USER_DELETED", "Cannot disable a deleted user.")
    before = {"status": user.status}
    user.status = DISABLED
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="users.disable",
        resource_type="user",
        resource_id=user.id,
        before_json=before,
        after_json={"status": DISABLED},
    )
    db.commit()
    return ok(UserRead.model_validate(user), request.state.request_id)


@router.post("/{user_id}/enable-requests", status_code=status.HTTP_200_OK)
def enable_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(ROLE_ADMIN)),
):
    """Re-enable a previously disabled user account."""
    user = get_user_or_404(db, user_id)
    if user.status == DELETED:
        raise AppHTTPException(400, "USER_DELETED", "Cannot enable a deleted user.")
    before = {"status": user.status}
    user.status = ACTIVE
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="users.enable",
        resource_type="user",
        resource_id=user.id,
        before_json=before,
        after_json={"status": ACTIVE},
    )
    db.commit()
    return ok(UserRead.model_validate(user), request.state.request_id)
