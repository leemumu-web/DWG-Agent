from __future__ import annotations

from sqlalchemy import select

from app.core.config import settings
from app.core.constants import (
    ROLE_ADMIN,
    ROLE_AUDITOR,
    ROLE_ENGINEER,
    ROLE_OPERATOR,
    ROLE_REVIEWER,
    ROLE_SUPER_ADMIN,
    ROLE_VIEWER,
)
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models import Permission, Role, User

ROLE_SEEDS = [
    (ROLE_SUPER_ADMIN, "超级管理员"),
    (ROLE_ADMIN, "系统管理员"),
    (ROLE_ENGINEER, "工程师"),
    (ROLE_REVIEWER, "复核员"),
    (ROLE_OPERATOR, "操作员"),
    (ROLE_VIEWER, "只读用户"),
    (ROLE_AUDITOR, "审计员"),
]

PERMISSION_SEEDS = [
    ("users:read", "users", "read", "查看用户"),
    ("users:write", "users", "write", "管理用户"),
    ("roles:write", "roles", "write", "管理角色"),
    ("projects:write", "projects", "write", "管理项目"),
    ("files:write", "files", "write", "上传/删除文件"),
    ("jobs:write", "jobs", "write", "创建/管理任务"),
    ("reviews:write", "reviews", "write", "提交复核"),
    ("audit_logs:read", "audit_logs", "read", "查看审计日志"),
]


def init_db() -> None:
    """Seed roles, permissions, and super-admin user.

    Table creation is owned by Alembic migrations (run ``alembic upgrade head``
    first).  This function only writes seed rows — it no longer calls
    ``Base.metadata.create_all()`` to avoid a dual-schema-authority risk
    where SQLAlchemy-created tables lack an ``alembic_version`` entry and
    block subsequent migrations.
    """
    db = SessionLocal()
    try:
        for code, name in ROLE_SEEDS:
            role = db.scalar(select(Role).where(Role.code == code))
            if not role:
                role = Role(code=code, name=name, is_system=True)
                db.add(role)
        db.flush()

        for code, resource, action, name in PERMISSION_SEEDS:
            permission = db.scalar(select(Permission).where(Permission.code == code))
            if not permission:
                db.add(Permission(code=code, resource=resource, action=action, name=name))
        db.flush()

        super_role = db.scalar(select(Role).where(Role.code == ROLE_SUPER_ADMIN))
        permissions = list(db.scalars(select(Permission)).all())
        if super_role:
            super_role.permissions = permissions

        admin = db.scalar(select(User).where(User.username == settings.super_admin_username))
        if not admin:
            admin = User(
                username=settings.super_admin_username,
                real_name=settings.super_admin_real_name,
                password_hash=hash_password(settings.super_admin_password),
                password_algo="argon2id",
                status="active",
            )
            if super_role:
                admin.roles.append(super_role)
            db.add(admin)
        db.commit()
        print("Database initialized.")
        print(f"Super admin username: {settings.super_admin_username}")
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
