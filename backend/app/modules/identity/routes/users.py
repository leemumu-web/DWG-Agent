"""User administration HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.identity.access import is_admin, require_roles, user_role_codes
from app.modules.identity.authentication import record_password_change
from app.modules.identity.dependencies import get_current_user
from app.modules.identity.models.role import Role, user_roles
from app.modules.identity.models.user import User
from app.modules.identity.schemas.user import (
    AdminPasswordResetRequest,
    AssignRoleRequest,
    UserCreate,
    UserRead,
    UserSelfUpdate,
    UserUpdate,
)
from app.modules.identity.users import (
    create_user,
    get_user_or_404,
    transition_user_status,
    update_user,
)
from app.modules.identity.users import reset_user_password as _reset_user_password_svc
from app.modules.operations.audit.interface import write_audit_log
from app.platform.config.constants import ACTIVE, DELETED, DISABLED, ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.platform.config.validators import validate_sort_by
from app.platform.database.pagination import paginate_scalars
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.envelopes import page as page_response
from app.platform.http.exceptions import AppHTTPException, forbidden, not_found

router = APIRouter()


_SUPER_ADMIN_ACCOUNT_PROTECTED = AppHTTPException(
    400,
    "SUPER_ADMIN_ACCOUNT_PROTECTED",
    "The sole super_admin account cannot be disabled, deleted, demoted, or taken over.",
)


def _is_super_admin(user: User) -> bool:
    return ROLE_SUPER_ADMIN in user_role_codes(user)


def _protect_super_admin_account(target_user: User) -> None:
    if _is_super_admin(target_user):
        raise _SUPER_ADMIN_ACCOUNT_PROTECTED


@router.get("")
def list_users(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc", pattern=r"^(asc|desc)$"),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(ROLE_ADMIN)),
):
    sort_column = validate_sort_by("users", sort_by)
    sort_dir_value = sort_dir.strip().lower()
    order_clause = getattr(User, sort_column)
    if sort_dir_value == "asc":
        order_clause = order_clause.asc()
    else:
        order_clause = order_clause.desc()
    tie_breaker = User.id.asc() if sort_dir_value == "asc" else User.id.desc()
    users, total = paginate_scalars(
        db,
        select(User).where(User.status != DELETED).order_by(order_clause, tie_breaker),
        page_no=page,
        page_size=page_size,
    )
    return page_response(
        [UserRead.model_validate(u) for u in users],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_user_api(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(ROLE_ADMIN)),
):
    """Create a user (roles must be assigned separately via ``POST /users/{id}/roles``)."""
    user = create_user(db, payload)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="users.create",
        resource_type="user",
        resource_id=user.id,
        after_json={"username": user.username},
        request=request,
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



@router.patch("/me")
def update_self(
    payload: UserSelfUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Allow any authenticated user to update their own real_name and email."""
    user = get_user_or_404(db, current_user.id)
    before = {"real_name": user.real_name, "email": user.email}
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(user, key, str(value) if key == "email" else value)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="users.update_self",
        resource_type="user",
        resource_id=user.id,
        before_json=before,
        after_json=payload.model_dump(exclude_unset=True),
        request=request,
    )
    db.commit()
    return ok(UserRead.model_validate(user), request.state.request_id)
