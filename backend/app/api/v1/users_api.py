from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.deps import get_db, require_roles
from backend.app.core.constants import DELETED, ROLE_ADMIN
from backend.app.core.exceptions import not_found
from backend.app.models.role import Role
from backend.app.models.user import User
from backend.app.schemas.common import ok, page
from backend.app.schemas.user_schema import AssignRoleRequest, UserCreate, UserRead, UserUpdate
from backend.app.services.audit_service import write_audit_log
from backend.app.services.user_service import create_user, get_user_or_404, update_user

router = APIRouter()


@router.get("")
def list_users(request: Request, db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    users = list(db.scalars(select(User).where(User.status != DELETED).order_by(User.id.desc())).all())
    return page([UserRead.model_validate(u) for u in users], 1, len(users), len(users), request.state.request_id)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_user_api(payload: UserCreate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_roles(ROLE_ADMIN))):
    user = create_user(db, payload)
    write_audit_log(db, actor_user_id=current_user.id, action="users.create", resource_type="user", resource_id=user.id, after_json={"username": user.username})
    db.commit()
    return ok(UserRead.model_validate(user), request.state.request_id)


@router.get("/{user_id}")
def get_user_api(user_id: int, request: Request, db: Session = Depends(get_db), _: User = Depends(require_roles(ROLE_ADMIN))):
    return ok(UserRead.model_validate(get_user_or_404(db, user_id)), request.state.request_id)


@router.patch("/{user_id}")
def update_user_api(user_id: int, payload: UserUpdate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_roles(ROLE_ADMIN))):
    user = get_user_or_404(db, user_id)
    before = {"real_name": user.real_name, "email": user.email, "status": user.status}
    update_user(db, user, payload)
    write_audit_log(db, actor_user_id=current_user.id, action="users.update", resource_type="user", resource_id=user.id, before_json=before, after_json=payload.model_dump(exclude_unset=True))
    db.commit()
    return ok(UserRead.model_validate(user), request.state.request_id)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_api(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_roles(ROLE_ADMIN))):
    user = get_user_or_404(db, user_id)
    user.status = DELETED
    user.deleted_at = datetime.now(UTC)
    write_audit_log(db, actor_user_id=current_user.id, action="users.delete", resource_type="user", resource_id=user.id)
    db.commit()
    return None


@router.post("/{user_id}/roles", status_code=status.HTTP_201_CREATED)
def assign_role(user_id: int, payload: AssignRoleRequest, request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_roles(ROLE_ADMIN))):
    user = get_user_or_404(db, user_id)
    role = db.scalar(select(Role).where(Role.code == payload.role_code))
    if not role:
        raise not_found("Role")
    if role not in user.roles:
        user.roles.append(role)
    write_audit_log(db, actor_user_id=current_user.id, action="users.roles.add", resource_type="user", resource_id=user.id, after_json={"role_code": role.code})
    db.commit()
    return ok(UserRead.model_validate(user), request.state.request_id)


@router.delete("/{user_id}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_role(user_id: int, role_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_roles(ROLE_ADMIN))):
    user = get_user_or_404(db, user_id)
    role = db.get(Role, role_id)
    if role and role in user.roles:
        user.roles.remove(role)
    write_audit_log(db, actor_user_id=current_user.id, action="users.roles.remove", resource_type="user", resource_id=user.id, after_json={"role_id": role_id})
    db.commit()
    return None
