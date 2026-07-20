from __future__ import annotations

import secrets
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.user import User
from app.platform.config.constants import ACTIVE, DELETED
from app.platform.http.exceptions import AppHTTPException, not_found
from app.platform.security.tokens import hash_password
from app.schemas.user_schema import UserCreate, UserSelfUpdate, UserUpdate


def create_user(db: Session, payload: UserCreate) -> User:
    """Create a user from *payload*.

    Role assignment is handled separately via ``POST /users/{id}/roles`` so
    that every role grant passes through the RBAC checks in the API layer.
    """
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
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise AppHTTPException(409, "USERNAME_EXISTS", "Username already exists.") from None
    return user


def get_user_or_404(db: Session, user_id: int, *, for_update: bool = False) -> User:
    if for_update:
        user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    else:
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


def update_user_self(db: Session, user: User, payload: UserSelfUpdate) -> User:
    """Apply safe self-service profile fields — no status changes allowed."""
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(user, key, str(value) if key == "email" and value else value)
    db.flush()
    return user


def transition_user_status(
    db: Session, user_id: int, to_status: str, *, set_deleted_at: bool = False
) -> bool:
    """Atomically update a user's status — returns True if a row was updated.

    Uses ``UPDATE ... WHERE status != 'deleted'`` to eliminate the TOCTOU
    race window between the SELECT and UPDATE in disable/delete/enable flows.
    """
    values: dict = {"status": to_status}
    if set_deleted_at:
        values["deleted_at"] = datetime.now(UTC)
    result = db.execute(
        update(User).where(User.id == user_id, User.status != DELETED).values(**values)
    )
    return result.rowcount > 0


def reset_user_password(db: Session, user: User) -> str:
    """Reset a user's password to a cryptographically random temporary value.

    Returns the plain-text temporary password — the caller MUST communicate
    it to the user through a secure channel.
    """
    temp_password = secrets.token_urlsafe(16)
    user.password_hash = hash_password(temp_password)
    user.password_algo = "argon2id"
    return temp_password
