from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_roles
from app.core.constants import ROLE_SUPER_ADMIN
from app.core.exceptions import AppHTTPException, not_found
from app.models.role import Permission, Role
from app.models.user import User
from app.schemas.common import ok, page
from app.schemas.user_schema import (
    PermissionRead,
    ReplaceRolePermissionsRequest,
    RoleCreate,
    RoleRead,
)
from app.services.audit_service import write_audit_log

router = APIRouter()


@router.get("/roles")
def list_roles(request: Request, db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_SUPER_ADMIN, "admin"))):
    roles = list(db.scalars(select(Role).order_by(Role.id)).all())
    return page([RoleRead.model_validate(r) for r in roles], 1, len(roles), len(roles), request.state.request_id)


@router.post("/roles", status_code=status.HTTP_201_CREATED)
def create_role(payload: RoleCreate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_roles(ROLE_SUPER_ADMIN))):
    if db.scalar(select(Role).where(Role.code == payload.code)):
        raise AppHTTPException(409, "ROLE_EXISTS", "Role code already exists.")
    role = Role(code=payload.code, name=payload.name, description=payload.description, is_system=False)
    db.add(role)
    db.flush()
    write_audit_log(db, actor_user_id=current_user.id, action="roles.create", resource_type="role", resource_id=role.id, after_json=payload.model_dump())
    db.commit()
    return ok(RoleRead.model_validate(role), request.state.request_id)


@router.get("/permissions")
def list_permissions(request: Request, db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_SUPER_ADMIN, "admin"))):
    permissions = list(db.scalars(select(Permission).order_by(Permission.code)).all())
    return page([PermissionRead.model_validate(p) for p in permissions], 1, len(permissions), len(permissions), request.state.request_id)


@router.put("/roles/{role_id}/permissions")
def replace_role_permissions(role_id: int, payload: ReplaceRolePermissionsRequest, request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_roles(ROLE_SUPER_ADMIN))):
    role = db.get(Role, role_id)
    if not role:
        raise not_found("Role")
    permissions = list(db.scalars(select(Permission).where(Permission.code.in_(payload.permission_codes))).all())
    role.permissions = permissions
    write_audit_log(db, actor_user_id=current_user.id, action="roles.permissions.replace", resource_type="role", resource_id=role.id, after_json=payload.model_dump())
    db.commit()
    return ok(RoleRead.model_validate(role), request.state.request_id)
