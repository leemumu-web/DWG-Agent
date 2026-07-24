"""Role and permission administration HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.identity.access import require_roles
from app.modules.identity.models.role import Permission, Role
from app.modules.identity.models.user import User
from app.modules.identity.schemas.user import (
    PermissionRead,
    ReplaceRolePermissionsRequest,
    RoleCreate,
    RoleRead,
)
from app.modules.operations.audit.interface import write_audit_log
from app.platform.config.constants import ROLE_ADMIN, ROLE_SUPER_ADMIN, ROLE_ADMIN
from app.platform.database.pagination import paginate_scalars
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.envelopes import page as page_response
from app.platform.http.exceptions import AppHTTPException, not_found

router = APIRouter()


@router.get("/roles")
def list_roles(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(ROLE_SUPER_ADMIN, ROLE_ADMIN)),
):
    roles, total = paginate_scalars(
        db,
        select(Role).options(selectinload(Role.permissions)).order_by(Role.id),
        page_no=page,
        page_size=page_size,
    )
    return page_response(
        [RoleRead.model_validate(r) for r in roles],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@router.post("/roles", status_code=status.HTTP_201_CREATED)
def create_role(
    payload: RoleCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(ROLE_SUPER_ADMIN, ROLE_ADMIN)),
):
    if db.scalar(select(Role).where(Role.code == payload.code)):
        raise AppHTTPException(409, "ROLE_EXISTS", "Role code already exists.")
    role = Role(
        code=payload.code, name=payload.name, description=payload.description, is_system=False
    )
    db.add(role)
    db.flush()
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="roles.create",
        resource_type="role",
        resource_id=role.id,
        after_json=payload.model_dump(),
        request=request,
    )
    db.commit()
    return ok(RoleRead.model_validate(role), request.state.request_id)


@router.get("/permissions")
def list_permissions(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(ROLE_SUPER_ADMIN, ROLE_ADMIN)),
):
    permissions, total = paginate_scalars(
        db,
        select(Permission).order_by(Permission.code, Permission.id),
        page_no=page,
        page_size=page_size,
    )
    return page_response(
        [PermissionRead.model_validate(p) for p in permissions],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@router.put("/roles/{role_id}/permissions")
def replace_role_permissions(
    role_id: int,
    payload: ReplaceRolePermissionsRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(ROLE_SUPER_ADMIN, ROLE_ADMIN)),
):
    role = db.get(Role, role_id)
    if not role:
        raise not_found("Role")
    permissions = list(
        db.scalars(select(Permission).where(Permission.code.in_(payload.permission_codes))).all()
    )
    role.permissions = permissions
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="roles.permissions.replace",
        resource_type="role",
        resource_id=role.id,
        after_json=payload.model_dump(),
        request=request,
    )
    db.commit()
    return ok(RoleRead.model_validate(role), request.state.request_id)
