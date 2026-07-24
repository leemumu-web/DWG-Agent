"""DB-level project fixtures — replaces the removed /api/v1/workflows/projects HTTP endpoint.

All project creation now uses direct service-layer calls since the /projects
HTTP CRUD endpoint has been removed (functionality merged into /workflows).
"""

from __future__ import annotations

from uuid import uuid4

from app.modules.projects.interface import (
    ProjectCreate,
    ProjectMemberCreate,
)
from app.modules.projects.interface import (
    add_project_member as _add_project_member,
)
from app.modules.projects.interface import (
    create_project as _create_project,
)
from tests.support.database import open_test_session


def create_project_via_db(owner_id: int, *, code_prefix: str = "TEST") -> int:
    """Create a project directly in the DB and return its id."""
    code = f"{code_prefix}-{uuid4().hex[:8]}"
    with open_test_session() as db:
        project = _create_project(
            db,
            ProjectCreate(code=code, name=f"Test {code}", description="auto-generated"),
            owner_id=owner_id,
        )
        db.commit()
        return project.id


def add_member_via_db(project_id: int, user_id: int, role: str) -> None:
    """Add a member to a project directly in the DB."""
    with open_test_session() as db:
        _add_project_member(
            db,
            project_id,
            ProjectMemberCreate(user_id=user_id, project_role=role),
        )
        db.commit()


def delete_project_via_db(project_id: int) -> None:
    """Soft-delete a project directly in the DB."""
    from app.modules.projects.models.project import Project

    with open_test_session() as db:
        project = db.get(Project, project_id)
        if project:
            project.status = "deleted"
            db.commit()