@router.patch("/{user_id}")
def update_user_api(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(ROLE_ADMIN)),
):
    user = get_user_or_404(db, user_id, for_update=True)
    if payload.status == DISABLED:
        _protect_super_admin_account(user)
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
        request=request,
    )
    db.commit()
    return ok(UserRead.model_validate(user), request.state.request_id)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_api(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(ROLE_ADMIN)),
):
    user = get_user_or_404(db, user_id)
    _protect_super_admin_account(user)
    if user.id == current_user.id:
        raise AppHTTPException(400, "CANNOT_DELETE_SELF", "Admin cannot delete their own account.")
    if not transition_user_status(db, user_id, DELETED, set_deleted_at=True):
        raise AppHTTPException(400, "USER_DELETED", "User has already been deleted.")
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="users.delete",
        resource_type="user",
        resource_id=user_id,
        request=request,
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
    # Serialize grants of the singleton role to close the concurrent-request gap.
    role = db.scalar(
        select(Role).where(Role.code == payload.role_code).with_for_update()
    )
    if not role:
        raise not_found("Role")
    if role.code == ROLE_SUPER_ADMIN:
        if not _is_super_admin(current_user):
            raise AppHTTPException(
                403,
                "SUPER_ADMIN_ASSIGNMENT_FORBIDDEN",
                "Only the sole super_admin may manage this protected role.",
            )
        existing_user_id = db.scalar(
            select(user_roles.c.user_id)
            .where(user_roles.c.role_id == role.id)
            .limit(1)
        )
        if existing_user_id is not None and int(existing_user_id) != user.id:
            raise AppHTTPException(
                409,
                "SUPER_ADMIN_SINGLETON",
                "The system permits exactly one super_admin account.",
            )
    if role not in user.roles:
        user.roles.append(role)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="users.roles.add",
        resource_type="user",
        resource_id=user.id,
        after_json={"role_code": role.code},
        request=request,
    )
    db.commit()
    return ok(UserRead.model_validate(user), request.state.request_id)


@router.delete("/{user_id}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_role(
    user_id: int,
    role_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(ROLE_ADMIN)),
):
    user = get_user_or_404(db, user_id)
    role = db.get(Role, role_id)
    if not role:
        raise not_found("Role")
    if role.code == ROLE_SUPER_ADMIN:
        raise _SUPER_ADMIN_ACCOUNT_PROTECTED
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
        request=request,
    )
    db.commit()
    return None


@router.post("/{user_id}/password-reset-requests", status_code=status.HTTP_200_OK)
def reset_user_password(
    user_id: int,
    payload: AdminPasswordResetRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Reset to the exact validated password supplied by an administrator."""
    user = get_user_or_404(db, user_id)
    # Authorisation: only admin/super_admin may reset passwords (§8.3)
    if not is_admin(current_user):
        if current_user.id == user_id:
            raise AppHTTPException(
                400,
                "SELF_RESET_NOT_IMPLEMENTED",
                "Self-service password reset is not yet implemented. Please contact an administrator.",
            )
        raise forbidden()
    if _is_super_admin(user) and current_user.id != user.id:
        raise _SUPER_ADMIN_ACCOUNT_PROTECTED
    if user.status == DELETED:
        raise AppHTTPException(400, "USER_DELETED", "Cannot reset password for a deleted user.")
    _reset_user_password_svc(db, user, payload.new_password)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="users.password_reset",
        resource_type="user",
        resource_id=user.id,
        request=request,
    )
    # Persist the reset, audit record and revocation marker atomically.
    record_password_change(db, user.id)
    db.commit()

    return ok(
        {
            "user_id": user.id,
            "message": "Password has been reset and existing sessions were revoked.",
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
    _protect_super_admin_account(user)
    if user.id == current_user.id:
        raise AppHTTPException(
            400, "CANNOT_DISABLE_SELF", "Admin cannot disable their own account."
        )
    before = {"status": user.status}
    if not transition_user_status(db, user_id, DISABLED):
        raise AppHTTPException(400, "USER_DELETED", "Cannot disable a deleted user.")
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="users.disable",
        resource_type="user",
        resource_id=user_id,
        before_json=before,
        after_json={"status": DISABLED},
        request=request,
    )
    db.commit()
    db.refresh(user)
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
    before = {"status": user.status}
    if not transition_user_status(db, user_id, ACTIVE):
        raise AppHTTPException(400, "USER_DELETED", "Cannot enable a deleted user.")
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="users.enable",
        resource_type="user",
        resource_id=user_id,
        before_json=before,
        after_json={"status": ACTIVE},
        request=request,
    )
    db.commit()
    db.refresh(user)
    return ok(UserRead.model_validate(user), request.state.request_id)
