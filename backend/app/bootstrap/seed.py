"""Idempotent application seed composition for identity-owned rows."""

from __future__ import annotations

from sqlalchemy import select

from app.modules.identity.interface import Permission, Role, User
from app.platform.config.constants import (
    ACTIVE,
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_SUPER_ADMIN,
    ROLE_VIEWER,
)
from app.platform.config.settings import settings
from app.platform.database.session import SessionLocal
from app.platform.security.tokens import hash_password

ROLE_SEEDS = [
    (ROLE_SUPER_ADMIN, "超级管理员", "全部管理权限：用户、角色、系统配置、审计日志、生产流程"),
    (ROLE_ADMIN, "管理员", "全部管理权限：用户、角色、系统配置、审计日志、生产流程"),
    (ROLE_OPERATOR, "操作员", "生产操作：工作流、文件、任务、复核、余料读写；不可管理用户和角色"),
    (ROLE_VIEWER, "只读用户", "只读查看已授权的项目、流程和结果；不可执行生产或管理操作"),
]

# Three-tier permission model matching the three effective access levels:
#   admin    — super_admin & admin: full platform control
#   operator — operator: production operations (workflows/files/jobs/reviews/remnants)
#   viewer   — viewer: read-only access to authorized project and workflow data
PERMISSION_SEEDS = [
    ("admin", "admin", "write", "全部管理权限"),
    ("operator", "operator", "write", "生产操作权限"),
    ("viewer", "viewer", "read", "只读查看权限"),
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
        for code, name, description in ROLE_SEEDS:
            role = db.scalar(select(Role).where(Role.code == code))
            if not role:
                role = Role(code=code, name=name, description=description, is_system=True)
                db.add(role)
        db.flush()

        for code, resource, action, name in PERMISSION_SEEDS:
            permission = db.scalar(select(Permission).where(Permission.code == code))
            if not permission:
                db.add(Permission(code=code, resource=resource, action=action, name=name))
        db.flush()

        all_permissions = list(db.scalars(select(Permission)).all())

        # super_admin 与 admin 权限完全相同 —— 全部管理权限
        super_role = db.scalar(select(Role).where(Role.code == ROLE_SUPER_ADMIN))
        if super_role:
            super_role.permissions = all_permissions

        admin_role = db.scalar(select(Role).where(Role.code == ROLE_ADMIN))
        if admin_role:
            admin_role.permissions = all_permissions

        # operator: 生产操作权限（不含用户/角色管理）
        operator_role = db.scalar(select(Role).where(Role.code == ROLE_OPERATOR))
        if operator_role:
            operator_role.permissions = [
                p for p in all_permissions if p.code == "operator"
            ]

        # viewer: 只读查看权限
        viewer_role = db.scalar(select(Role).where(Role.code == ROLE_VIEWER))
        if viewer_role:
            viewer_role.permissions = [
                p for p in all_permissions if p.code == "viewer"
            ]

        admin = db.scalar(select(User).where(User.username == settings.super_admin_username))
        if not admin:
            admin = User(
                username=settings.super_admin_username,
                real_name=settings.super_admin_real_name,
                password_hash=hash_password(settings.super_admin_password),
                password_algo="argon2id",
                status=ACTIVE,
            )
            db.add(admin)
        # The configured account is the sole recoverable privilege root.  Keep
        # it active and repair the protected role idempotently on every start.
        admin.status = ACTIVE
        admin.deleted_at = None
        if super_role and super_role not in admin.roles:
            admin.roles.append(super_role)
        db.flush()

        # Historical databases may predate the singleton rule.  Preserve any
        # additional accounts and their management ability, but normalize them
        # to admin so exactly one user retains the protected super_admin role.
        if super_role and admin_role:
            for legacy_super_admin in list(super_role.users):
                if legacy_super_admin.id == admin.id:
                    continue
                legacy_super_admin.roles.remove(super_role)
                if admin_role not in legacy_super_admin.roles:
                    legacy_super_admin.roles.append(admin_role)
        db.commit()
        print("Database initialized.")
        print(f"Super admin username: {settings.super_admin_username}")
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
