"""Public project-catalog boundary for other business modules."""

from app.modules.projects.access import (
    get_project_membership,
    has_global_project_access,
    require_active_project,
    require_project_member,
    require_project_role,
)
from app.modules.projects.models import Drawing, DrawingVersion, Project, ProjectMember
from app.modules.projects.schemas.project import (
    ProjectCreate,
    ProjectMemberCreate,
    ProjectMemberUpdate,
)
from app.modules.projects.services.projects import (
    add_project_member,
    create_project,
    remove_project_member,
    require_project_member_or_404,
    update_project_member,
)

__all__ = [
    "Drawing",
    "DrawingVersion",
    "Project",
    "ProjectMember",
    "ProjectCreate",
    "ProjectMemberCreate",
    "ProjectMemberUpdate",
    "add_project_member",
    "create_project",
    "get_project_membership",
    "has_global_project_access",
    "remove_project_member",
    "require_active_project",
    "require_project_member",
    "require_project_member_or_404",
    "require_project_role",
    "update_project_member",
]
