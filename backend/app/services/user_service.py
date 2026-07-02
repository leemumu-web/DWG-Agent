from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import ACTIVE, DELETED
from app.core.exceptions import AppHTTPException, not_found
from app.core.security import hash_password
from app.models.role import Role
from app.models.user import User
from app.schemas.user_schema import UserCreate, UserUpdate


def create_user(db: Session, payload: UserCreate) -> User:
    exists = db.scalar(select(User).where(User.username == payload.username))
    if exists:
        raise AppHTTPException(409, "USERNAME_EXISTS", "Username already exists.")
    user = User(
        username=payload.username,
        employee_no=payload.employee_no,
        real_name=payload.real_name,
        email=str(payload.email) if payload.email else None,
        password_hash=hash_password(payload.password),
        status=ACTIVE,
    )
    if payload.role_codes:
        roles = list(db.scalars(select(Role).where(Role.code.in_(payload.role_codes))).all())
        user.roles.extend(roles)
    db.add(user)
    db.flush()
    return user


def get_user_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if not user or user.status == DELETED:
        raise not_found("User")
    return user


def update_user(db: Session, user: User, payload: UserUpdate) -> User:
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(user, key, str(value) if key == "email" and value else value)
    db.flush()
    return user
