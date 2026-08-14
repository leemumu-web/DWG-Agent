"""经 projects/interface.py 共享的项目级访问规则。

对每个跨模块消费方（files/jobs/workflows）的调用契约：

- 全局旁路：admin/super_admin 通过所有检查，助手对成员行返回 ``None``——
  ``None`` 表示「已授权但无成员行」，不是「非成员」。
- ``require_active_project`` 对不存在或已软删除的项目抛 404，且不泄露
  项目是否存在；成员检查失败抛 403。
- ``require_project_role`` 当成员角色不在 ``allowed_project_roles`` 中时
  抛 403；它同时要求项目先处于活跃状态。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.identity.interface import User, is_admin
from app.modules.projects.models.project import Project, ProjectMember
from app.platform.http.exceptions import forbidden, not_found


def has_global_project_access(user: User) -> bool:
    """用户是否绕过项目级规则（admin/super_admin）。"""
    return is_admin(user)


def get_project_membership(db: Session, user: User, project_id: int) -> ProjectMember | None:
    """返回用户在项目中的成员行，或 None。"""
    return db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
        )
    )


def require_active_project(db: Session, project_id: int) -> None:
    """项目不存在或已软删除时抛 404。"""
    project = db.get(Project, project_id)
    if not project or project.status == "deleted":
        raise not_found("Project")


def require_project_member(db: Session, user: User, project_id: int) -> ProjectMember | None:
    """要求成员身份（或全局 admin）；返回成员行。

    admin/super_admin 返回 ``None``（无成员行但已授权）；项目缺失/已软删除
    → 404；非成员 → 403。
    """
    if has_global_project_access(user):
        return None
    require_active_project(db, project_id)
    member = get_project_membership(db, user, project_id)
    if not member:
        raise forbidden("Project membership is required.")
    return member


def require_project_role(
    db: Session,
    user: User,
    project_id: int,
    allowed_project_roles: set[str],
) -> ProjectMember | None:
    """要求允许的项目角色（或全局 admin）。

    admin/super_admin 返回 ``None``；成员 ``project_role`` 必须在
    ``allowed_project_roles`` 中，否则 403。
    """
    if has_global_project_access(user):
        return None
    member = require_project_member(db, user, project_id)
    if member and member.project_role in allowed_project_roles:
        return member
    raise forbidden("Project role is not allowed for this action.")
