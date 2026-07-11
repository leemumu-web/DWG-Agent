from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    get_current_user,
    get_db,
    is_admin,
    require_roles,
    user_role_codes,
)
from app.core.constants import ACTIVE, DELETED, DISABLED, ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.core.exceptions import AppHTTPException, forbidden, not_found
from app.core.validators import validate_sort_by
from app.db.pagination import paginate_scalars
from app.models.role import Role
from app.models.user import User
from app.schemas.common import ok
from app.schemas.common import page as page_response
from app.schemas.user_schema import (
    AssignRoleRequest,
    UserCreate,
    UserRead,
    UserSelfUpdate,
    UserUpdate,
)
from app.services.audit_service import write_audit_log
from app.services.auth_service import record_password_change
from app.services.user_service import (
    create_user,
    get_user_or_404,
    transition_user_status,
    update_user,
)
from app.services.user_service import (
    reset_user_password as _reset_user_password_svc,
)

router = APIRouter()


def _require_super_admin_role_manager(current_user: User, role_code: str) -> None:
    if role_code == ROLE_SUPER_ADMIN and ROLE_SUPER_ADMIN not in user_role_codes(current_user):
        raise forbidden("Only super_admin can manage the super_admin role.")


_CANNOT_MANAGE_SUPER_ADMIN = AppHTTPException(
    400,
    "CANNOT_MANAGE_SUPER_ADMIN",
    "Only super_admin can manage super_admin accounts.",
)


def _require_super_admin_target(db: Session, current_user: User, target_user: User) -> None:
    """Raise if *current_user* is not super_admin but *target_user* is."""
    if ROLE_SUPER_ADMIN not in user_role_codes(current_user) and ROLE_SUPER_ADMIN in user_role_codes(
        target_user
    ):
        raise _CANNOT_MANAGE_SUPER_ADMIN


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
    _require_super_admin_target(db, current_user, user)
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
    _require_super_admin_target(db, current_user, user)
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
        request=request,
    )
    db.commit()
    return None


@router.post("/{user_id}/password-reset-requests", status_code=status.HTTP_200_OK)
def reset_user_password(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Admin-initiated password reset. Sets a temporary password that user must change."""
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
    _require_super_admin_target(db, current_user, user)
    if user.status == DELETED:
        raise AppHTTPException(400, "USER_DELETED", "Cannot reset password for a deleted user.")
    temp_password = _reset_user_password_svc(db, user)
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
    _require_super_admin_target(db, current_user, user)
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
    _require_super_admin_target(db, current_user, user)
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
